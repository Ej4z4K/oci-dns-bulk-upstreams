# Test the phase scripts from a Mac terminal

Use one Terminal session for these commands. The operator runs Python locally; upstream record management stays with the DNS administrator. Start with one controlled selected zone and then increase the reviewed count.

## 1. Open the package and activate Python

```bash
cd /path/to/oci-dns-bulk-upstreams
python3.11 --version
```

If the virtual environment already exists:

```bash
source .venv/bin/activate
```

For a first installation:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Check dependencies and run the offline tests:

```bash
python -c 'import oci, dns; print("OCI SDK", oci.__version__, "dnspython", dns.__version__)'
python -m unittest discover -s tests -v
```

Expected: 42 tests and `OK`. These tests do not connect to OCI or DNS servers. Do not run operator scripts with `sudo`.

## 2. Create a new private configuration

```bash
umask 077
mkdir -p local
RUN_TAG=$(date -u +%Y%m%dT%H%M%SZ)
CONFIG="local/migration-${RUN_TAG}.json"
cp examples/migration.example.json "$CONFIG"
```

Give each run new OCID/report paths while retaining the input template:

```bash
python - "$CONFIG" "$RUN_TAG" <<'PY'
import json
from pathlib import Path
import sys
path = Path(sys.argv[1])
settings = json.loads(path.read_text())
settings['zone_ids_file'] = 'zone-ids-' + sys.argv[2] + '.txt'
settings['output_dir'] = 'results/' + sys.argv[2]
path.write_text(json.dumps(settings, indent=2) + '\n')
PY
nano "$CONFIG"
```

In the editor set your compartment, API profile, management region, source IP, target IP, exact zone selection/count and TXT record owner. Save with Control+O, Enter, then exit with Control+X. Documentation addresses/placeholders must be replaced.

For unrelated zone names, replace `name_prefix` with `zone_names_file` and create that UTF-8 file with exact names, one per line. JSON paths are relative to the configuration file, so `zone-names.txt` means `local/zone-names.txt` here.

Use an existing authenticated profile, or configure one with `oci setup config` if CLI is installed. Register its API public key and obtain zone/key permissions as described in [authentication](CLI_AUTH.md). The Python scripts use the same profile.

## 3. Check the target connection before verification

Read the target from your edited configuration and test TCP 53:

```bash
TARGET_IP=$(python - "$CONFIG" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))['target_ip'])
PY
)
nc -vz -G 5 "$TARGET_IP" 53
```

The generic verifier requests signed AXFR **from this Mac directly to the target**. Permit the Mac's actual outbound IPv4/32 or IPv6/128 address in the target's network and host-firewall rules. The server must also accept the matching per-zone TSIG key. A successful TCP connection checks transport; the verifier checks authentication and zone contents.

OCI transfer destination access is a separate server requirement. If TCP 53 is unavailable to the Mac, configure the approved path before treating generic verification as usable. A preview can still inspect API configuration, but it does not verify target transfer health.

## 4. Phase 1 — export and review

```bash
python scripts/phase1_export_zones.py --config "$CONFIG"
echo "Exit code: $?"
```

Read the new OCID file and inventory using paths from your config:

```bash
IDS=$(python - "$CONFIG" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1])
print(p.parent / json.loads(p.read_text())['zone_ids_file'])
PY
)
cat "$IDS"
python -m json.tool "$IDS.inventory.json"
```

Confirm exact names/count, ACTIVE secondary state, signed source entries and correct compartment. Keep the reviewed selection fixed for the subsequent phases. An existing output filename is refused; each run above creates a new one.

## 5. Phase 2 — preview and add the target

Preview:

```bash
python scripts/phase2_add_upstream.py --config "$CONFIG"
echo "Exit code: $?"
```

Review the printed complete-list plans and summary CSV. They should retain source and add target with the same per-zone TSIG key reference. Then apply separately:

```bash
python scripts/phase2_add_upstream.py --config "$CONFIG" --apply
echo "Exit code: $?"
```

Require exit 0 and successful/unchanged read-back for every selected zone. If a command fails, inspect its report and current settings before proceeding.

## 6. Publish fresh data and verify Phase 2

Capture the start time and a new marker:

