#!/usr/bin/env python3
"""End-to-end smoke check for the deployed local WQPU contracts."""

from __future__ import print_function

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wqpu_chain import RegistryClient  # noqa: E402
from devnet import DEFAULT_PRIVATE_KEY, STATE, run  # noqa: E402


def main():
    state = json.loads(STATE.read_text())
    rpc_url = state["rpc_url"]
    registry = state["registry"]
    endpoint = "127.0.0.1:7443"
    fingerprint = "0x" + "12" * 32
    capacity = 32768
    load_bps = 1250

    run([
        "cast", "send", registry,
        "announce(string,bytes32,uint64,uint16)",
        endpoint, fingerprint, str(capacity), str(load_bps),
        "--rpc-url", rpc_url,
        "--private-key", DEFAULT_PRIVATE_KEY,
    ])

    client = RegistryClient(rpc_url=rpc_url, registry=registry)
    actual_chain = client.chain_id()
    expected_chain = state["chain_id"].lower()
    if actual_chain != expected_chain:
        raise RuntimeError("chain mismatch: {} != {}".format(actual_chain, expected_chain))

    price = client.global_price()
    if price != int(state["price_per_million_units"]):
        raise RuntimeError("price mismatch")

    nodes = client.discover(max_age=0)
    match = next((node for node in nodes if node["endpoint"] == endpoint), None)
    if not match:
        raise RuntimeError("registered node was not returned by wqpu_chain.py")
    if match["fingerprint"].lower() != fingerprint.lower():
        raise RuntimeError("TLS fingerprint mismatch")
    if match["capacity"] != capacity or match["load_bps"] != load_bps:
        raise RuntimeError("capacity/load ABI decoding mismatch")

    print("WQPU devnet registry round-trip OK: {}".format(match["wallet"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
