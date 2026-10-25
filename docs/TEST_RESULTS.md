# Test coverage and execution status

## Offline suite

Run from the package root with the virtual environment active:

```bash
python -m unittest discover -s tests -v
```

The suite contains 42 offline tests. It does not require OCI credentials, DNS access or upstream administration. Coverage includes:

- Source/target IPv4 and IPv6 handling, per-zone TSIG preservation and unrelated-entry preservation.
- Shared JSON inputs, CLI overrides, file-relative paths and selected-profile region fallback.
- Exact-name export, count mismatch, duplicate/invalid IDs and missing-name rejection.
- Preview behavior, all-zone preflight and fresh ETag checks before updates.
- Authoritative SOA/TXT verification, record-owner selection and DNS error handling.
- Signed target capability and matching data without claiming actual OCI target usage.
- Completed outgoing transfer classification, matching client/key/serial and timestamped evidence.
- Rejection of old/incomplete/unrelated transfer evidence and proof-level reporting.
- Compatibility behavior for previous entry points and helper functions.

## Read-only MCP suite

Install `requirements-mcp.txt` and run:

```bash
python -m unittest discover -s tests_mcp -v
```

On 25 October 2026, all 42 existing tests and 16 MCP tests passed locally on macOS with Python 3.12.14 and MCP SDK 2.3.0. The MCP tests cover scoped and paginated reads, secret filtering, schema bounds, rejected extra arguments, DNS failure/concurrent-change handling, and the freshness-only proof boundary. They exercise both an in-process client and a real stdio subprocess handshake with no OCI credentials.

The GitHub Actions workflow runs both suites on Linux and Windows with Python 3.11. The MCP server has not been exercised against a live OCI tenancy. Fake DNS/API responses establish application behavior, not actual DNS transfer success.

## Live acceptance criteria

Offline tests validate implementation contracts; they do not establish deployment success. Rehearse with one controlled selected zone, then a small reviewed batch. Require:

1. Correct source baseline and matching target records/TSIG material.
2. Successful Phase 2 configuration read-back.
3. Fresh marker/higher SOA serial on target and every OCI nameserver.
4. Reviewed target transfer evidence if actual OCI source selection is required.
5. Successful Phase 3 source removal read-back.
6. Another fresh marker/serial and successful target verification after removal.

Record operator OS, Python/SDK versions, management region, selected count, statuses and report filenames for that run. Keep resource identifiers and server logs private. The generalized workflow has not completed a new live end-to-end acceptance run in this documentation update. Do not infer IPv6 or operating-system execution evidence from fixture tests.

See [Mac terminal testing](MAC_TERMINAL.md) and [generic transfer verification](GENERIC_VERIFICATION.md).
