# Updating upstream IPs across OCI secondary DNS zones

*Technical article draft · 13 October 2026*

An upstream DNS migration can require the same address change across hundreds of OCI public secondary zones. The documented Console, CLI and API workflows update a zone individually. A reviewed batch script can coordinate those per-zone operations, retain TSIG authentication and capture evidence at each stage.

The reusable workflow is: select exact zone OCIDs, add the replacement upstream during an overlap window, verify fresh transfers, remove the original address in a separate operation, then verify again.

## Understand the transfer relationship

The upstream maintains zone data and SOA serials. OCI secondary DNS pulls those contents and answers authoritative queries. A hidden-primary deployment delegates publicly to the secondary service while keeping upstream addresses outside that delegation. Source and target servers can be deployed wherever OCI can reach them; their locations and DNS software are separate from the migration scripts. [Oracle secondary DNS](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/secondary-dns.htm).

During overlap, both upstreams must serve consistent data. Their order in a submitted list should not be treated as a preferred/standby policy. Removing the source address from OCI also does not migrate the target's replication or record-management dependency.

## Prerequisites and configuration

The operator needs Python, an authenticated OCI API-key profile, zone list/get/update permissions, and a manually reviewed selection in the intended compartment and management region. The target must serve matching zone data with the source's per-zone TSIG key name, algorithm and secret. Configure network access for OCI transfers and the operator's signed AXFR capability checks.

The repository's main README includes installation, permissions, network requirements, code explanations and phase commands. One non-secret JSON file defines source/target IPv4 or IPv6 addresses, compartment, profile, region, selected-file path, expected count and report directory. CLI flags override those values. Paths in JSON are relative to the configuration file; region can fall back to the selected OCI profile.

The updater submits TSIG key OCIDs, preserving a zone's existing reference when adding the target. The verifier reads the referenced key into memory to sign its direct transfer request; secrets are excluded from reports and command arguments. [Oracle external-master model](https://docs.oracle.com/en-us/iaas/tools/python/latest/api/dns/models/oci.dns.models.ExternalMaster.html).

## Phase 1: make selection reviewable

```bash
python scripts/phase1_export_zones.py --config local/migration.json
```

The script paginates visible public secondary zones and selects a literal name prefix or exact names from a text file. It rejects missing requested names or a count mismatch, then writes an OCID file and adjacent inventory of current state/upstream/key references. It does not change any zone.

Review the inventory. Later phases operate only on that fixed OCID file, rather than rediscovering and modifying every zone in the compartment. A manually prepared file is also supported.

## Phase 2: add the replacement

```bash
python scripts/phase2_add_upstream.py --config local/migration.json
```

Preview shows every planned before/after list. Each target entry inherits the selected zone's source TSIG reference. Unrelated upstreams stay intact; a correct existing target is unchanged. An unsigned source or conflicting target key fails preflight.

After reviewing the preview, run the same command with `--apply`. All zones pass preflight before the first update. Before-settings backups are saved, each changed zone is read again, and a fresh ETag with `If-Match` protects the complete-list replacement. The script waits for ACTIVE and verifies read-back.

API success establishes configuration. Publish a fresh approved TXT marker through the upstream DNS management process, increase SOA serials, synchronize the target and send NOTIFY as supported. Then verify:

```bash
python scripts/phase2_verify_upstream.py --config local/migration.json --marker YOUR_PHASE2_MARKER
```

The verifier checks overlap configuration, signed AXFR directly from the target, and matching fresh SOA/TXT answers at every assigned OCI nameserver. `capability-and-data-pass` describes exactly that evidence. It does not establish which upstream OCI selected.

For direct source-selection evidence, supply a timestamped target BIND journal export plus the start time recorded before the update. The parser requires completed signed outgoing transfers to current OCI transfer destination IPs, at the selected zone/key/new serial. Old, incomplete or unrelated traffic does not pass. Other server products can use equivalent provider telemetry. [OCI transfer-server discovery](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/zone-transfer-server-list.htm).

## Phase 3: remove the original address

```bash
python scripts/phase3_remove_upstream.py --config local/migration.json
```

Review the separate removal preview and then apply with `--apply`. The script requires a signed target already configured and matching source/target TSIG references while both exist. It removes only the source and preserves intended remaining entries.

Publish another fresh marker/higher serial, allow the target to synchronize and verify:

```bash
python scripts/phase3_verify_target.py --config local/migration.json --marker YOUR_PHASE3_MARKER
```

By default the verifier requires the target as the sole remaining upstream. Intentional other upstreams require an explicit option. Target logs can establish actual transfer usage; fresh records alone are a different proof level.

## Scale with evidence and recovery

Start with one controlled zone, then a small reviewed batch. The code accepts caller-selected counts, but a 1000-zone runtime has not been established. Sequential API pacing and SDK transient retries help with throttling. They do not create an atomic all-zone transaction.

A failed apply can leave earlier updates completed. Use results, current OCI state and the relevant complete before-settings backup to decide whether to retry or restore. Restoration needs fresh ETag checks; a stale backup must not overwrite an unrelated concurrent change.

Keep the source running if the replacement still depends on it. Upstream-address migration, record ownership, replication and server retirement are separate changes.

The package includes a Mac terminal walkthrough, generic code explanations and 42 offline tests. These checks cover configuration, key preservation, concurrency, selection and evidence boundaries. A new live end-to-end acceptance run is required for the deployment being changed. The example is community code, not an Oracle-supported product. [Oracle zone update](https://docs.oracle.com/en-us/iaas/Content/DNS/Tasks/zone-update.htm).
