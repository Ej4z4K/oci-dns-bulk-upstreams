# Read-only OCI DNS MCP server

Expose a small part of the existing Python workflow to an MCP client. The server uses the official MCP Python SDK 2.3.0 and local **stdio** transport. It does not start an HTTP listener.

## Tools

| Tool | Input | Result |
| --- | --- | --- |
| `list_secondary_zones` | Optional `limit` (1–50, default 20), optional `page` | One page of public secondary zones with current upstreams and a `next_page` token |
| `get_secondary_zone` | `zone_id` | Name, lifecycle state, upstream IPs/ports, TSIG key OCIDs and assigned nameservers |
| `verify_secondary_zone` | `zone_id`, `expected_serial`, `marker`, optional `record_name` | SOA/TXT checks at every assigned OCI nameserver and an aggregate pass/fail result |

All tools use the compartment, API-key profile and management region selected by the operator at startup. A tool cannot switch those settings. Child compartments are not included automatically. `GLOBAL` in the API identifies the public DNS scope here.

The server calls only OCI `ListZones` and `GetZone`. It does not expose migration updates, SSH, shell execution, arbitrary file access, record publication, NOTIFY or TSIG-key retrieval. Responses contain TSIG **resource OCIDs**, never key secrets. It does not write result reports or alter zone files. Public zone names, upstream IPs and DNS answers returned by a tool are visible to the connected MCP client.

The implementation reuses `master_dict` and `fingerprint` from `oci_dns_batch_common.py`, plus the authoritative SOA/TXT checks and record-owner validation from `verify_dns.py`. It calls these functions directly rather than launching the migration CLI scripts.

## 1. Prerequisites

- Clone the repository. Python 3.11 is the CI target; local MCP tests also run on Python 3.12.
- Configure an OCI API-key profile as described in [CLI_AUTH.md](CLI_AUTH.md).
- Select the exact compartment OCID and DNS management region.
- For listing/inspection, allow HTTPS 443 from the operator to the OCI DNS API.
- For verification, allow UDP/TCP 53 from the operator to OCI's assigned authoritative nameservers.
- Use an MCP host that can launch a local stdio subprocess.

An administrator can grant read access using a policy such as:

```text
Allow group YOUR_DNS_READERS_GROUP to read dns-zones in compartment YOUR_COMPARTMENT_NAME
```

Adapt the group/domain syntax to your tenancy. The OCI `read dns-zones` verb covers listing and reading zone metadata. This server needs neither zone update permissions nor `read dns-tsig-keys`. Existing broader grants remain broader; use a dedicated reader profile when appropriate. See the [OCI DNS policy reference](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/dnspolicyreference.htm).

## 2. Install

Run from the repository root. The MCP dependency is optional and separate from the migration CLI dependencies.

### macOS or Oracle Linux

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-mcp.txt
```

If `.venv` already exists, activate it and run the install command.

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
$Py = '.\.venv\Scripts\python.exe'
& $Py -m pip install -r requirements-mcp.txt
```

## 3. Check startup

```bash
python scripts/oci_dns_mcp.py --help
python scripts/oci_dns_mcp.py \
  --compartment-id YOUR_COMPARTMENT_OCID \
  --profile YOUR_READ_ONLY_PROFILE \
  --region YOUR_DNS_MANAGEMENT_REGION
```

The second command waits for MCP messages on stdin. It is not an interactive DNS prompt. Stop it with Ctrl+C; normally your MCP client starts and stops this process itself. Stdout is reserved for MCP messages, and diagnostics go to stderr.

Omit `--region` to use the profile's region. Use `--config-file /absolute/path/to/config` for a non-default OCI config file. Credentials load on the first OCI read, so tool discovery can succeed even when a later API call reports an authentication error.

## 4. Connect a local MCP client

[mcp-client.example.json](../examples/mcp-client.example.json) provides a configuration for clients using the `mcpServers` JSON format. Replace every placeholder and adapt the enclosing format to your client.

