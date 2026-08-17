#!/usr/bin/env python3
"""Unified WQPU command entrypoint."""

from __future__ import print_function

import json
import shutil
import sys


RELEASE_VERSION = "0.6.0"


def doctor():
    import wqpu
    from wqpu_chain import load_network_config
    import wqpu_runtime_pin

    network = load_network_config()
    checks = {
        "version": RELEASE_VERSION,
        "python": sys.version.split()[0],
        "openssl": bool(shutil.which("openssl")),
        "llama_cpp_tag": wqpu_runtime_pin.desired_tag(),
        "public_network_published": bool(network),
        "rpc_url": network.get("rpc_url") if network else None,
        "registry": network.get("registry") if network else None,
        "market": network.get("market") if network else None,
        "relayer_url": network.get("relayer_url") if network else None,
        "bootstrap_relays": len((network.get("relays") or [])) if network else 0,
    }
    print(json.dumps(checks, indent=2))
    return 0 if checks["openssl"] else 1


def main():
    args = [str(x) for x in sys.argv[1:]]
    if args and args[0].lower() in ("--version", "-v", "version"):
        print("WQPU {}".format(RELEASE_VERSION))
        return 0

    if args and args[0].lower() == "doctor":
        return doctor()

    if args and args[0].lower() == "claim":
        import wqpu_claim
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        return wqpu_claim.main()

    import wqpu
    import wqpu_runtime_pin
    wqpu.ensure_runtime = wqpu_runtime_pin.ensure_runtime

    # Keep accounting policy outside the large runtime module so malformed/partial
    # meter streams fail closed without touching the transport implementation.
    import wqpu_accounting
    import wqpu_runtime as runtime
    runtime.save_usage_receipt = wqpu_accounting.save_usage_receipt

    import wqpu_autopay
    return wqpu_autopay.main()


if __name__ == "__main__":
    raise SystemExit(main())
