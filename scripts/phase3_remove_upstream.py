#!/usr/bin/env python3
"""Remove only the source IP after a matching signed target exists; default preview."""
from oci_dns_batch_common import run_batch

if __name__ == "__main__":
    raise SystemExit(run_batch('remove'))
