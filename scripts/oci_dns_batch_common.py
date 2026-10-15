"""Shared validation, TSIG-aware updates, backups, and results for the DNS scripts."""
import argparse
import csv
import hashlib
import ipaddress
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import oci
from oci_dns_config import load_options, sdk_config, PacedDnsClient


def ask(value, label):
    if value:
        return value
    if not sys.stdin.isatty():
        raise ValueError(f"Missing {label}; supply its command option")
    value = input(f"{label}: ").strip()
    if not value:
        raise ValueError(f"{label} is required")
    return value


def master_dict(master):
    result = {"address": master.address}
    if master.port is not None:
        result["port"] = master.port
    if master.tsig_key_id is not None:
        result["tsigKeyId"] = master.tsig_key_id
    return result


def fingerprint(masters):
    return sorted((str(ipaddress.ip_address(m.address)), m.port or 53,
                   m.tsig_key_id or "") for m in masters)


def desired_masters(before, old_ip, new_ip, mode):
    """Plan one zone without mutation; retain unrelated entries and per-zone TSIG references."""
    if mode not in {"add", "remove"}:
        raise ValueError("Unknown migration action")
    old = [m for m in before if str(ipaddress.ip_address(m.address)) == old_ip]
    new = [m for m in before if str(ipaddress.ip_address(m.address)) == new_ip]
    if any((m.port or 53) != 53 for m in before):
        raise ValueError("OCI external masters support port 53 only")
    if len(old) > 1 or len(new) > 1:
        raise ValueError("Duplicate source or target upstream entries")
    if mode == "add":
        if not old or not old[0].tsig_key_id:
            raise ValueError("Source upstream must be present with a TSIG key")
        if new:
            if new[0].tsig_key_id != old[0].tsig_key_id:
                raise ValueError("Target upstream does not use source upstream's per-zone TSIG key")
            return list(before), False
        return list(before) + [oci.dns.models.ExternalMaster(
            address=new_ip, port=old[0].port, tsig_key_id=old[0].tsig_key_id)], True
    if old and new and old[0].tsig_key_id != new[0].tsig_key_id:
        raise ValueError("Target upstream must use the same per-zone TSIG key as Source upstream")
    if not new or not new[0].tsig_key_id:
        raise ValueError("Target upstream must already be present with TSIG before removing Source upstream")
    if not old:
        return list(before), False
    after = [m for m in before if str(ipaddress.ip_address(m.address)) != old_ip]
    if not after:
        raise ValueError("Refusing to remove the last upstream")
    return after, True


def selected_ids(path):
    ids = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
           if line.strip() and not line.lstrip().startswith("#")]
    if not ids or any(not item.startswith("ocid1.dns-zone.") for item in ids):
        raise ValueError("Zone file must contain DNS zone OCIDs, one per line")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate OCIDs; review the zone file")
    return ids


def secure_file(path):
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8", newline="")


def emit(stream, record):
    stream.write(json.dumps(record, sort_keys=True) + "\n")
    stream.flush()


