#!/usr/bin/env python3
"""Read-only migration checks for any selected domain and upstream IPv4/IPv6 address.

This verifier queries the target with the OCI zone's TSIG key, then compares fresh
SOA/TXT data at all assigned OCI nameservers. Optional timestamped BIND logs add
proof that OCI actually transferred the new serial from that target. It never
changes OCI settings, upstream records, keys, files or firewall rules.
"""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import sys
import dns.query
import dns.name
import dns.rdatatype
import dns.tsigkeyring
import dns.zone
import oci
from oci_dns_batch_common import ask, fingerprint, selected_ids, secure_file
from oci_dns_config import load_options, sdk_config, PacedDnsClient
from oci_dns_transfer_evidence import completed_transfers
from verify_dns import check, marker_name


def validate_stage(zone, args, mode):
    """Require source+target before removal, or absence of source after removal."""
    masters = zone.external_masters or []
    addresses = [str(ipaddress.ip_address(m.address)) for m in masters]
    expected = {args.old_ip, args.new_ip} if mode == 'overlap' else {args.new_ip}
    actual = set(addresses)
    target = [m for m in masters if str(ipaddress.ip_address(m.address)) == args.new_ip]
    source = [m for m in masters if str(ipaddress.ip_address(m.address)) == args.old_ip]
    valid_set = (expected <= actual and (mode == 'overlap' or args.old_ip not in actual)
                 if args.allow_other_upstreams else actual == expected)
    if (zone.compartment_id != args.compartment_id or zone.scope != 'GLOBAL' or
            zone.zone_type != 'SECONDARY' or zone.lifecycle_state != 'ACTIVE' or
            not valid_set or len(addresses) != len(actual) or not zone.nameservers or
            len(target) != 1 or not target[0].tsig_key_id or
            any((m.port or 53) != 53 for m in masters) or
            (source and source[0].tsig_key_id != target[0].tsig_key_id)):
        raise ValueError('Stage/upstream/TSIG preflight failed: ' + zone.name)
    return target[0]


def signed_axfr(address, zone_name, key):
    """Read the key from OCI into memory only; never print, persist or pass its secret in argv."""
    keyring = dns.tsigkeyring.from_text({key.name: key.secret})
    stream = dns.query.xfr(address, zone_name, port=53, timeout=8, lifetime=30,
                           keyring=keyring, keyname=key.name,
                           keyalgorithm=key.algorithm.rstrip('.') + '.')
    return dns.zone.from_xfr(stream)


def target_data(zone, zone_name, record_name):
    soa = zone.get_rdataset(dns.name.from_text(zone_name), dns.rdatatype.SOA)
    txt = zone.get_rdataset(dns.name.from_text(marker_name(zone_name, record_name)), dns.rdatatype.TXT)
    if not soa or not txt:
        raise ValueError('Signed target AXFR lacks SOA or requested TXT marker')
    return soa[0].serial, [b''.join(rr.strings).decode('utf-8') for rr in txt]


def parse_since(value):
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('transfer_since needs an explicit UTC offset, for example 2026-10-12T10:00:00Z')
    return stamp.astimezone(timezone.utc)


def journal_messages(path, since):
    """Only accept structured journal records newer than the operator's transfer window."""
    messages = []
    threshold = int(since.timestamp() * 1000000)
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict) or '__REALTIME_TIMESTAMP' not in record or 'MESSAGE' not in record:
            raise ValueError('Logs must be JSONL from journalctl -o json, with timestamps and MESSAGE')
        if int(record['__REALTIME_TIMESTAMP']) >= threshold:
            messages.append(record['MESSAGE'])
    return messages