```bash
TRANSFER_SINCE2=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MARKER2="phase2-$(date -u +%Y%m%dT%H%M%SZ)"
printf '%s\n' "$MARKER2"
```

Through your DNS management process:

1. Set this marker on the configured TXT owner in **every selected zone**. For `record_name` `_oci-transfer-check`, the owner is `_oci-transfer-check.YOUR_ZONE`.
2. Increase each zone's current SOA serial.
3. Synchronize the target and send signed NOTIFY as supported by your server/provider.

The generic Python verifier does not make those record changes. For BIND, the DNS administrator can run `sudo rndc notify ZONE` on the upstream after its data is ready. NOTIFY requests a refresh and does not force OCI to select that sender.

Run the new verifier from the Mac:

```bash
python scripts/phase2_verify_upstream.py --config "$CONFIG" --marker "$MARKER2"
echo "Exit code: $?"
```

`capability-and-data-pass`, exit 0, means signed target AXFR and matching fresh SOA/TXT data at every assigned OCI nameserver passed. During source+target overlap, either server may supply OCI. Review actual transfer telemetry when source selection matters.

### Optional: require actual target transfer proof

For a BIND target, obtain its journal JSONL after the recorded start time and save it as `local/target-phase2-journal.jsonl`. An administrator can export it on that target with:

```bash
sudo journalctl -u named --since 'YOUR_PHASE2_START_TIMESTAMP' --no-pager -o json
```

Use the actual value of `TRANSFER_SINCE2` for that command, then transfer the output through the approved administration channel. Run:

```bash
python scripts/phase2_verify_upstream.py --config "$CONFIG" --marker "$MARKER2" \
  --logs-file local/target-phase2-journal.jsonl --transfer-since "$TRANSFER_SINCE2"
echo "Exit code: $?"
```

`data-and-transfer-pass` requires completed signed transfers from the target to OCI clients at every selected zone's fresh serial. Exit 2 means data checks passed but matching target-transfer evidence is incomplete. OCI may have selected the source; inspect the target log window and transfer IP list. Other DNS products/providers use their own equivalent telemetry.

## 7. Phase 3 — separately preview and remove source

After reviewing the verification gate, preview:

```bash
python scripts/phase3_remove_upstream.py --config "$CONFIG"
echo "Exit code: $?"
```

Review that only the source IP is removed and target/other intended entries remain. Apply separately:

```bash
python scripts/phase3_remove_upstream.py --config "$CONFIG" --apply
echo "Exit code: $?"
```

Require successful read-back. Keep the original source running if the target still depends on its replication feed.

## 8. Publish another marker and verify Phase 3

```bash
TRANSFER_SINCE3=$(date -u +%Y-%m-%dT%H:%M:%SZ)
MARKER3="phase3-$(date -u +%Y%m%dT%H%M%SZ)"
printf '%s\n' "$MARKER3"
```

Publish that new value at the configured TXT owner, increase SOA serials, synchronize the target and request NOTIFY. Then:

```bash
python scripts/phase3_verify_target.py --config "$CONFIG" --marker "$MARKER3"
echo "Exit code: $?"
```

This requires source absence and, by default, target-only configuration. For intentional additional upstreams use `--allow-other-upstreams` and review target telemetry. A new target log export can provide the stronger proof:

```bash
python scripts/phase3_verify_target.py --config "$CONFIG" --marker "$MARKER3" \
  --logs-file local/target-phase3-journal.jsonl --transfer-since "$TRANSFER_SINCE3"
echo "Exit code: $?"
```

## 9. Inspect results and recover

Every command prints its report filenames. The configured output directory has timestamped JSONL/CSV for updates and JSON for verification. Read JSON with `python -m json.tool PATH_TO_REPORT`.

| Exit code | Meaning |
| --- | --- |
| `0` | Preview/update success, or verifier success at its printed proof level |
| `1` | Input/state/API/transfer/data failure; inspect the report |
| `2` from verification | Fresh data passed; required actual target-transfer evidence is incomplete |

`--apply` changes OCI settings. The verifiers are read-only. Earlier completed updates can remain after an interruption. Use the matching before-settings backup, fresh current state/ETag and [rollback guidance](VERIFY_AND_ROLLBACK.md) before retrying or reversing a migration.
