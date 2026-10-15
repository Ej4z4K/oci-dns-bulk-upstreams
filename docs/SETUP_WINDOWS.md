# Operator setup: Windows 10/11

Use PowerShell and a Python 3.11 virtual environment. Keep upstream DNS server administration separate from the operator scripts. Normal script execution does not need Administrator privileges.

## 1. Install OCI CLI

Download the Windows MSI from the official [Oracle OCI CLI releases](https://github.com/oracle/oci-cli/releases), following [Oracle's Windows instructions](https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm). Install it, open a **new** PowerShell window, then check:

```powershell
oci --version
```

If the command is missing, check the installer's PATH entry and reopen PowerShell. Use the MSI procedure rather than pasting a remote installer into an administrative shell.

## 2. Install Python 3.11

Install an approved Python 3.11 distribution with the Python launcher (`py`). Official installers are at [python.org](https://www.python.org/downloads/windows/). Confirm:

```powershell
py -3.11 --version
```

Download/extract this project's ZIP, or clone its actual repository URL once published. Open PowerShell at the extracted repository root.

## 3. Create a private working directory and virtual environment

```powershell
New-Item -ItemType Directory -Force local | Out-Null
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
icacls .\local /inheritance:r /grant:r "${Identity}:(OI)(CI)F"
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -c "import oci, dns; print(oci.__version__, dns.__version__)"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

These commands call the virtual environment's interpreter directly; no PowerShell activation-policy change is needed. Use this interpreter for every runbook command. `icacls` protects files from ordinary other local users; privileged administrators can still access them. Check your enterprise backup/access policies separately.

## 4. Configure CLI and protect credentials

```powershell
oci setup config
```

Follow [CLI_AUTH.md](CLI_AUTH.md), upload the API public key and verify its fingerprint. The usual config file is `$env:USERPROFILE\.oci\config`. In it, use a full forward-slash key path such as `C:/Users/Alice/.oci/oci_api_key.pem`.

```powershell
icacls "$env:USERPROFILE\.oci" /inheritance:r /grant:r "${Identity}:(OI)(CI)F" /T
```

Inspect the resulting ACL with `icacls`; do not assume Python's Unix mode `0600` restricts access on Windows. Store batch results beneath `local/`, which inherits the restricted ACL.

## 5. Use the scripts

```powershell
.\.venv\Scripts\python.exe scripts\oci_dns_export_zone_ids.py --help
.\.venv\Scripts\python.exe scripts\oci_secondary_masters.py --help
.\.venv\Scripts\python.exe scripts\oci_dns_remove_vm_a.py --help
```

Use the Windows examples in [RUNBOOK.md](RUNBOOK.md). `verify_dns.py` uses Python and does not require Windows `dig`. Windows OpenSSH Client supplies `ssh`/`scp` for optional server administration and collecting transfer logs; install it through Optional Features if missing, according to your administrator's policy.

## Text files

The selection parser accepts UTF-8 with or without a BOM and Windows CRLF. A plain text editor is suitable. Do not use Windows PowerShell 5.1's default `Out-File` encoding (UTF-16) for the zone selection file. Prefer the export script, or explicitly write UTF-8:

```powershell
$Utf8 = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllLines("$PWD\local\zone-ids.txt", $ZoneIds, $Utf8)
```

`$ZoneIds` must be the reviewed array of actual zone OCIDs. Do not paste the placeholder examples into a real apply run.
