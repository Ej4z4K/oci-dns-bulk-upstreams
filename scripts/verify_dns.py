#!/usr/bin/env python3
"""Check selected zone upstreams, SOA and TXT at every assigned OCI nameserver."""
import argparse
import concurrent.futures
import ipaddress
import json
import socket
import sys
from pathlib import Path
import dns.flags
import dns.message
import dns.name
import dns.query
import dns.rdatatype
import oci
from oci_dns_batch_common import selected_ids, secure_file


def query(server, name, rdtype):
    addresses = socket.getaddrinfo(server, 53, type=socket.SOCK_DGRAM)
    # Keep existing IPv4 operator deployments working; IPv6-only nameservers also work.
    addresses.sort(key=lambda item: item[0] != socket.AF_INET)
    address = addresses[0][4][0]
    request = dns.message.make_query(name, rdtype)
    request.flags &= ~dns.flags.RD
    reply = dns.query.udp(request, address, timeout=8)
    if reply.flags & dns.flags.TC:
        reply = dns.query.tcp(request, address, timeout=8)
    if reply.rcode() != 0 or not reply.flags & dns.flags.AA:
        raise ValueError('Expected NOERROR and an authoritative answer')
    answers = [rr for rrset in reply.answer if rrset.rdtype == rdtype for rr in rrset]
    if not answers:
        raise ValueError('No answer for the requested type')
    return answers


def marker_name(zone_name, record_name='probe'):
    """Resolve a relative name or {zone} template, rejecting records outside the selected zone."""
    origin = dns.name.from_text(zone_name)
    value = record_name.replace('{zone}', zone_name.rstrip('.'))
    if '{' in value or '}' in value:
        raise ValueError('Only the {zone} placeholder is supported')
    name = origin if value == '@' else dns.name.from_text(value, origin=origin)
    if not name.is_subdomain(origin):
        raise ValueError('Verification record must be inside the selected zone')
    return name.to_text()


def check(zone_name, server, marker, serial, record_name='probe'):
    try:
        soa = query(server, zone_name, dns.rdatatype.SOA)[0].serial
        values = [b''.join(rr.strings).decode('utf-8') for rr in
                  query(server, marker_name(zone_name, record_name), dns.rdatatype.TXT)]
        passed = marker in values and (serial is None or serial == soa)
        return {'zone': zone_name, 'server': server, 'soaSerial': soa,
                'txt': values, 'status': 'pass' if passed else 'fail'}
    except Exception as exc:
        return {'zone': zone_name, 'server': server, 'status': 'fail', 'error': str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record-name', default='probe')
    parser.add_argument('--zone-ids-file', type=Path, required=True)
    parser.add_argument('--compartment-id', required=True)
    parser.add_argument('--expected-ips', required=True, help='Comma-separated complete upstream IP set')
    parser.add_argument('--marker', required=True)
    parser.add_argument('--serial', type=int)
    parser.add_argument('--profile', default='DEFAULT')
    parser.add_argument('--region')
    parser.add_argument('--output', type=Path, help='New results JSON filename')
    args = parser.parse_args()
    expected = {str(ipaddress.ip_address(ip.strip())) for ip in args.expected_ips.split(',')}
    config = oci.config.from_file(profile_name=args.profile)
    if args.region:
        config['region'] = args.region
    client = oci.dns.DnsClient(config, timeout=(10, 60))
    records, jobs = [], []
    for zone_id in selected_ids(args.zone_ids_file):
        zone = client.get_zone(zone_id, scope='GLOBAL').data
        masters = zone.external_masters or []
        actual = {str(ipaddress.ip_address(m.address)) for m in masters}
        if (zone.compartment_id != args.compartment_id or zone.zone_type != 'SECONDARY'
                or zone.scope != 'GLOBAL' or zone.lifecycle_state != 'ACTIVE'
                or actual != expected or any(not m.tsig_key_id for m in masters)
                or not zone.nameservers):
            records.append({'zone': zone.name, 'status': 'fail', 'error': 'Zone/upstream/TSIG precheck failed'})
            continue
        jobs.extend((zone.name, server.hostname) for server in zone.nameservers)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(check, zone, server, args.marker, args.serial, args.record_name) for zone, server in jobs]
        records.extend(future.result() for future in futures)
    for record in records:
        print(json.dumps(record, sort_keys=True))
    passed = sum(row['status'] == 'pass' for row in records)
    print(f'Passed {passed}/{len(records)} checks')
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with secure_file(args.output) as stream:
            json.dump(records, stream, indent=2)
    return int(passed != len(records) or not records)


if __name__ == '__main__':
    sys.exit(main())
