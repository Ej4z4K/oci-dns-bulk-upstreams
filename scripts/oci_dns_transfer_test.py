#!/usr/bin/env python3
"""Fresh-transfer lab tests; OCI upstream configuration is read-only throughout."""
import argparse
import concurrent.futures
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
import dns.rdatatype
import dns.zone
import oci
from oci_dns_batch_common import fingerprint, selected_ids, secure_file
from verify_dns import check, query


from oci_dns_transfer_evidence import completed_transfers


def axfr_data(record):
    zone = dns.zone.from_text(record['axfr'], origin=record['zone'], relativize=False,
                              check_origin=False)
    soa = zone.get_rdataset(record['zone'] + '.', dns.rdatatype.SOA)
    txt = zone.get_rdataset('probe.' + record['zone'] + '.', dns.rdatatype.TXT)
    if not soa or not txt:
        raise ValueError('Signed AXFR lacks SOA/probe: ' + record['zone'])
    return soa[0].serial, [b''.join(rr.strings).decode() for rr in txt]


def validate_zone(zone, args, mode):
    name = zone.name.rstrip('.')
    if (not name.endswith('.test') or not name.startswith(args.lab_prefix)
            or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,250}', name)
            or '..' in name):
        raise ValueError('Only selected lab-prefix .test zones are supported: ' + name)
    expected = {args.old_ip, args.new_ip} if mode == 'both' else {args.new_ip}
    masters = zone.external_masters or []
    actual = [str(ipaddress.ip_address(m.address)) for m in masters]
    if (zone.compartment_id != args.compartment_id or zone.scope != 'GLOBAL'
            or zone.zone_type != 'SECONDARY' or zone.lifecycle_state != 'ACTIVE'
            or set(actual) != expected or len(actual) != len(expected)
            or any(not m.tsig_key_id or (m.port or 53) != 53 for m in masters)
            or len({m.tsig_key_id for m in masters}) != 1 or not zone.nameservers):
        raise ValueError('Zone state/upstream/port/TSIG preflight failed: ' + name)
    return {'zone': name, 'zoneId': zone.id, 'keyName': args.key_name_template.format(zone=name),
            'masters': fingerprint(masters), 'nameservers': [ns.hostname for ns in zone.nameservers]}


def ssh(args, host, operation, **kwargs):
    helper = Path(__file__).with_name('oci_dns_transfer_remote.py').read_text()
    request = {'operation': operation, **kwargs}
    # Send JSON as a Python string literal via stdin. No remote shell interpolation or secrets in argv.
    source = helper + '\ntry:\n    print(json.dumps(dispatch(json.loads(' + repr(json.dumps(request)) + "))))\nexcept Exception as exc:\n    print(json.dumps({'error': str(exc)}))\n    raise SystemExit(1)\n"
    argv = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10']
    if args.ssh_key:
        argv.extend(['-i', str(args.ssh_key.expanduser())])
    argv += [f'{args.ssh_user}@{host}', 'sudo -n /usr/bin/python3 -']
    result = subprocess.run(argv, input=source, text=True, capture_output=True, timeout=240)
    try:
        value = json.loads(result.stdout)
    except ValueError:
        raise RuntimeError('SSH/helper failed; check SSH, sudo, dig and journal access: ' + result.stderr[-1000:])
    if result.returncode or 'error' in value:
        raise RuntimeError(value.get('error', 'SSH helper failed'))
    return value


