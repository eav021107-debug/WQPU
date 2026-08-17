#!/usr/bin/env python3
"""E2E: signed dynamic worker load traverses the public transport relay.

The worker and requester use the same test wallet/certificate only to keep this transport
check dependency-free. Registry still binds that wallet to the TLS fingerprint. The test
proves a valid load update reaches requester scheduling while a tampered load using an old
signature is rejected.
"""
from __future__ import print_function

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

# Must be set before importing wqpu because its HOME/CERT/KEY paths are module globals.
TEST_HOME = ROOT / ".wqpu-testnet" / "status-e2e-node"
os.environ["WQPU_HOME"] = str(TEST_HOME)

import wqpu  # noqa: E402
import wqpu_network_guard  # noqa: E402
import wqpu_runtime as runtime  # noqa: E402
from wqpu_chain import RegistryClient  # noqa: E402

STACK = ROOT / ".wqpu-testnet"


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if proc.returncode != 0:
        raise RuntimeError("command failed: {}\n{}".format(" ".join(cmd), proc.stdout))
    return proc.stdout


async def wait_until(predicate, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("signed status relay topology did not converge")


async def close_mesh(mesh):
    mesh.stop.set()
    for ctrl in list(mesh.outbound.values()):
        await wqpu.close_writer(ctrl.writer)
    for ctrl in list(mesh.controls.values()):
        await wqpu.close_writer(ctrl.writer)


async def check():
    state = json.loads((STACK / "state.json").read_text())
    config = json.loads((STACK / "network-config.json").read_text())["public"]
    operator = json.loads((STACK / "operator.json").read_text())
    wallet = str(operator["address"]).lower()
    private_key = str(operator["private_key"])
    rpc = str(state["internal_rpc"])
    registry = str(config["registry"]).lower()

    wqpu.ensure_cert()
    fingerprint = wqpu.cert_fingerprint()
    run([
        "cast", "send", registry,
        "announce(string,bytes32,uint64,uint16)",
        "127.0.0.1:7443", "0x" + fingerprint, "32768", "0",
        "--rpc-url", rpc, "--private-key", private_key,
    ])

    chain = RegistryClient(rpc_url=rpc, registry=registry)
    chain.expected_chain_id = str(config["chain_id"]).lower()
    chain.network = dict(config)
    registry_node = chain.find_wallet(wallet, 512)
    if not registry_node or not registry_node.get("active"):
        raise RuntimeError("test wallet did not appear as active Registry node")

    secret = runtime.public_secret(config["chain_id"], registry)
    relay_peer = dict(config["relays"][0])
    mesh_cfg = {"secret": secret, "peers": [relay_peer]}
    wqpu_network_guard.install(runtime)

    worker = runtime.ChainMesh(mesh_cfg, chain, wallet)
    requester = runtime.ChainMesh(mesh_cfg, chain, wallet)
    worker.me = "signed-status-worker"
    requester.me = "signed-status-requester"

    # Same wallet is intentional in this focused transport E2E. Requester needs the
    # on-chain fingerprint snapshot to verify the worker's signed dynamic status.
    requester.chain_nodes[wallet] = registry_node
    worker.chain_nodes[wallet] = registry_node

    old_load = runtime.system_load_bps
    try:
        runtime.system_load_bps = lambda: 500
        await worker.connect_control(relay_peer)
        await requester.connect_control(relay_peer)
        await wait_until(lambda: worker.me in requester.peer_info)
        await wait_until(
            lambda: int((requester.peer_info.get(worker.me) or {}).get("load_bps") or -1) == 500
        )
        if not any(node_id == worker.me for node_id, _ in requester.peers()):
            raise RuntimeError("valid signed worker was not schedulable")

        # The status signer caches unchanged values briefly, so move beyond that window.
        await asyncio.sleep(2.1)
        runtime.system_load_bps = lambda: 8500
        await worker.broadcast_nodes()
        await wait_until(
            lambda: int((requester.peer_info.get(worker.me) or {}).get("load_bps") or -1) == 8500
        )
        if not any(node_id == worker.me for node_id, _ in requester.peers()):
            raise RuntimeError("fresh signed worker status became unschedulable")

        # Keep the valid signature but lie about the advertised load. Relay/requester
        # must reject this because public fields no longer match the signed status body.
        info = worker.my_info()
        tampered = dict(info)
        tampered["load_bps"] = 0
        for ctrl in list(worker.outbound.values()):
            await worker.send(ctrl, {"type": "status", "info": tampered})
        await asyncio.sleep(0.5)
        if int(requester.peer_info[worker.me]["load_bps"]) != 8500:
            raise RuntimeError("tampered dynamic load crossed the signed relay boundary")

        print("WQPU signed dynamic status relay E2E OK")
    finally:
        runtime.system_load_bps = old_load
        await close_mesh(requester)
        await close_mesh(worker)
        try:
            run([
                "cast", "send", registry, "setOffline()",
                "--rpc-url", rpc, "--private-key", private_key,
            ])
        except Exception:
            pass


def main():
    return wqpu.run_async(check()) or 0


if __name__ == "__main__":
    raise SystemExit(main())