def main(mode):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--compartment-id')
    parser.add_argument('--zone-ids-file', type=Path)
    parser.add_argument('--source-ip', '--old-ip', dest='old_ip')
    parser.add_argument('--target-ip', '--new-ip', dest='new_ip')
    parser.add_argument('--expected-count', type=int)
    parser.add_argument('--profile')
    parser.add_argument('--region', help='Default: the selected OCI profile region')
    parser.add_argument('--api-delay', type=float)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--record-name', help='Relative TXT owner or {zone} template; default probe')
    parser.add_argument('--marker', help='Fresh, operator-published TXT value expected in every selected zone')
    parser.add_argument('--logs-file', type=Path, help='Optional target BIND JSONL journal export')
    parser.add_argument('--transfer-since', help='Timezone-aware timestamp captured before publishing the marker')
    parser.add_argument('--transfer-ips', help='Optional comma-separated OCI destination IPs; otherwise discover in profile region')
    parser.add_argument('--allow-other-upstreams', action=argparse.BooleanOptionalAction, default=None,
                        help='Allow unrelated upstreams while still checking the source/target migration state')
    args = parser.parse_args()
    report, path = {'mode': mode, 'status': 'error', 'zones': []}, None
    try:
        load_options(args, {'profile': 'DEFAULT', 'api_delay': 0.6,
                            'output_dir': Path('local/results'), 'record_name': 'probe',
                            'allow_other_upstreams': False})
        if type(args.allow_other_upstreams) is not bool:
            raise ValueError('allow_other_upstreams must be boolean')
        args.compartment_id = ask(args.compartment_id, 'Zone compartment OCID')
        args.zone_ids_file = Path(ask(args.zone_ids_file, 'Reviewed zone OCID file'))
        args.old_ip = str(ipaddress.ip_address(ask(args.old_ip, 'Existing upstream IP')))
        args.new_ip = str(ipaddress.ip_address(ask(args.new_ip, 'Replacement upstream IP')))
        if args.old_ip == args.new_ip or '%' in args.old_ip + args.new_ip:
            raise ValueError('Use distinct IPs without IPv6 interface scope identifiers')
        args.marker = ask(args.marker, 'Fresh expected TXT marker')
        if bool(args.logs_file) != bool(args.transfer_since):
            raise ValueError('Supply logs_file AND transfer_since together for transfer proof')
        since = parse_since(args.transfer_since) if args.transfer_since else None
        messages = journal_messages(args.logs_file, since) if args.logs_file else None
        ids = selected_ids(args.zone_ids_file)
        if args.expected_count is not None and len(ids) != args.expected_count:
            raise ValueError('Selection count differs from expected_count')
        args.output_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = args.output_dir / f'verify-target-{mode}-{stamp}.json'
        report.update(sourceIP=args.old_ip, targetIP=args.new_ip, marker=args.marker,
                      recordName=args.record_name, allowOtherUpstreams=args.allow_other_upstreams,
                      proofLevel='signed-axfr-and-dns', transferSince=since.isoformat() if since else None)
        config = sdk_config(args)
        client = PacedDnsClient(config, args.api_delay)
        prepared = []
        for zone_id in ids:
            zone = client.get_zone(zone_id, scope='GLOBAL').data
            target = validate_stage(zone, args, mode)
            marker_name(zone.name, args.record_name)
            prepared.append((zone, target.tsig_key_id, fingerprint(zone.external_masters)))
        client_ips = set()
        if messages is not None:
            if args.transfer_ips:
                client_ips = {str(ipaddress.ip_address(x.strip())) for x in args.transfer_ips.split(',')}
            else:
                servers = oci.pagination.list_call_get_all_results(
                    client.list_zone_transfer_servers, config['tenancy']).data
                client_ips = {str(ipaddress.ip_address(s.address)) for s in servers if s.is_transfer_destination}
            if not client_ips:
                raise ValueError('No OCI transfer destination IPs available for matching logs')
            report.update(proofLevel='signed-axfr-dns-and-oci-transfer-log', transferClientIPs=sorted(client_ips))
        keys, data_failed, missing_evidence = {}, False, False
        for zone, key_id, before in prepared:
            record = {'zone': zone.name, 'zoneId': zone.id, 'tsigKeyId': key_id, 'status': 'error'}
            report['zones'].append(record)
            try:
                if key_id not in keys:
                    keys[key_id] = client.get_tsig_key(key_id).data
                key = keys[key_id]
                transferred = signed_axfr(args.new_ip, zone.name, key)
                serial, values = target_data(transferred, zone.name, args.record_name)
                record.update(targetSerial=serial, targetMarkerValues=values, signedAxfr=True, keyName=key.name)
                if args.marker not in values:
                    raise ValueError('Target does not serve the fresh expected marker')
                record['dnsChecks'] = [check(zone.name, ns.hostname, args.marker, serial, args.record_name)
                                       for ns in zone.nameservers]
                if not all(row['status'] == 'pass' for row in record['dnsChecks']):
                    raise ValueError('One or more OCI nameservers do not match target serial/marker')
                fresh = client.get_zone(zone.id, scope='GLOBAL').data
                validate_stage(fresh, args, mode)
                if fingerprint(fresh.external_masters) != before:
                    raise ValueError('Upstream/key settings changed during verification')
                record['ociTargetTransferObserved'] = None
                if messages is not None:
                    record['targetTransfers'] = completed_transfers(messages, zone.name, serial, key.name, client_ips)
                    record['ociTargetTransferObserved'] = bool(record['targetTransfers'])
                    if not record['targetTransfers']:
                        missing_evidence = True
                        record['status'] = 'target-transfer-not-observed'
                    else:
                        record['status'] = 'data-and-transfer-pass'
                else:
                    record['status'] = 'capability-and-data-pass'
            except Exception as exc:
                data_failed = True
                record['error'] = str(exc)
            print(record['status'], zone.name, flush=True)
        report['status'] = ('fail' if data_failed else 'target-transfer-not-observed' if missing_evidence else
                            'data-and-transfer-pass' if messages is not None else 'capability-and-data-pass')
        if messages is None:
            print('Signed target AXFR and DNS answers are checked; actual OCI target usage was not observed.')
        return 1 if data_failed else 2 if missing_evidence else 0
    except Exception as exc:
        report['error'] = str(exc)
        print('Verification stopped: ' + str(exc), file=sys.stderr)
        return 1
    finally:
        if path:
            with secure_file(path) as stream:
                json.dump(report, stream, indent=2)
            print(f"Result: {report['status']}; report: {path}", flush=True)


if __name__ == '__main__':
    raise SystemExit('Use phase2_verify_upstream.py or phase3_verify_target.py')
