# Troubleshooting

| Symptom | Checks and response |
| --- | --- |
| OCI HTTP 401 | Match API public key, fingerprint, private-key path, user/tenancy OCIDs, passphrase and clock |
| 404 / NotAuthorizedOrNotFound | Check region, compartment, resource OCID and IAM scope |
| Python import failure | Activate the package virtual environment; the CLI's Python environment is separate |
| Export mismatch | Review exact names/prefix and expected count; select intended zones rather than bypassing the count check |
| Invalid/duplicate OCID file | Use one zone OCID per line and UTF-8; inspect duplicate lines and encoding |
| Preflight failure | Confirm ACTIVE GLOBAL SECONDARY state, expected compartment and source/target key references |
| ETag conflict | Another writer changed the zone; reconcile current upstreams and repeat preview |
| API timeout or failed update | The request may have reached OCI; inspect current state and partial results before retrying |
| HTTP 429 | Increase `api_delay`, allow transient retries and avoid concurrent API maintenance |
| Target TCP 53 unreachable from Mac | Check routing/VPN and target network/host-firewall rules for the Mac's actual outbound address |
| Signed AXFR fails | Check target zone availability, per-zone TSIG name/algorithm/secret, AXFR ACL and clock synchronization |
| TSIG resource read denied | Grant access to the referenced key compartment; updater access alone is insufficient for signed verification |
| Fresh marker absent | Publish the expected TXT owner/value, increase SOA serials, synchronize target and allow transfer timing |
| OCI SERVFAIL | Check transfer reachability, TSIG, SOA expiry and server logs/OCI transfer metrics |
| NOTIFY produces no new transfer | Ensure the SOA serial is newer; NOTIFY requests a refresh, not a forced server selection |
| `capability-and-data-pass` | Signed target AXFR and matching OCI data passed; actual OCI target usage was not observed |
| `target-transfer-not-observed` | Inspect target log window, client IPs, zone/key/serial and completed events; OCI may choose source during overlap |
| Log file rejected | Supply target journal JSONL with `MESSAGE` and `__REALTIME_TIMESTAMP`, plus a timezone-aware start timestamp |
| Existing output file | Choose a new export filename; preserve existing selection/evidence |
| Windows permissions too broad | Restrict ignored local storage with Windows ACLs; mode 0600 is not a Windows ACL |

Diagnose connectivity and TSIG before altering working upstreams. Preserve request IDs and private evidence for support. Keep credentials and unsanitized records/logs out of public issues.
