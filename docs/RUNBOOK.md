# Reusable three-phase runbook

The main [README](../README.md) defines prerequisites, configuration fields, installation and phase commands. This runbook is for existing OCI public secondary zones with source/target IPs supplied by the operator. A [Mac terminal walkthrough](MAC_TERMINAL.md) covers the same entry points step by step.

## Before starting

- Configure API authentication, compartment/key permissions and the correct DNS management region.
- Install Python requirements on the operator.
- Prepare the target with each zone's matching TSIG key and synchronized data/SOA serial.
- Allow OCI transfer destination IPs at both servers and allow the operator's signed AXFR at the target for generic verification.
- Create ignored `local/migration.json` from the example; set your addresses, selection/count and TXT record owner.
- Start with one reviewed zone. Stop concurrent upstream changes and preserve source data/configuration backups.

Source and target can be anywhere OCI can reach. IPv4 and IPv6 inputs are supported by the migration/generic verification code. The scripts modify only the selected OCI upstream lists; they do not build DNS servers or route/firewall connectivity.

## Phase 1 — select and review

```bash
python scripts/phase1_export_zones.py --config local/migration.json
```

Or use exact unrelated names:

```bash
python scripts/phase1_export_zones.py --config local/migration.json --zone-names-file local/zone-names.txt
```

Review the new OCID text file and inventory. Confirm exact intended names/count, ACTIVE GLOBAL SECONDARY state, correct compartment and signed source entries. You can also create the OCID file manually. Keep one ID per line and keep the reviewed file fixed for later phases.

**Gate:** selection correct, target synchronized, signed transfer capability and connectivity prepared. Prefix export is literal `startswith`; exact-name selection avoids selecting unrelated matching domains.

## Phase 2 — preview, add, verify

Preview only:

```bash
python scripts/phase2_add_upstream.py --config local/migration.json
```

Read the IP/key before/after plans and CSV, then apply separately:

```bash
python scripts/phase2_add_upstream.py --config local/migration.json --apply
```

Require exit 0 and successful/unchanged read-back for every selected zone. Target inherits the source's per-zone TSIG key reference. Unrelated upstreams stay intact. Results include complete before-settings JSONL, per-zone results JSONL and summary CSV.

Publish a fresh TXT marker at the configured owner in every selected zone through your DNS management process; increase SOA serials, synchronize the target and send NOTIFY as supported. Verify:

```bash
python scripts/phase2_verify_upstream.py --config local/migration.json --marker YOUR_PHASE2_MARKER
```

This is read-only and works with real domains and arbitrary upstream IPs. `capability-and-data-pass` checks signed target AXFR and matching records at every OCI nameserver; it does not establish actual OCI source selection while both exist. Add timestamped target log evidence for `data-and-transfer-pass`, following [generic verification](GENERIC_VERIFICATION.md).

**Gate:** API outcomes successful, fresh data matching all nameservers, target signed transfer works, transfer evidence reviewed. Do not remove the source based only on API configuration alone.

## Phase 3 — separately preview/remove, verify again

Preview only:

```bash
python scripts/phase3_remove_upstream.py --config local/migration.json
```

Review the source removal and preserved target/other entries, then apply separately:

```bash
python scripts/phase3_remove_upstream.py --config local/migration.json --apply
```

Removal requires a signed target already present and matching source/target keys while both exist. It removes only source. It does not perform the preceding health gate itself.

Publish a **new** marker/higher SOA serial, synchronize target and request NOTIFY, then:

```bash
python scripts/phase3_verify_target.py --config local/migration.json --marker YOUR_PHASE3_MARKER
```

By default verification requires target-only. If unrelated upstreams are intentional, pass `--allow-other-upstreams`; it still requires source absence and target presence. Use logs to identify actual source selection among multiple candidates. Keep source running if the target still relies on it for private replication.

## Windows PowerShell

After installation, use the virtual environment's interpreter directly:

```powershell
$Py = '.\.venv\Scripts\python.exe'
& $Py scripts\phase1_export_zones.py --config local/migration.json
& $Py scripts\phase2_add_upstream.py --config local/migration.json
```

Review the preview before the separate apply:

```powershell
& $Py scripts\phase2_add_upstream.py --config local/migration.json --apply
& $Py scripts\phase2_verify_upstream.py --config local/migration.json --marker YOUR_PHASE2_MARKER
```

After publishing the fresh marker and reviewing the verification gate, preview removal:

```powershell
& $Py scripts\phase3_remove_upstream.py --config local/migration.json
```

Then apply separately and publish/verify another marker:

```powershell
& $Py scripts\phase3_remove_upstream.py --config local/migration.json --apply
& $Py scripts\phase3_verify_target.py --config local/migration.json --marker YOUR_PHASE3_MARKER
```

Check `$LASTEXITCODE` immediately after each invocation. On Linux/macOS check `$?`. Verification without logs is capability/data evidence only; see the generic guide for the stronger proof option.

## Results, interruptions and scaling

Use the printed filenames under your configured output directory. A preflight failure means no updates started. If apply fails, earlier writes may remain and later selected zones stop. Inspect current state, results and the relevant before backup before retrying. SDK transient retries and `api_delay` help with throttling; they do not create an all-zone commit.

See [verification and rollback](VERIFY_AND_ROLLBACK.md). Increase from one to ten and then reviewed sequential batches per compartment/management region. The code has no ten-zone cap, but no 1000-zone runtime is claimed. The verification scripts are read-only; publish fresh verification records through your existing DNS management process.