```json
{
  "mcpServers": {
    "oci-dns-readonly": {
      "command": "/absolute/path/to/oci-dns-bulk-upstreams/.venv/bin/python",
      "args": [
        "/absolute/path/to/oci-dns-bulk-upstreams/scripts/oci_dns_mcp.py",
        "--compartment-id", "YOUR_COMPARTMENT_OCID",
        "--profile", "YOUR_READ_ONLY_PROFILE",
        "--region", "YOUR_DNS_MANAGEMENT_REGION"
      ]
    }
  }
}
```

Use absolute paths so the host's working directory does not matter. On Windows, use a command such as `C:\\repos\\oci-dns-bulk-upstreams\\.venv\\Scripts\\python.exe` and the corresponding absolute script path. There is no need to activate the virtual environment when the client uses its Python executable directly. Keep the API private key and real local configuration outside Git.

Restart/reload your client's MCP connection and confirm that exactly the three tools above appear.

## 5. Try the tools

Start with listing:

```json
{"limit": 10}
```

To fetch the following page, call `list_secondary_zones` again with the returned token:

```json
{"limit": 10, "page": "TOKEN_FROM_NEXT_PAGE"}
```

Continue until `next_page` is null. A page reads the zone list once, then reads each returned zone for its current upstreams. The default page therefore uses at most 21 OCI calls. Listing is not an atomic snapshot across pages. An error stops that call instead of returning a misleading complete inventory.

Inspect a zone using `get_secondary_zone`:

```json
{"zone_id": "ocid1.dns-zone.oc1..REPLACE_ME"}
```

Before verification, publish a fresh TXT marker and higher SOA serial through your existing DNS administration process, and wait for synchronization. Then call `verify_secondary_zone`:

```json
{
  "zone_id": "ocid1.dns-zone.oc1..REPLACE_ME",
  "expected_serial": 2026102501,
  "marker": "YOUR_FRESH_MARKER",
  "record_name": "_oci-transfer-check"
}
```

Replace the sample serial with the zone's actual new serial. The marker owner must remain inside the selected zone. Verification requires an ACTIVE public secondary zone with assigned nameservers. It runs up to four DNS checks concurrently, rejects non-authoritative answers, retries truncated UDP replies over TCP, and checks for concurrent upstream/nameserver changes afterward.

### Meaning of a verification pass

`status: pass` with `proof_level: authoritative-soa-txt` means every assigned OCI nameserver returned the expected serial and marker, with unchanged zone metadata during that check. `oci_target_transfer_observed` is null because this tool does not observe transfers. It does not perform signed AXFR, validate a migration phase's expected upstream set, or establish which upstream OCI used. Use the existing [signed target and transfer-log verification workflow](GENERIC_VERIFICATION.md) for those stronger checks.

An ordinary DNS mismatch produces a structured `status: fail`. Invalid inputs, authentication failures and rejected scope produce MCP tool errors. Raw OCI error bodies and credential-loading exception details are suppressed. OCI calls use connect/read timeouts of 5/20 seconds with SDK retries disabled; existing DNS queries use 8-second transport timeouts. Actual elapsed time can include OS name resolution and queued checks.

## 6. Offline tests

```bash
python -m unittest discover -s tests -v
python -m unittest discover -s tests_mcp -v
```

The MCP suite uses fake OCI responses and DNS checks, an in-process MCP client, and a real stdio subprocess handshake. No OCI key, live zone or upstream server is needed. These tests do not establish a live tenancy deployment. The CI workflow runs both suites on Linux and Windows.

## Sources

- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP tools, schemas and annotations](https://py.sdk.modelcontextprotocol.io/servers/tools/)
- [MCP client and stdio transport](https://py.sdk.modelcontextprotocol.io/client/)
- [OCI DNS IAM operations](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/dnspolicyreference.htm)

Sources checked 25 October 2026. The application exposes only the registered read operations; read-only MCP annotations describe that behavior and do not replace OCI IAM enforcement.
