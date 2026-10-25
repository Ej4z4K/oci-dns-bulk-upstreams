# OCI secondary DNS: reusable upstream migration

Batch-migrate selected OCI public secondary zones from an **existing source upstream IP** to a **replacement target upstream IP**. The source and target can be DNS servers anywhere that OCI can reach: OCI, another cloud, an external DNS provider, or your data centre. Server names and deployment locations are caller defined.

All phase inputs come from command-line flags or a non-secret JSON configuration file. IPv4 and IPv6 addresses are accepted. Domain names, zone count, compartment, OCI profile and DNS management region are caller supplied. The tools use OCI's supported per-zone API and preserve each zone's existing TSIG key reference.

**Workflow:** export/review zone OCIDs → preview/add target → verify target → preview/remove source → verify again.


[Presentation and HLD diagrams (PDF)](presentations/OCI_DNS_Upstream_Migration.pdf)

The HLD views are on pages 5–8. Download the PDF and open it in a PDF reader, then click the phase tabs or **Next phase** to follow source-only, overlap, and target-only transfer flows. The phase tabs link between PDF pages. Bookmarks provide navigation to every section, and the final page links to the official reference documentation.

## Get the project

```bash
git clone https://github.com/Ej4z4K/oci-dns-bulk-upstreams.git
cd oci-dns-bulk-upstreams
```

## 1. Prerequisites — read before running

### Operator machine

| Requirement | Details |
| --- | --- |
| Python | Python 3.11 is the tested target; use a virtual environment and `requirements.txt` |
| Operating system | macOS, Oracle Linux or Windows; core phase scripts need neither SSH nor BIND on the desktop |
| OCI authentication | An API-key profile in `~/.oci/config` (Windows: `%USERPROFILE%\.oci\config`), matching uploaded public key, private key, user/tenancy OCIDs and fingerprint |
| OCI CLI | Optional for Python execution; useful for `oci setup config` and inspecting zones |
| OCI API connectivity | HTTPS access to the selected DNS management region |
| Verification connectivity | DNS UDP/TCP 53 to OCI authoritative nameservers and TCP 53 to the target for signed AXFR |
| Permissions | List/get/update selected DNS zones; the generic signed verifier additionally reads each referenced TSIG key, including its secret into process memory |
| Local storage | A writable ignored `local/` directory for reviewed selection, configuration, backups and reports |

See [API authentication and IAM examples](docs/CLI_AUTH.md), [Mac terminal walkthrough](docs/MAC_TERMINAL.md), [Oracle Linux installation](docs/SETUP_ORACLE_LINUX.md), and [Windows installation](docs/SETUP_WINDOWS.md). Have the DNS administrator grant the required compartment/key access. Transfer-server discovery for optional log proof uses the root tenancy compartment and may require separate permission; an administrator can supply the discovered IPs instead.

The API key, SSH key and TSIG secret are different credentials. API private keys stay outside this repository. The reusable configuration contains addresses, paths and identifiers, **not credentials or TSIG secrets**.

### OCI zones and upstream servers

1. Existing `ACTIVE`, `GLOBAL`, `SECONDARY` zones in the supplied compartment and management region. A batch operates in one compartment/region.
2. A reviewed text file containing the exact zone OCIDs to update, one per line. Comments beginning `#` and blank lines are accepted; duplicates and non-zone OCIDs are rejected.
3. Distinct source and target IPs. Use the addresses OCI will reach, including public/NAT addresses where applicable. The scripts cannot make an unreachable private address reachable. IPv6 interface scope identifiers such as `%eth0` are rejected. OCI external masters support port **53** only.
4. The source is already configured for every selected zone with a TSIG key reference. The target has the matching zone data, serial, TSIG key name, algorithm and secret for that zone. This migration deliberately reuses the source key; it does not create or rotate keys. Different zones may have different keys.
5. Both servers accept OCI transfer requests and signed notifications as appropriate. Discover current OCI transfer destination IPs for your region, then configure server transfer ACLs, network security rules and firewalls.
6. For the generic verifier, the operator must also be allowed to perform a **signed AXFR directly against the target**. If that path is unavailable, use your provider's transfer telemetry. API read-back alone is configuration evidence.
7. An approved TXT verification record inside every selected zone (for example `_oci-transfer-check`) and a fresh marker value. Publish it through your upstream DNS management process, increase each SOA serial, synchronize the target, then verify OCI. The generic verifier is read-only; it does not edit arbitrary DNS servers.
8. A backup/recovery plan and no concurrent upstream maintenance during the batch. Each invocation saves the complete original upstream lists before the first write, but separate zones do not form an atomic transaction.

