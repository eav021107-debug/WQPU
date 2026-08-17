#!/usr/bin/env python3
"""Unified WQPU command entrypoint."""

from __future__ import print_function

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1].lower() == "claim":
        import wqpu_claim
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        return wqpu_claim.main()

    import wqpu
    import wqpu_runtime_pin
    wqpu.ensure_runtime = wqpu_runtime_pin.ensure_runtime

    # Keep accounting policy outside the large runtime module so a malformed/partial
    # meter stream can fail closed without touching the transport implementation.
    import wqpu_accounting
    import wqpu_runtime as runtime
    runtime.save_usage_receipt = wqpu_accounting.save_usage_receipt

    import wqpu_autopay
    return wqpu_autopay.main()


if __name__ == "__main__":
    raise SystemExit(main())
