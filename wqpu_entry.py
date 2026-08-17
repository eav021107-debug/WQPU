#!/usr/bin/env python3
"""Unified WQPU command entrypoint."""

from __future__ import print_function

import json
import os
import shutil
import sys


RELEASE_VERSION = "0.6.0"


def install_public_config_compat():
    import wqpu_chain
    import wqpu_public_config
    wqpu_public_config.install(wqpu_chain)


def force_tunneled_rpc_transport():
    """Prevent llama.cpp RPC from negotiating a direct RDMA path around WQPU."""
    os.environ["GGML_RDMA_DEV"] = "__wqpu_tcp_tunnel_only__"


def doctor():
    install_public_config_compat()
    import wqpu
    import wqpu_accel
    from wqpu_chain import load_network_config
    import wqpu_runtime_pin

    network = load_network_config()
    accel = wqpu_accel.info(wqpu.total_ram_mb())
    checks = {
        "version": RELEASE_VERSION,
        "python": sys.version.split()[0],
        "openssl": bool(shutil.which("openssl")),
        "llama_cpp_tag": wqpu_runtime_pin.desired_tag(),
        "accelerator": accel["accelerator"],
        "runtime_variant": accel["runtime_variant"],
        "vram_mb": accel["vram_mb"],
        "public_network_published": bool(network),
        "rpc_url": network.get("rpc_url") if network else None,
        "registry": network.get("registry") if network else None,
        "market": network.get("market") if network else None,
        "relayer_url": network.get("relayer_url") if network else None,
        "bootstrap_relays": len((network.get("relays") or [])) if network else 0,
        "rpc_transport": "tcp-inside-wqpu-tunnel",
    }
    print(json.dumps(checks, indent=2))
    return 0 if checks["openssl"] else 1


def main():
    args = [str(x) for x in sys.argv[1:]]
    if args and args[0].lower() in ("--version", "-v", "version"):
        print("WQPU {}".format(RELEASE_VERSION))
        return 0

    install_public_config_compat()

    if args and args[0].lower() == "doctor":
        return doctor()

    if args and args[0].lower() == "claim":
        import wqpu_claim
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        return wqpu_claim.main()

    # b10456 can auto-negotiate RDMA after HELLO. WQPU deliberately keeps the RPC byte
    # stream inside its authenticated/metered TCP/TLS relay, regardless of CPU/GPU backend.
    force_tunneled_rpc_transport()

    import wqpu
    import wqpu_gpu_patch
    import wqpu_runtime_pin
    wqpu_gpu_patch.install_wqpu(wqpu)
    wqpu.ensure_runtime = wqpu_runtime_pin.ensure_runtime

    # Keep accounting policy outside the large runtime module so malformed/partial
    # meter streams fail closed without touching the transport implementation.
    import wqpu_accounting
    import wqpu_runtime as runtime
    runtime.save_usage_receipt = wqpu_accounting.save_usage_receipt
    wqpu_gpu_patch.install_runtime(runtime, wqpu)

    # Public-network security is layered on only for ChainMesh. Legacy/private mode
    # keeps the original Mesh behavior while still benefiting from accelerator selection.
    import wqpu_network_guard
    wqpu_network_guard.install(runtime)

    import wqpu_autopay
    import wqpu_multistream
    import wqpu_public_security
    # Real llama.cpp opens multiple RPC sockets per logical request. Aggregate only
    # individually verified worker stream reports before comparing with requester usage.
    wqpu_multistream.install(wqpu_autopay.AutoPayChainMesh)
    wqpu_public_security.install(wqpu_autopay.AutoPayChainMesh)
    return wqpu_autopay.main()


if __name__ == "__main__":
    raise SystemExit(main())
