#!/usr/bin/env python3
"""Phase 3: remove VM-A after VM-B has been verified on the selected zones."""
from oci_dns_batch_common import run_batch

if __name__ == "__main__":
    raise SystemExit(run_batch("remove"))
