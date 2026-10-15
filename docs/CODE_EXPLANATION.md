# Code explanation, phase by phase

Start with the prerequisites and commands in the main [README](../README.md). Every phase accepts the same non-secret migration configuration, with CLI overrides.

## Shared configuration: `oci_dns_config.py`

`load_options()` reads a UTF-8 JSON object, rejects undocumented fields, resolves JSON paths against the config file's directory, then fills only CLI values that were omitted. Explicit flags win. Config cannot contain `apply` or key secrets. `source_ip`/`target_ip` map to the older internal `old_ip`/`new_ip` names so old commands remain usable.

`sdk_config()` loads the requested API-key profile. It only overrides region when the caller provides one. `PacedDnsClient` wraps SDK methods with a configurable delay and OCI's transient-error retry strategy. These calls are sequential; pacing and retries do not make separate-zone updates transactional.

## Phase 1: `phase1_export_zones.py`

The entry point calls `oci_dns_export_zone_ids.main()`:

1. Resolve compartment, exact name-selection method, count and output path.
2. Paginate `ListZones` for GLOBAL SECONDARY zones in that compartment.
3. Select a literal prefix or exact unrelated names; reject missing exact names or count mismatches.
4. Get each selected zone and record name, OCID, state and existing upstream/TSIG references.
5. Create a new OCID text file and adjacent inventory without overwriting existing files.

This phase never calls `UpdateZone`. Review the inventory before the next phase. The add/remove scripts then read **only that file**, not the compartment's discovered list.

## Phase 2: `phase2_add_upstream.py`

The entry point selects the fixed action `run_batch("add")`; the shared engine performs the work.

`selected_ids()` accepts UTF-8/BOM and CRLF, skips comments/blank lines and rejects duplicate/non-zone IDs. `desired_masters()` normalizes source/target address comparisons, finds the existing source, and constructs one added target entry using the source's per-zone `tsig_key_id` and port. Existing unrelated entries remain the same objects. Already-correct target entries are idempotent. A different target key fails rather than silently changing authentication.

`run_batch()` validates all selected zones before any write. `secure_file()` creates audit files with exclusive creation and Unix permissions 0600. The backup records the complete original upstream list, ETag and selected-file hash. Preview logs plans and stops without SDK writes.

With `--apply`, each changed zone is read again. `fingerprint()` compares addresses, effective ports and key references against preflight. The update uses the fresh ETag with `If-Match`, submits the complete `external_masters` list, waits for ACTIVE and checks read-back. This protects the complete-list replacement from overwriting a changed upstream list. Missing fresh ETags fail closed.

API success is configuration evidence. Follow with the verification stage before removing the source.

## Verification after Phase 2: `phase2_verify_upstream.py`

Calls `oci_dns_verify_upstream.main("overlap")`. By default the zone must contain exactly source and target. An explicit `--allow-other-upstreams` permits intentional additional entries. Source and target share the zone's TSIG reference.

The operator publishes a fresh TXT marker and higher SOA serial through their own DNS management system. This code does not assume BIND, a VM, `.test`, a key-name convention, a region or a zone-file path.

`validate_stage()` checks the selected zone's identity/state and upstream set. `signed_axfr()` reads the target's TSIG resource into process memory and passes an in-memory keyring to dnspython. It requests a signed TCP AXFR directly from the target IP. The secret is never printed, persisted or put in argv.

`target_data()` reads the returned SOA and configured TXT record. `verify_dns.check()` queries every assigned OCI authoritative nameserver without recursion, requires authoritative NOERROR, retries a truncated UDP reply over TCP, and compares the fresh marker and target serial. A final upstream fingerprint check detects concurrent changes.

Without log proof, `capability-and-data-pass` means target capability plus matching OCI data. It makes no claim about which upstream OCI chose. With structured target logs, the verifier requires a completed transfer from an OCI client at the fresh target serial before returning `data-and-transfer-pass`.

## Phase 3: `phase3_remove_upstream.py`

Selects `run_batch("remove")`. `desired_masters()` requires the target with TSIG already present and a matching source/target key while both exist, then removes only the source. Target and unrelated entries remain. If the source is absent already, it reports unchanged. It refuses an unsafe missing/unsigned target.

Preflight, backups, ETags, update/read-back, JSONL results and CSV summaries use the same engine as Phase 2. The target health test is a separate gate; the removal engine does not infer health from the presence of its IP.

## Verification after Phase 3: `phase3_verify_target.py`

Calls `main("after-removal")`. Source must be absent; by default target is the only remaining upstream. It reads a **new** operator-published marker, signed target AXFR and matching answers from every OCI nameserver. Additional upstreams require the explicit option and weaken sole-source inference. Timestamped target logs provide direct usage evidence.

## Transfer evidence parser

`oci_dns_transfer_evidence.completed_transfers()` accepts completed outgoing AXFR/IXFR (including BIND's AXFR-style IXFR), for the requested zone/key/new serial and a discovered OCI client address. IPv6 addresses are compared after normalization. Old serials, an incomplete start, unsigned traffic, different keys/clients and incoming replication do not count.

The generic verifier reads JSONL from `journalctl -o json`; `journal_messages()` filters by the operator's timezone-aware start timestamp. A log entry without a timestamp is rejected. This proof adapter is BIND-specific; other DNS products need equivalent telemetry rather than invented log matches.

## Failure and retry model

Preflight failure means no upstream updates began. During apply, earlier updates can remain when a later zone fails. Each results file identifies completed/unchanged/error zones. SDK retries handle transient request failures, but callers must still inspect current state after a timeout. Restore from the relevant complete-list backup with fresh concurrency checks; never assume an all-zone rollback occurred.
