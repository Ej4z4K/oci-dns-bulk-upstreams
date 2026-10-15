# Verification for existing upstream servers and real domains

These read-only scripts accept any valid upstream IPv4/IPv6 IP and any selected OCI public secondary zone name:

- `phase2_verify_upstream.py`: source and target configured.
- `phase3_verify_target.py`: source removed, target retained.

They require an authenticated API-key profile, read access to the per-zone TSIG resource, direct signed TCP AXFR connectivity from the operator to the target, and DNS access to OCI authoritative servers. See the main [prerequisites](../README.md#1-prerequisites--read-before-running).

## 1. Publish fresh data through your DNS management process

Choose a unique marker for each verification stage, for example `phase2-20261012T100000Z`. Set it on the configured relative TXT owner, such as `_oci-transfer-check`, in **every selected zone**. Increase each zone's SOA serial according to DNS serial arithmetic. Wait for the target to serve those values and send signed NOTIFY as supported by your upstream DNS software/provider.

Source and target must be synchronized before the overlap exercise. If the target is independently managed, use the same controlled records on both. If it replicates from the source, keep that source running until the separate replication dependency is migrated.

The verifier does not perform these writes. It does not assume a file path, OS, server product, hidden-primary deployment or record-management API. A failed data check can reflect an update still propagating; allow the configured SOA/NOTIFY timing and retry read-only verification.

Configure `verification.record_name` and `verification.marker`, or override flags:

```bash
python scripts/phase2_verify_upstream.py --config local/migration.json \
  --record-name _oci-transfer-check --marker YOUR_FRESH_PHASE2_MARKER
```

After source removal and another fresh update:

```bash
python scripts/phase3_verify_target.py --config local/migration.json \
  --record-name _oci-transfer-check --marker YOUR_FRESH_PHASE3_MARKER
```

Relative TXT owners, `@` for the apex, or `_check.{zone}.` templates are supported. An absolute name must remain inside that selected zone. No `.test` restriction or ten-zone cap applies to these read-only checks.

## 2. Understand a result without logs

`capability-and-data-pass` verifies:

- Required signed upstream configuration for this stage.
- Successful signed AXFR directly from the target using the zone's OCI TSIG key.
- Target's fresh marker and SOA serial match every assigned OCI nameserver.
- Upstream/key settings did not change during the zone's check.

This does not observe OCI's transfer request. With source+target, either can supply data. With target-only, fresh unique data supports propagation through the configured target, but direct server logs give stronger transfer evidence. Do not describe an operator-issued AXFR as an OCI-issued transfer.

## 3. Add actual OCI transfer proof from BIND logs

Before publishing the fresh marker, record a UTC timestamp on the operator:

```bash
TRANSFER_SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
```

Then publish the marker/increase the SOA as above and let OCI update. Export the **target server's** named journal for that time window in JSON Lines format. Example from a Linux target:

```bash
sudo journalctl -u named --since 'YOUR_UTC_START_TIMESTAMP' --no-pager -o json
```

Save/copy that output to the operator as `local/target-journal.jsonl`. Use authenticated administration to obtain the actual target's logs. The script expects `MESSAGE` and `__REALTIME_TIMESTAMP` journal fields, not screenshots or plain-text copies.

PowerShell start timestamp:

```powershell
$TransferSince = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
```

Run with the log file and timestamp (Linux/macOS):

```bash
python scripts/phase2_verify_upstream.py --config local/migration.json \
  --marker YOUR_FRESH_PHASE2_MARKER \
  --logs-file local/target-journal.jsonl --transfer-since "$TRANSFER_SINCE"
```

For Phase 3, use `phase3_verify_target.py` and a new marker/start timestamp/log export. On Windows use `& $Py`, the script's Windows path and `$TransferSince`.

The verifier discovers OCI transfer **destination** IPs in the selected management region using the tenancy/root compartment. If discovery permission is unavailable, supply a reviewed current list with `--transfer-ips IP1,IP2` (IPv4 or IPv6), or `verification.transfer_ips` in JSON. Transfer-server addresses are discovered for the selected region.

Success requires `data-and-transfer-pass`: fresh data plus completed signed outgoing transfers from target to discovered OCI client IPs at each selected zone's target serial. Missing evidence returns `target-transfer-not-observed`, exit 2. The parser rejects old log entries and serials, SOA/NOTIFY-only messages, incomplete transfers and target receiving from its own source.

BIND journal evidence is the supplied adapter. Other DNS server products/providers can use the generic signed AXFR/data test, plus their own equivalent logs or transfer telemetry. Do not label unrecognized provider logs as a BIND proof result.

## 4. Additional upstreams

The migration preserves unrelated upstreams. The verifier defaults to exactly source+target during overlap and exactly target after removal so a sole-target rehearsal is clear. For an intentional additional upstream, pass `--allow-other-upstreams`. It still requires the target with TSIG, and source absence after removal. Supply log proof if you need to identify the actual transfer source among several candidates.

## 5. Secrets and outputs

The SDK fetches a TSIG secret into process memory, signs the AXFR through dnspython's in-memory keyring and discards it when the process ends. Nothing writes secrets to result files or shell command lines. No DNS records, upstream settings, TSIG resources, server files or firewalls are changed.

Reports contain zone/key OCIDs, requested IPs, marker/serial values, DNS answers and optional completed transfer messages. Store them under ignored `local/`. Windows ACLs must be restricted separately; Unix mode 0600 is not a Windows ACL.
