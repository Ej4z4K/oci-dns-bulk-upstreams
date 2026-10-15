#!/usr/bin/env python3
"""Phase 2: add VM-B using each selected zone's VM-A TSIG key."""
from oci_dns_batch_common import run_batch

if __name__ == "__main__":
    raise SystemExit(run_batch("add"))