def run_batch(mode):
    parser = argparse.ArgumentParser(description=(
        "Add the target upstream with each zone's existing TSIG key" if mode == "add" else
        "Remove the source only where a signed target upstream is present"))
    parser.add_argument("--config", type=Path, help="Non-secret migration JSON file")
    parser.add_argument("--compartment-id")
    parser.add_argument("--zone-ids-file", type=Path)
    parser.add_argument("--source-ip", "--old-ip", dest="old_ip", help="Existing upstream IPv4/IPv6 address")
    parser.add_argument("--target-ip", "--new-ip", dest="new_ip", help="Replacement upstream IPv4/IPv6 address")
    parser.add_argument("--profile")
    parser.add_argument("--region", help="DNS management region; default is selected OCI profile")
    parser.add_argument("--expected-count", type=int)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--api-delay", type=float, help="Seconds before each API call; default 0.6")
    parser.add_argument("--apply", action="store_true", help="Apply; default is read-only preview")
    args = parser.parse_args()
    try:
        load_options(args, {"profile": "DEFAULT", "output_dir": Path("local/results"), "api_delay": 0.6})
        args.compartment_id = ask(args.compartment_id, "Zone compartment OCID")
        args.zone_ids_file = Path(ask(args.zone_ids_file, "Reviewed zone OCID text file"))
        old_ip = str(ipaddress.ip_address(ask(args.old_ip, "Existing upstream IP")))
        new_ip = str(ipaddress.ip_address(ask(args.new_ip, "Replacement upstream IP")))
        if "%" in old_ip + new_ip:
            raise ValueError("IPv6 interface scope identifiers are not OCI upstream addresses")
        if old_ip == new_ip:
            raise ValueError("Source and target IPs must differ")
        ids = selected_ids(args.zone_ids_file)
        if args.expected_count is not None and len(ids) != args.expected_count:
            raise ValueError(f"Expected {args.expected_count} zones, found {len(ids)}")
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    config = sdk_config(args)
    client = PacedDnsClient(config, args.api_delay)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    base = args.output_dir / f"{mode}-{'apply' if args.apply else 'preview'}-{stamp}"
    backup_path, results_path, csv_path = [Path(f"{base}-{s}") for s in
                                          ("before.jsonl", "results.jsonl", "summary.csv")]
    print(f"Phase: {mode}; zones: {len(ids)}; apply: {args.apply}", flush=True)
    print(f"Selection: {args.zone_ids_file.resolve()}", flush=True)
    print(f"Backup: {backup_path}\nResults: {results_path}\nSummary: {csv_path}", flush=True)
    selection_hash = hashlib.sha256(args.zone_ids_file.read_bytes()).hexdigest()
    prepared, rows = [], []
    failed = False
    with secure_file(backup_path) as backup, secure_file(results_path) as results:
        # Validate every selected zone before changing any zone.
        try:
            for zone_id in ids:
                response = client.get_zone(zone_id, scope="GLOBAL")
                zone = response.data
                if (zone.compartment_id != args.compartment_id or zone.zone_type != "SECONDARY"
                        or zone.scope != "GLOBAL" or zone.lifecycle_state != "ACTIVE"):
                    raise ValueError(f"{zone.name}: expected ACTIVE GLOBAL SECONDARY in requested compartment")
                if not response.headers.get("etag"):
                    raise ValueError(f"{zone.name}: missing ETag")
                before = list(zone.external_masters or [])
                after, changed = desired_masters(before, old_ip, new_ip, mode)
                prepared.append((zone, response.headers["etag"], before, after, changed))
            for zone, etag, before, _, _ in prepared:
                emit(backup, {"zoneId": zone.id, "zoneName": zone.name,
                              "compartmentId": zone.compartment_id, "etag": etag,
                              "selectionSha256": selection_hash,
                              "externalMasters": [master_dict(m) for m in before]})
        except Exception as exc:
            emit(results, {"status": "preflight-error", "error": str(exc)})
            print(f"Preflight failed; no zones changed: {exc}", file=sys.stderr)
            return 1
        for zone, etag, before, after, changed in prepared:
            before_dicts = [master_dict(m) for m in before]
            after_dicts = [master_dict(m) for m in after]
            status = "unchanged" if not changed else "preview"
            try:
                if args.apply and changed:
                    fresh = client.get_zone(zone.id, scope="GLOBAL")
                    if (fresh.data.lifecycle_state != "ACTIVE" or
                            fingerprint(fresh.data.external_masters or []) != fingerprint(before)):
                        raise ValueError("Upstream settings changed after preflight; rerun the preview")
                    if not fresh.headers.get("etag"):
                        raise ValueError("Missing fresh ETag; refusing an unconditional update")
                    client.update_zone(zone.id, oci.dns.models.UpdateZoneDetails(
                        external_masters=after), if_match=fresh.headers["etag"], scope="GLOBAL")
                    deadline = time.monotonic() + 120
                    while True:
                        actual = client.get_zone(zone.id, scope="GLOBAL").data
                        if actual.lifecycle_state == "ACTIVE":
                            if fingerprint(actual.external_masters or []) != fingerprint(after):
                                raise ValueError("Read-back upstream list differs from the requested list")
                            after_dicts = [master_dict(m) for m in actual.external_masters]
                            break
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Zone did not become ACTIVE within 120 seconds")
                        time.sleep(2)
                    status = "updated"
                emit(results, {"zoneId": zone.id, "zoneName": zone.name, "status": status,
                               "beforeExternalMasters": before_dicts, "afterExternalMasters": after_dicts})
                row = {"zoneName": zone.name, "zoneId": zone.id, "status": status,
                       "beforeIPs": ";".join(m["address"] for m in before_dicts),
                       "afterIPs": ";".join(m["address"] for m in after_dicts),
                       "tsigKeyIds": ";".join(m.get("tsigKeyId", "") for m in after_dicts)}
                rows.append(row)
                print(f"{status:10} {zone.name}: {row['beforeIPs']} -> {row['afterIPs']}", flush=True)
            except Exception as exc:
                emit(results, {"zoneId": zone.id, "zoneName": zone.name,
                               "status": "error", "error": str(exc)})
                rows.append({"zoneName": zone.name, "zoneId": zone.id, "status": "error",
                             "beforeIPs": ";".join(m["address"] for m in before_dicts),
                             "afterIPs": "Inspect OCI and the results log", "tsigKeyIds": ""})
                print(f"Stopped at {zone.name}: {exc}", file=sys.stderr)
                failed = True
                break
    with secure_file(csv_path) as summary:
        writer = csv.DictWriter(summary, fieldnames=(
            "zoneName", "zoneId", "status", "beforeIPs", "afterIPs", "tsigKeyIds"))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Completed {len(rows)}/{len(ids)} zones; errors: {int(failed)}", flush=True)
    print("Verify SOA/TXT and transfer logs before proceeding to the next phase.")
    return int(failed)