The migration code accepts caller-selected zone names and counts. Start with one controlled zone, then a small reviewed batch. A 1000-zone runtime has not been rehearsed.

## 2. Install and configure the operator

Run from the root of this package.

### Linux/macOS

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
mkdir -p local
cp examples/migration.example.json local/migration.json
```

If OCI CLI is installed and the profile has not been configured:

```bash
oci setup config
```

Use an existing authenticated profile where available. The example `DEFAULT` profile is only a name; it can be any profile you configure.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
$Py = '.\.venv\Scripts\python.exe'
& $Py -m pip install -r requirements.txt
New-Item -ItemType Directory -Force local
Copy-Item examples\migration.example.json local\migration.json
```

Use `& $Py` instead of `python` in the commands below. Configuration-based commands are otherwise identical; replace script path separators as desired. This does not require changing PowerShell execution policy.

## 3. Set your own inputs once

Edit `local/migration.json`. Replace the compartment, region, sample addresses, selection and count:

```json
{
  "compartment_id": "ocid1.compartment.oc1..REPLACE_ME",
  "profile": "DEFAULT",
  "region": "REPLACE_WITH_DNS_MANAGEMENT_REGION",
  "source_ip": "192.0.2.10",
  "target_ip": "192.0.2.20",
  "zone_ids_file": "zone-ids.txt",
  "name_prefix": "example",
  "expected_count": 2,
  "output_dir": "results",
  "api_delay": 0.6,
  "verification": {
    "record_name": "_oci-transfer-check",
    "marker": "REPLACE_WITH_FRESH_MARKER"
  }
}
```

`192.0.2.10` and `192.0.2.20` are documentation examples, not usable servers. You can instead supply IPv6 addresses such as `2001:db8::10` and `2001:db8::20` as examples, then replace them with your real routable addresses.

### Configuration rules

- CLI flags override JSON values. For example: `--source-ip YOUR_IP --target-ip YOUR_IP --region YOUR_REGION`.
- Omit `region` or use JSON `null` to use the selected OCI profile's region. The reusable phase scripts have no fixed region.
- JSON paths are relative to the configuration file. In this example the OCID file is `local/zone-ids.txt` and reports are under `local/results/`. CLI path arguments are relative to the current terminal directory.
- `name_prefix` selects literal zone names starting with that string, not a wildcard or suffix. For unrelated domains, remove it and set `"zone_names_file": "zone-names.txt"`; put exact names in `local/zone-names.txt`. See `examples/zone-names.example.txt`.
- `expected_count` is your exact reviewed selection count. It is required for export; supply it for every migration/verification run to detect an unintended selection change.
- `api_delay` paces sequential calls; the default is 0.6 seconds. OCI SDK transient-error retries also apply. Adjust pacing to your tenancy's rate limits.
- The file cannot enable writes: `apply` is not an accepted config field. Only an explicit `--apply` flag changes OCI upstreams.
- If a phase-1 output already exists, choose a new `zone_ids_file` or override `--output`; export never overwrites an existing selection.

For command-line-only usage:

```bash
python scripts/phase2_add_upstream.py \
  --compartment-id YOUR_COMPARTMENT_OCID \
  --zone-ids-file local/zone-ids.txt \
  --source-ip YOUR_SOURCE_IP --target-ip YOUR_TARGET_IP \
  --expected-count YOUR_COUNT --profile YOUR_PROFILE --region YOUR_REGION
```

`--old-ip`/`--new-ip` remain aliases for `--source-ip`/`--target-ip`. The old script filenames remain compatible. Missing core inputs prompt in an interactive terminal; noninteractive runs require explicit inputs.

## 4. Phase 1 — export and manually review zones

```bash
python scripts/phase1_export_zones.py --config local/migration.json
```

Alternatively supply exact unrelated names without changing the configuration:

```bash
python scripts/phase1_export_zones.py --config local/migration.json \
  --zone-names-file local/zone-names.txt
```

Inspect the files:

```bash
cat local/zone-ids.txt
python -m json.tool local/zone-ids.txt.inventory.json
```

On PowerShell use `Get-Content local/zone-ids.txt` instead of `cat` if preferred.

**What the code does:** `phase1_export_zones.py` calls `oci_dns_export_zone_ids.main()`. It paginates `ListZones`, filters the supplied prefix or exact names, refuses missing names/count mismatches, reads each zone's current upstreams, and writes the OCID file plus a readable inventory. No zone is modified. You may instead manually create the OCID file from the Console; the later phases only use that reviewed file.