def parser_for(mode):
    parser = argparse.ArgumentParser(description=(
        'Prove fresh OCI transfers from B while A+B remain configured' if mode == 'both' else
        'Prove fresh OCI transfers with B as the only configured upstream'))
    parser.add_argument('--zone-ids-file', type=Path, required=True)
    parser.add_argument('--compartment-id', required=True)
    parser.add_argument('--old-ip', required=True, help='VM-A public upstream IP')
    parser.add_argument('--new-ip', required=True, help='VM-B public upstream IP')
    parser.add_argument('--lab-prefix', required=True, help='Required .test zone name prefix')
    parser.add_argument('--expected-count', type=int, default=10)
    parser.add_argument('--ssh-user', default='opc')
    parser.add_argument('--ssh-key', type=Path)
    parser.add_argument('--vm-a-ssh-host', help='Reachable VM-A SSH IPv4 address; defaults to --old-ip')
    parser.add_argument('--vm-b-ssh-host', help='Reachable VM-B SSH IPv4 address; defaults to --new-ip')
    parser.add_argument('--bind-key-file', default='/etc/named/oci-lab.keys')
    parser.add_argument('--key-name-template', default='tsig.{zone}.')
    parser.add_argument('--transfer-ips', required=True, help='Comma-separated OCI transfer destination IPs')
    parser.add_argument('--profile', default='DEFAULT')
    parser.add_argument('--region', default='uk-london-1')
    parser.add_argument('--timeout', type=int, default=300, help='Polling seconds after publishing; default 300')
    parser.add_argument('--interval', type=int, default=10)
    parser.add_argument('--output-dir', type=Path, default=Path('local/results'))
    parser.add_argument('--run', action='store_true', help='Publish a unique probe marker/increment serials on A; default preview')
    return parser


