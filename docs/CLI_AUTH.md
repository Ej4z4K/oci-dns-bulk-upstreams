# OCI API-key authentication and access

The Python scripts use an API-key profile. Session-token and instance-principal authentication require other SDK signers and are not supported by these entry points.

## 1. Gather your values

- Tenancy OCID from tenancy details.
- User OCID from the intended IAM user and identity domain.
- Compartment OCID containing the selected secondary zones.
- DNS management region for those zones.
- API key fingerprint and local private-key path.

An API key establishes identity; IAM policies grant access.

## 2. Configure the profile

If OCI CLI is installed, run `oci setup config`, choose the config file/profile and enter your own tenancy/user/region. Generate an API key pair or reference an existing private key. Upload the matching **public** PEM to the intended user's API keys in the OCI Console and verify its fingerprint.

The SDK reads the same file, normally `~/.oci/config` or `%USERPROFILE%\.oci\config`:

```ini
[DEFAULT]
user=ocid1.user.oc1..REPLACE_ME
fingerprint=REPLACE_WITH_PUBLIC_KEY_FINGERPRINT
key_file=/absolute/path/to/oci_api_key.pem
tenancy=ocid1.tenancy.oc1..REPLACE_ME
region=YOUR_DNS_MANAGEMENT_REGION
```

On Windows use a full path such as `C:/Users/Alice/.oci/oci_api_key.pem`. Comments occupy their own lines. If the key is encrypted, configure its passphrase according to the organization's handling policy. [Oracle required keys and OCIDs](https://docs.oracle.com/en-us/iaas/Content/API/Concepts/apisigningkey.htm).

An existing authenticated profile can be selected with `profile` in migration JSON or `--profile`. Omitted migration `region` uses that profile's region.

## 3. IAM access

An administrator can scope zone access and key reads to the required compartments. Example policy forms:

```text
Allow group DnsBatchOperators to use dns-zones in compartment YOUR_DNS_COMPARTMENT
Allow group DnsBatchOperators to read dns-tsig-keys in compartment YOUR_KEY_COMPARTMENT
```

The update workflow lists/gets/updates zones. The generic signed verifier reads the referenced TSIG resource, including the secret into process memory to sign requests. Key permissions must cover every referenced key, including keys in another compartment. Identity-domain group names must be qualified as required by the tenancy.

Transfer-server discovery uses the root tenancy compartment and may need separate read access. An administrator can supply the current transfer destination IP list instead. No Compute/VCN provisioning permissions are required to run these phase scripts. [Oracle DNS policy reference](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/dnspolicyreference.htm).

## 4. Check authentication

With CLI installed:

```bash
oci dns zone list --compartment-id YOUR_COMPARTMENT_OCID \
  --scope GLOBAL --zone-type SECONDARY --all \
  --profile YOUR_PROFILE --region YOUR_DNS_MANAGEMENT_REGION
```

Phase 1 export also exercises SDK authentication and zone-read access. An empty list means no matching visible zones; it does not confirm your intended selection. Resolve HTTP 401 key/profile issues and 404/NotAuthorizedOrNotFound region/OCID/IAM issues before applying updates.

## Credential roles

| Credential | Purpose | Handling |
| --- | --- | --- |
| API private key | OCI HTTPS authentication | Operator only; outside repository |
| SSH private key, if used | Upstream administration and log collection | Operator only; optional for core phase scripts |
| Per-zone TSIG secret | DNS transfer authentication | Upstream servers and OCI TSIG resource; never published |

The updater submits key OCIDs, not secrets. The verifier signs through an in-memory keyring and excludes the secret from reports. Store configuration, reviewed inventories and results beneath ignored `local/`; restrict Windows ACLs separately.