**Gate:** exact intended names/OCIDs, ACTIVE secondary zones, correct compartment/region, signed source upstream, and a synchronized target. Keep this OCID file fixed throughout the migration.

## 5. Phase 2 — preview and add the target

Preview:

```bash
python scripts/phase2_add_upstream.py --config local/migration.json
```

Review the printed IP changes and generated summary CSV, then apply separately:

```bash
python scripts/phase2_add_upstream.py --config local/migration.json --apply
```

**What the code does:** this entry point calls `run_batch("add")`. `desired_masters()` finds the source IP in each zone, inherits that zone's TSIG key OCID and port for the new target, preserves every unrelated entry, and treats an already-correct target as unchanged. It refuses an unsigned source, duplicate source/target entries, or an existing target with a different key reference.

All selected zones pass preflight before the first update. The code saves complete before-settings JSONL, re-reads the zone, checks its upstream fingerprint, then submits the complete replacement list with the **fresh ETag / If-Match**. It waits for ACTIVE and verifies read-back. A failure stops later updates; earlier successful updates remain applied.

### Verify after Phase 2

Using your source DNS management tool, publish a unique value at the configured TXT owner in every selected zone, increase each SOA serial, synchronize the target, and send NOTIFY as supported by your server/provider. For BIND, `sudo rndc notify ZONE` runs on the DNS server; it requests a refresh and does not choose OCI's upstream.

Put that fresh value in `verification.marker`, or override it:

```bash
python scripts/phase2_verify_upstream.py --config local/migration.json \
  --marker YOUR_FRESH_PHASE2_MARKER
```

**What the code does:** verifies that source and target are configured, fetches each target's TSIG resource into memory, requests a signed AXFR directly from the target, then checks the expected TXT value and the target's current SOA serial at **every assigned OCI nameserver**. It rechecks upstream settings to detect changes during verification. It does not edit DNS records or upstream lists.

Without transfer logs, success is `capability-and-data-pass`. This proves the target can serve signed transfers and that OCI has matching data. It does **not** prove OCI fetched from the target while the source is also configured. OCI may use either upstream; public documentation does not specify a selection priority.

To require actual target usage, supply timestamped target BIND logs as described in [generic verification](docs/GENERIC_VERIFICATION.md). Success then requires `data-and-transfer-pass`; missing matching transfer evidence returns exit 2.

## 6. Phase 3 — separately preview and remove the source

After reviewing the Phase 2 verification evidence, preview:

```bash
python scripts/phase3_remove_upstream.py --config local/migration.json
```

Review, then apply separately:

```bash
python scripts/phase3_remove_upstream.py --config local/migration.json --apply
```

**What the code does:** this entry point calls `run_batch("remove")`. It requires a signed target already present and, while source and target both exist, matching per-zone TSIG references. It removes only the source IP and preserves the target and unrelated upstreams. An already-removed source is unchanged. Backups, preflight, ETags, pacing, error handling and read-back follow the same shared implementation as Phase 2.

The removal script does not establish transfer health; complete and review the preceding verification first.

### Verify after Phase 3

Publish another unique TXT value and increase the SOA serial through your upstream DNS management process. Synchronize the target and request NOTIFY, then:

```bash
python scripts/phase3_verify_target.py --config local/migration.json \
  --marker YOUR_FRESH_PHASE3_MARKER
```

By default this requires **exactly the target** as the remaining upstream, signed AXFR from it and fresh matching data at every OCI nameserver. To retain intentionally unrelated upstreams, use `--allow-other-upstreams`; the verifier still requires source absence and target presence, but cannot infer a sole transfer path. Use target log evidence for direct observation.

If the target still copies from the source, keep the source running until that replication dependency is separately migrated.

## 7. Results and recovery

| Script | Outputs / result |
| --- | --- |
| Phase 1 export | OCID text file plus `.inventory.json`; refuses overwrite |
| Add/remove | Timestamped `before.jsonl`, `results.jsonl`, `summary.csv` |
| Generic verification | Timestamped JSON report with key OCIDs, SOA/TXT answers and optional completed transfer evidence |
| Update exit 0 | Preview passed or every applied zone matched read-back; inspect printed statuses |
| Update exit 1 | Error; prior successful writes can remain |
| Verification exit 0 | Capability/data passed, or data/transfer proof passed; inspect `proofLevel` and status |
| Verification exit 1 | Input/state/data/transfer error |
| Verification exit 2 | Fresh data passed but requested OCI target-transfer evidence is missing |

No report contains TSIG secrets. The generic verifier reads secrets into memory to sign requests; it never writes them to a file or command argument. Existing server key files contain secrets and must stay outside published repository content.