def main(mode):
    parser = parser_for(mode)
    args = parser.parse_args()
    report = {'mode': mode, 'run': args.run, 'status': 'error', 'zones': []}
    report_path = None
    try:
        args.old_ip, args.new_ip = [str(ipaddress.IPv4Address(x)) for x in (args.old_ip, args.new_ip)]
        if args.old_ip == args.new_ip or not re.fullmatch(r'[a-z0-9-]+', args.lab_prefix):
            raise ValueError('Use distinct VM IPs and a nonempty lowercase lab prefix')
        a_host = str(ipaddress.IPv4Address(args.vm_a_ssh_host or args.old_ip))
        b_host = str(ipaddress.IPv4Address(args.vm_b_ssh_host or args.new_ip))
        clients = {str(ipaddress.IPv4Address(ip.strip())) for ip in args.transfer_ips.split(',')}
        if (args.key_name_template.count('{zone}') != 1 or
                not re.fullmatch(r'[A-Za-z0-9_.{}-]+', args.key_name_template) or
                '{' in args.key_name_template.replace('{zone}', '') or
                '}' in args.key_name_template.replace('{zone}', '')):
            raise ValueError('Key name template must contain only one {zone} placeholder')
        if not re.fullmatch(r'[a-z_][a-z0-9_-]*', args.ssh_user):
            raise ValueError('Invalid SSH user')
        if not 1 <= args.expected_count <= 10 or args.timeout < 1 or args.interval < 1:
            raise ValueError('Use 1-10 lab zones and positive polling settings')
        ids = selected_ids(args.zone_ids_file)
        if len(ids) != args.expected_count:
            raise ValueError('Selection count differs from --expected-count')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        marker = 'b-' + mode + '-' + stamp.lower() + '-' + uuid.uuid4().hex[:8]
        args.output_dir.mkdir(parents=True, exist_ok=True)
        report_path = args.output_dir / f'test-vm-b-{mode}-{stamp}.json'
        report.update(marker=marker, selectionSha256=hashlib.sha256(args.zone_ids_file.read_bytes()).hexdigest(),
                      transferClientIPs=sorted(clients), reportPath=str(report_path))
        config = oci.config.from_file(profile_name=args.profile)
        config['region'] = args.region
        client = oci.dns.DnsClient(config, timeout=(10, 60))
        items = [validate_zone(client.get_zone(zid, scope='GLOBAL').data, args, mode) for zid in ids]
        report['zones'] = items
        # Fail before writes if B's signed AXFR/negative checks, A files or journal access fail.
        transfers = ssh(args, b_host, 'axfr', zones=items, keyFile=args.bind_key_file)['transfers']
        for item, transfer in zip(items, transfers):
            serial, values = axfr_data(transfer)
            item['baselineB'] = {'serial': serial, 'txt': values, 'signedAxfr': True,
                                 'unsignedRejected': True, 'wrongSecretRejected': True}
            for ns in item['nameservers']:
                if (query(ns, item['zone'], dns.rdatatype.SOA)[0].serial != serial or
                        check(item['zone'], ns, values[0], serial)['status'] != 'pass'):
                    raise ValueError('Baseline B/OCI records differ: ' + item['zone'])
        prepared = ssh(args, a_host, 'prepare', zones=items, marker=marker)['prepared']
        for item, plan in zip(items, prepared):
            if (plan['serial'] - 1) % (2 ** 32) != item['baselineB']['serial']:
                raise ValueError('A and B baseline serials differ: ' + item['zone'])
            item['expectedSerial'] = plan['serial']
        a_cursor = ssh(args, a_host, 'cursor')['cursor']
        b_cursor = ssh(args, b_host, 'cursor')['cursor']
        print(f'Preflight passed: {len(items)} zones; signed B AXFR and negative checks passed.', flush=True)
        print(f'Report: {report_path}', flush=True)
        if not args.run:
            report['status'] = 'preview'
            print('Preview only. --run changes probe TXT and increments SOA on A; OCI upstreams stay unchanged.')
            return 0
        # Recheck all upstreams immediately before publishing. Recheck again during every poll.
        for item in items:
            fresh = client.get_zone(item['zoneId'], scope='GLOBAL').data
            validate_zone(fresh, args, mode)
            if fingerprint(fresh.external_masters) != item['masters']:
                raise ValueError('Upstream/TSIG settings changed since preflight')
        report['publish'] = ssh(args, a_host, 'publish', prepared=prepared, marker=marker)
        print(f'Published fresh marker {marker}; polling B, OCI nameservers and transfer logs.', flush=True)
        deadline = time.monotonic() + args.timeout
        notified = False
        while True:
            for item in items:
                fresh = client.get_zone(item['zoneId'], scope='GLOBAL').data
                validate_zone(fresh, args, mode)
                if fingerprint(fresh.external_masters) != item['masters']:
                    raise ValueError('Upstream/TSIG settings changed during test')
            transfers = ssh(args, b_host, 'axfr', zones=items, keyFile=args.bind_key_file)['transfers']
            b_fresh = True
            for item, transfer in zip(items, transfers):
                serial, values = axfr_data(transfer)
                item['bFresh'] = serial == item['expectedSerial'] and marker in values
                b_fresh = b_fresh and item['bFresh']
            if b_fresh and not notified:
                ssh(args, b_host, 'notify', zones=items)
                notified = True
            jobs = [(item, ns) for item in items for ns in item['nameservers']]
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = [(item, pool.submit(check, item['zone'], ns, marker, item['expectedSerial']))
                           for item, ns in jobs]
                for item in items:
                    item['dnsChecks'] = []
                for item, future in futures:
                    item['dnsChecks'].append(future.result())
            a_logs = ssh(args, a_host, 'logs', cursor=a_cursor)['messages']
            b_logs = ssh(args, b_host, 'logs', cursor=b_cursor)['messages']
            for item in items:
                for label, logs in (('aTransfers', a_logs), ('bTransfers', b_logs)):
                    item[label] = completed_transfers(logs, item['zone'], item['expectedSerial'], item['keyName'], clients)
                item['dnsFresh'] = all(row['status'] == 'pass' for row in item['dnsChecks'])
                item['status'] = ('pass' if item['bFresh'] and item['dnsFresh'] and item['bTransfers'] else
                                  'b-transfer-not-observed' if item['bFresh'] and item['dnsFresh'] else 'waiting')
            counts = {key: sum(bool(item[key]) for item in items) for key in ('bFresh', 'dnsFresh', 'bTransfers')}
            print(f"B fresh {counts['bFresh']}/{len(items)}; OCI fresh {counts['dnsFresh']}/{len(items)}; "
                  f"completed OCI transfers from B {counts['bTransfers']}/{len(items)}", flush=True)
            if all(item['status'] == 'pass' for item in items):
                report['status'] = 'pass'
                return 0
            if time.monotonic() >= deadline:
                if all(item['bFresh'] and item['dnsFresh'] for item in items):
                    report['status'] = 'b-transfer-not-observed'
                    print('Fresh DNS data passed, but a completed OCI transfer from B was not observed for every zone.')
                    return 2
                report['status'] = 'fail'
                return 1
            time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
    except Exception as exc:
        report['error'] = str(exc)
        print('Test stopped: ' + str(exc), file=sys.stderr)
        return 1
    finally:
        if report_path:
            with secure_file(report_path) as stream:
                json.dump(report, stream, indent=2)
            print(f"Result: {report['status']}; report: {report_path}", flush=True)


if __name__ == '__main__':
    raise SystemExit('Run test_vm_b_with_both.py or test_vm_b_only.py')
