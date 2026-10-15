# Verification and rollback

## Configuration, capability and actual transfer evidence

An ACTIVE zone and API read-back confirm upstream configuration. Signed AXFR issued by the operator confirms target capability. A fresh SOA serial and TXT marker at every assigned OCI nameserver confirm matching data. Target transfer telemetry can show which upstream actually served OCI.

Use `phase2_verify_upstream.py` while source and target are configured and `phase3_verify_target.py` after removing the source. Publish a new marker/higher serial for each stage through the upstream DNS management process. The verifiers are read-only. BIND JSONL logs can provide `data-and-transfer-pass`; otherwise `capability-and-data-pass` does not observe the actual OCI transfer source. See [generic verification](GENERIC_VERIFICATION.md).

OCI metrics `ZoneTransferSuccessCount`, `ZoneTransferFailureCount` and `ZoneExpirationInHours` in namespace `oci_dns` provide additional operational evidence. [Oracle DNS metrics](https://docs.oracle.com/en-us/iaas/Content/DNS/Reference/metrics.htm).

## Inspect a partial run before retrying

The before-settings JSONL contains complete original upstream lists, ports, key references and ETags. Results identify updated, unchanged, preview or error zones. Earlier successful updates can remain when a later zone fails. Read current OCI state and reconcile it with the relevant before backup. Do not assume automatic rollback or an atomic multi-zone commit.

## Restore the exact before list for a zone

Extract the intended zone's complete `externalMasters` list from its applicable backup into a protected file, such as `local/restore-masters.json`:

```json
[
  {
    "address": "192.0.2.10",
    "port": 53,
    "tsigKeyId": "ocid1.dns-tsig-key.oc1..REPLACE_ME"
  }
]
```

Use actual backup values. Read the current zone and obtain its current ETag:

```bash
oci dns zone get --zone-name-or-id YOUR_ZONE_OCID --scope GLOBAL \
  --profile YOUR_PROFILE --region YOUR_DNS_MANAGEMENT_REGION
```

Review current state and concurrent changes, then restore with that fresh ETag:

```bash
oci dns zone update --zone-name-or-id YOUR_ZONE_OCID --scope GLOBAL \
  --external-masters file://local/restore-masters.json --if-match CURRENT_ETAG \
  --profile YOUR_PROFILE --region YOUR_DNS_MANAGEMENT_REGION --wait-for-state ACTIVE
```

This replaces the complete upstream list. The old backup ETag is normally stale. Verify the restored upstream's health and fresh records afterward. There is no automatic bulk rollback command because unrelated concurrent changes require review.

## Return to the original source through a reverse migration

After verifying the original source is healthy and synchronized, use a separately reviewed reverse configuration: swap `source_ip` and `target_ip`, preserve the selected OCID file and choose a new results directory. Preview/add the original source, verify it, then preview/remove the replacement. The phase commands still perform add and remove; the input addresses define the roles.

For an exact configuration restore, use the complete before list instead of assuming a reverse migration reconstructs every original setting. A marker restore must use a higher SOA serial; copying an old zone file/serial back can prevent secondary updates.

## Retire an upstream only after removing dependencies

Removing an address from OCI does not migrate record ownership, target replication, DNSSEC signing or other consumers. Verify the replacement's record-management and replication process before shutting the original server down. The phase scripts do not retire servers or establish regional redundancy.