See [verification and rollback](docs/VERIFY_AND_ROLLBACK.md). Recover using the relevant before-settings backup, a fresh read/ETag and the complete list; do not restore a stale snapshot over another writer. There is no automatic multi-zone rollback. API/SSH/DNS timeouts can leave a partial run; inspect current state before retrying.

## 8. Code map and further details

| File | Role |
| --- | --- |
| `scripts/phase1_export_zones.py` | Neutral Phase 1 export entry point |
| `scripts/phase2_add_upstream.py` | Neutral Phase 2 add entry point |
| `scripts/phase2_verify_upstream.py` | Real-domain signed target and DNS checks during overlap |
| `scripts/phase3_remove_upstream.py` | Neutral Phase 3 removal entry point |
| `scripts/phase3_verify_target.py` | Real-domain checks after source removal |
| `scripts/oci_dns_config.py` | JSON/CLI precedence, profile region and paced SDK calls |
| `scripts/oci_dns_batch_common.py` | Reviewed selection, TSIG-aware plans, ETags, backups and results |
| `scripts/oci_dns_verify_upstream.py` | Signed AXFR, stage checks and optional timestamped log proof |
| `scripts/oci_dns_transfer_evidence.py` | Completed signed transfer evidence from BIND messages |
| `scripts/verify_dns.py` | Legacy read-only SOA/TXT checker with configurable TXT owner |
| `scripts/oci_dns_mcp.py` | Optional local MCP server for scoped inventory and SOA/TXT verification |

Detailed [code explanations](docs/CODE_EXPLANATION.md), [generic verification](docs/GENERIC_VERIFICATION.md), [runbook](docs/RUNBOOK.md), [test evidence](docs/TEST_RESULTS.md), and [GitHub publishing](docs/PUBLISH_TO_GITHUB.md) are included. Use the [Mac terminal walkthrough](docs/MAC_TERMINAL.md) to rehearse the phase scripts.

## 9. Offline tests and publishing

```bash
python -m unittest discover -s tests -v
```

42 offline tests pass as of 13 October 2026. They include configuration precedence, arbitrary real-domain and IPv6 fixtures, key preservation, ETag concurrency, missing selection rejection, stale-log rejection and verification proof boundaries. The new generalized workflow has not been run end-to-end against a live production domain, IPv6 upstream, Windows or Oracle Linux operator.

The project is published at [Ej4z4K/oci-dns-bulk-upstreams](https://github.com/Ej4z4K/oci-dns-bulk-upstreams). Keep `local/`, `.oci/`, private keys, real inventories, logs, TSIG manifests and Terraform state out of Git; Windows users must also restrict local directory ACLs. See the included `.gitignore` and publishing guide. Example addresses/names/OCIDs must be replaced.

## 10. Optional read-only MCP server

The local stdio server exposes `list_secondary_zones`, `get_secondary_zone` and `verify_secondary_zone`. The operator fixes the compartment, profile and region at startup. Tools list current upstreams and check authoritative SOA/TXT answers without modifying OCI zones or reading TSIG secrets.

```bash
python -m pip install -r requirements-mcp.txt
python scripts/oci_dns_mcp.py \
  --compartment-id YOUR_COMPARTMENT_OCID \
  --profile YOUR_READ_ONLY_PROFILE \
  --region YOUR_DNS_MANAGEMENT_REGION
```

The process waits for MCP messages rather than displaying an interactive prompt. Normally an MCP client launches it. See the [MCP setup and test guide](docs/MCP_SERVER.md) for macOS/Linux/Windows instructions, read-only IAM permissions, pagination, client configuration and tool examples. SOA/TXT verification establishes record freshness, not actual upstream selection. The existing signed AXFR and transfer-log checks remain available through the migration CLI.

Run the additional offline MCP suite after installing its optional dependencies:

```bash
python -m unittest discover -s tests_mcp -v
```

## Oracle references and scope

- [Updating one secondary zone's upstreams](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/zone-update.htm)
- [ExternalMaster: IPv4/IPv6 address, port 53 and TSIG reference](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/dns/models/oci.dns.models.ExternalMaster.html)
- [Secondary DNS, NOTIFY, TSIG and transfer-server discovery](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/secondary-dns.htm)
- [Python SDK update_zone](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/dns/client/oci.dns.DnsClient.html#oci.dns.DnsClient.update_zone)

Oracle's documented update operation targets one zone. This project batches it sequentially; it adds no atomic bulk API. References reviewed 13 October 2026. Public secondary zones/API-key profiles only; no instance-principal mode or private DNS in this version. This community example is not an Oracle-supported product. Code is MIT licensed; dependencies retain their own licenses.
