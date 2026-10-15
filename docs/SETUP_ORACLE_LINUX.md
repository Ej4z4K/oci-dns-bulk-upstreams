# Operator setup: Oracle Linux 9

Use a separate operator machine if possible. DNS server software runs on the upstream servers; Python runs on the operator. HTTPS access to OCI and package sources is required. DNS verification also needs outbound UDP/TCP 53. The generic signed verifier also needs operator-to-target TCP 53 and TSIG-key read permission. SSH/SCP are only needed for server administration or collecting transfer logs.

## 1. Install CLI and Python

Oracle documents the following CLI packages for Oracle Linux 9:

```bash
sudo dnf -y install oraclelinux-developer-release-el9
sudo dnf -y install python39-oci-cli
oci --version
sudo dnf -y install python3.11 python3.11-pip git openssh-clients bind-utils
python3.11 --version
```

Use your organization's approved repositories. If `python3.11` is unavailable, enable the appropriate Oracle Linux 9 AppStream repository or install an approved Python 3.11 distribution. Do not replace the system Python. The CLI's Python and the application's virtual environment are separate.

Sources: [Oracle CLI installation](https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm), [Oracle Linux Python runtimes](https://docs.oracle.com/en/operating-systems/oracle-linux/9/python/python-InstallingPython.html).

## 2. Obtain the repository

Download/extract the published repository ZIP, or clone its actual URL once published. Change into the folder containing `requirements.txt` and `scripts/`:

```bash
cd /path/to/oci-dns-bulk-upstreams
umask 077
mkdir -p local
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -c 'import oci, dns; print("OCI SDK", oci.__version__, "DNS", dns.__version__)'
python -m unittest discover -s tests -v
```

Use the same activated terminal for the runbook. On a later day, run `source .venv/bin/activate` again. Do not run the scripts with `sudo`.

## 3. Configure authentication

Run `oci setup config`, upload its API public key, then follow [CLI_AUTH.md](CLI_AUTH.md). The SDK reads the same `~/.oci/config` profile. Protect the key and config:

```bash
chmod 700 ~/.oci
chmod 600 ~/.oci/config ~/.oci/oci_api_key.pem
```

Adjust the key path if the setup wizard used a different filename.

## 4. Check the script entry points

```bash
python scripts/oci_dns_export_zone_ids.py --help
python scripts/oci_secondary_masters.py --help
python scripts/oci_dns_remove_vm_a.py --help
python scripts/verify_dns.py --help
```

Continue with the [batch runbook](RUNBOOK.md).
