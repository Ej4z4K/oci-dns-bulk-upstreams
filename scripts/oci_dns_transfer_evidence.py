"""Identify completed outgoing signed OCI transfers in BIND log messages."""
import ipaddress
import re


def completed_transfers(messages, zone, serial, key_name, client_ips):
    """Require a completed outgoing transfer, the new serial, zone/key and an OCI client."""
    started, matches = {}, []
    for message in messages:
        if not isinstance(message, str) or f"transfer of '{zone}/IN'" not in message:
            continue
        prefix = message.split("transfer of '", 1)[0]
        if not re.search(r'/key\s+' + re.escape(key_name.rstrip('.')) + r'\.?(?=[:\s])', prefix):
            continue
        # Normalize IPv6 compression rather than matching the input's literal spelling.
        seen_clients = re.findall(r'(?:^|\s)([0-9A-Fa-f:.]+)#\d+', prefix)
        try:
            if not {str(ipaddress.ip_address(ip)) for ip in seen_clients} & {
                    str(ipaddress.ip_address(ip)) for ip in client_ips}:
                continue
        except ValueError:
            continue
        serials = re.findall(r'\(serial\s+(\d+)(?:\s*->\s*(\d+))?\)', message)
        stated = int(serials[-1][1] or serials[-1][0]) if serials else None
        if re.search(r'\b(?:AXFR|IXFR) started\b', message):
            started[prefix] = stated
        if re.search(r'\b(?:AXFR|IXFR) ended\b', message):
            effective = stated if stated is not None else started.get(prefix)
            if effective == serial:
                matches.append(message)
            started.pop(prefix, None)
    return matches
