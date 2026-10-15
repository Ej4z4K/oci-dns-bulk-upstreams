#!/usr/bin/env python3
"""Phase 1: export selected public secondary zone OCIDs for manual review."""
import argparse
import json
from pathlib import Path
import oci
from oci_dns_batch_common import ask, master_dict, secure_file
from oci_dns_config import load_options, sdk_config, PacedDnsClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--compartment-id")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--name-prefix", help="Literal zone-name prefix; no wildcard")
    selection.add_argument("--zone-names-file", type=Path, help="Exact names, one per line, for unrelated domains")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--zone-ids-file", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--expected-count", type=int, default=None)
    parser.add_argument("--profile")
    parser.add_argument("--region")
    parser.add_argument("--api-delay", type=float)
    args = parser.parse_args()
    cli_prefix, cli_names = args.name_prefix is not None, args.zone_names_file is not None
    try:
        load_options(args, {'profile': 'DEFAULT', 'api_delay': 0.6})
        args.compartment_id = ask(args.compartment_id, 'Zone compartment OCID')
        if args.zone_names_file and args.name_prefix:
            # An explicit selection flag supersedes the other selection method in config.
            if cli_names:
                args.name_prefix = None
            elif cli_prefix:
                args.zone_names_file = None
            else:
                raise ValueError('Choose name_prefix OR zone_names_file in config')
        if not args.zone_names_file:
            args.name_prefix = ask(args.name_prefix, 'Literal zone-name prefix')
        args.output = Path(ask(args.output or getattr(args, 'zone_ids_file', None), 'New output OCID text file'))
        args.expected_count = int(ask(args.expected_count, 'Exact expected zone count'))
        if args.expected_count < 1:
            raise ValueError('Expected count must be positive')
        wanted = None
        if args.zone_names_file:
            wanted = [line.strip().rstrip('.').lower() for line in args.zone_names_file.read_text(encoding='utf-8-sig').splitlines()
                      if line.strip() and not line.lstrip().startswith('#')]
            if not wanted or len(set(wanted)) != len(wanted):
                raise ValueError('Exact-name selection must be nonempty and contain no duplicates')
        config = sdk_config(args)
        client = PacedDnsClient(config, args.api_delay)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    zones = oci.pagination.list_call_get_all_results(
        client.list_zones, args.compartment_id, zone_type="SECONDARY", scope="GLOBAL").data
    zones = sorted((z for z in zones if (z.name.rstrip('.').lower() in wanted if wanted is not None
                                        else z.name.startswith(args.name_prefix))), key=lambda z: z.name)
    if wanted is not None and {z.name.rstrip('.').lower() for z in zones} != set(wanted):
        parser.error('One or more requested names are missing or not GLOBAL SECONDARY; no file written')
    if len(zones) != args.expected_count:
        parser.error(f"Expected {args.expected_count} matching zones, found {len(zones)}; no file written")
    inventory = []
    for item in zones:
        zone = client.get_zone(item.id, scope="GLOBAL").data
        inventory.append({"zoneId": zone.id, "zoneName": zone.name,
                          "state": zone.lifecycle_state,
                          "externalMasters": [master_dict(m) for m in zone.external_masters or []]})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    inventory_path = Path(f"{args.output}.inventory.json")
    if args.output.exists() or inventory_path.exists():
        parser.error("Output already exists; review it or choose a new filename")
    with secure_file(args.output) as stream:
        stream.write("".join(z.id + "\n" for z in zones))
    with secure_file(inventory_path) as stream:
        json.dump(inventory, stream, indent=2)
        stream.write("\n")
    for zone in inventory:
        print(f"{zone['zoneName']}  {zone['zoneId']}  {zone['state']}")
    print(f"Wrote {len(zones)} IDs to {args.output}; inventory: {inventory_path}")
    print("Review the text file before separately running the add script. No settings changed.")


if __name__ == "__main__":
    main()
