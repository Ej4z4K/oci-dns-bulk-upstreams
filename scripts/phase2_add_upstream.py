#!/usr/bin/env python3
"""Append the target IP using each zone's source TSIG reference; default preview."""
from oci_dns_batch_common import run_batch

if __name__ == "__main__":
    raise SystemExit(run_batch('add'))
