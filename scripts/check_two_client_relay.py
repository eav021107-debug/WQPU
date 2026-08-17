#!/usr/bin/env python3
"""E2E: distinct wallets/TLS identities route and dual-meter RPC through one relay."""
from __future__ import print_function

import asyncio
import json
import os
import secrets
import shutil
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

import wqpu  # noqa: E402
import wqpu_accounting  # noqa: E402
import wqpu_attestation  # noqa: E402
import wqpu_autopay  # noqa: E402
import wqpu_chain  # noqa: E402
import wqpu_meter  # noqa: E402
import wqpu_network_guard  # noqa: E402
import wqpu_public_config  # noqa: E402
import wqpu_public_security  # noqa: E402
import wqpu_runtime as runtime  # noqa: E402
from wqpu_chain import RegistryClient  # noqa: E402
from wqpu_node_identity import certificate_der, certificate_fingerprint_from_der  # noqa: E402

STACK = ROOT / ".wqpu-testnet"
CLIENTS = STACK / "two-client-e2e"
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if proc.returncode != 0:
        raise RuntimeError("command failed: {}\n{}".format(" ".join(map(str, cmd)), proc.stdout))
    return proc.stdout.strip()


def rpc(url, method, params):
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=raw, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    if body.get("error"):
        raise RuntimeError("RPC {} failed: {}".format(method, body["error"]))
    return body.get("result")


def private_key():
    return "0x{:064x}".format(secrets.randbelow(SECP256K1_N - 1) + 1)


def wallet_address(key):
    out = run(["cast", "wallet", "address", "--private-key", key])
    for token in out.split():
        if token.startswith("0x") and len(token) == 42:
            return token.lower()
    raise RuntimeError("could not derive EVM wallet")


def make_tls_identity(name):
    root = CLIENTS / name
    root.mkdir(parents=True, exist_ok=True)
    cert = root / "cert.pem"
    key = root / "key.pem"
    run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=WQPU-{}".format(name),
    ])
    try:
        key.chmod(0o600)
    except Exception:
        pass
    fp = certificate_fingerprint_from_der(certificate_der(cert))
    return cert, key, fp


def register(rpc_url, registry, wallet_key, wallet, fingerprint, port):
    rpc(rpc_url, "anvil_setBalance", [wallet, hex(10 * 10 ** 18)])
    run([
        "cast", "send", registry,
        "announce(string,bytes32,uint64,uint16)",
        "127.0.0.1:{}".format(port), "0x" + fingerprint, "32768", "0",
        "--rpc-url", rpc_url, "--private-key", wallet_key,
    ])


def frame(command, payload=b""):
    return bytes([command]) + len(payload).to_bytes(8, "little") + payload


def rpc_tensor(tensor_id, ne=(1, 1, 1, 1), op=2):
    raw = bytearray(wqpu_meter.RPC_TENSOR_SIZE)
    struct.pack_into("<Q", raw, 0, tensor_id)
    struct.pack_into("<I", raw, 8, 0)
    struct.pack_into("<Q", raw, 12, 0)
    struct.pack_into("<4I", raw, 20, *ne)
    struct.pack_into("<4I", raw, 36, 1, 1, 1, 1)
    struct.pack_into("<I", raw, 52, op)
    return bytes(raw)


def graph_payload():
    tensor = rpc_tensor(7, ne=(2, 3, 4, 1), op=2)
    return b"".join([
        (0).to_bytes(4, "little"),
        (1).to_bytes(4, "little"),
        (7).to_bytes(8, "little"),
        (1).to_bytes(4, "little"),
        tensor,
    ])


async def echo_handler(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    finally:
        await wqpu.close_writer(writer)


async def wait_until(predicate, timeout=10):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("two-client WQPU topology did not converge")


async def close_mesh(mesh):
    mesh.stop.set()
    for ctrl in list(mesh.outbound.values()):
        await wqpu.close_writer(ctrl.writer)
    for ctrl in list(mesh.controls.values()):
        await wqpu.close_writer(ctrl.writer)


async def check():
    if CLIENTS.exists():
        shutil.rmtree(str(CLIENTS))
    CLIENTS.mkdir(parents=True)

    state = json.loads((STACK / "state.json").read_text())
    raw_config = json.loads((STACK / "network-config.json").read_text())
    config = wqpu_public_config.normalize_public(
        wqpu_chain, raw_config, raw_config["public"]
    )
    wqpu_chain.validate_network_config(raw_config, config)
    rpc_url = str(state["internal_rpc"])
    registry = str(config["registry"]).lower()

    requester_key = private_key()
    worker_key = private_key()
    requester_wallet = wallet_address(requester_key)
    worker_wallet = wallet_address(worker_key)
    requester_cert, requester_tls_key, requester_fp = make_tls_identity("requester")
    worker_cert, worker_tls_key, worker_fp = make_tls_identity("worker")

    register(rpc_url, registry, requester_key, requester_wallet, requester_fp, 41001)
    register(rpc_url, registry, worker_key, worker_wallet, worker_fp, 41002)

    chain = RegistryClient(rpc_url=rpc_url, registry=registry)
    chain.expected_chain_id = str(config["chain_id"]).lower()
    chain.network = dict(config)
    requester_record = chain.find_wallet(requester_wallet, 512)
    worker_record = chain.find_wallet(worker_wallet, 512)
    if not requester_record or not worker_record:
        raise RuntimeError("both test wallets were not registered")

    wqpu_network_guard.install(runtime)
    wqpu_public_security.install(wqpu_autopay.AutoPayChainMesh)
    relay_peer = dict(config["relays"][0])
    secret = runtime.public_secret(config["chain_id"], registry)
    mesh_cfg = {"secret": secret, "peers": [relay_peer]}

    requester = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, requester_wallet)
    worker = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, worker_wallet)
    requester.me = "two-client-requester"
    worker.me = "two-client-worker"
    requester.identity_cert_path = requester_cert
    requester.identity_key_path = requester_tls_key
    worker.identity_cert_path = worker_cert
    worker.identity_key_path = worker_tls_key
    # Worker usage attestations resolve by provider_node_id. Normal one-node-per-process
    # installs fall back to wqpu.CERT/KEY and never need this registration.
    wqpu_attestation.register_identity(requester.me, requester_cert, requester_tls_key)
    wqpu_attestation.register_identity(worker.me, worker_cert, worker_tls_key)
    for mesh in (requester, worker):
        mesh.chain_nodes[requester_wallet] = requester_record
        mesh.chain_nodes[worker_wallet] = worker_record

    echo = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
    old_rpc_port = wqpu.RPC_PORT
    wqpu.RPC_PORT = echo.sockets[0].getsockname()[1]
    proxy = None
    client_writer = None
    request_id = secrets.token_hex(16)
    try:
        await worker.connect_control(relay_peer)
        await requester.connect_control(relay_peer)
        await wait_until(lambda: worker.me in requester.peer_info)
        await wait_until(lambda: any(nid == worker.me for nid, _ in requester.peers()))
        await wait_until(lambda: requester.me in worker.peer_info)

        seen = requester.peer_info.get(worker.me) or {}
        if str(seen.get("wallet") or "").lower() != worker_wallet:
            raise RuntimeError("requester associated worker node with wrong wallet")
        if str(seen.get("fingerprint") or "").lower().replace("0x", "") != worker_fp:
            raise RuntimeError("requester associated worker with wrong TLS fingerprint")
        if requester_wallet == worker_wallet or requester_fp == worker_fp:
            raise RuntimeError("two-client test did not create distinct identities")

        requester.current_request_id = request_id
        requester.provider_usage_reports.pop(request_id, None)
        requester.begin_usage()
        proxy = await asyncio.start_server(
            lambda r, w: requester.proxy_handler(worker.me, r, w),
            "127.0.0.1", 0,
        )
        reader, client_writer = await asyncio.open_connection(
            "127.0.0.1", proxy.sockets[0].getsockname()[1]
        )
        payload = frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph_payload())
        client_writer.write(payload)
        await client_writer.drain()
        echoed = await asyncio.wait_for(reader.readexactly(len(payload)), 10)
        if echoed != payload:
            raise RuntimeError("authenticated dual-meter RPC echo mismatch")
        await wqpu.close_writer(client_writer)
        client_writer = None
        await wqpu.close_server(proxy)
        proxy = None

        await requester.wait_provider_reports([worker.me], request_id, 10)
        snapshot = requester.end_usage()
        requester_stats = snapshot.get(worker.me)
        report = (requester.provider_usage_reports.get(request_id) or {}).get(worker.me)
        if not requester_stats or not report:
            raise RuntimeError("dual-meter worker report was not received")
        worker_stats = report.get("rpc") or {}
        if not wqpu_accounting.meters_match(requester_stats, worker_stats):
            raise RuntimeError("distinct requester and worker meters disagreed")
        if int(requester_stats.get("estimated_scalar_ops") or 0) != 24:
            raise RuntimeError("unexpected synthetic compute estimate")
        if wqpu_attestation.certificate_fingerprint(report.get("certificate")) != worker_fp:
            raise RuntimeError("usage report was not signed by registered worker TLS identity")
        if str(report.get("provider_wallet") or "").lower() != worker_wallet:
            raise RuntimeError("usage report claimed wrong provider wallet")

        print("WQPU two-client authenticated dual-meter E2E OK")
        print("requester={} worker={} scalar_ops=24 distinct_tls=true".format(
            requester_wallet, worker_wallet
        ))
    finally:
        requester.current_request_id = None
        if client_writer:
            await wqpu.close_writer(client_writer)
        if proxy:
            await wqpu.close_server(proxy)
        wqpu.RPC_PORT = old_rpc_port
        await close_mesh(requester)
        await close_mesh(worker)
        await wqpu.close_server(echo)
        wqpu_attestation.unregister_identity(requester.me)
        wqpu_attestation.unregister_identity(worker.me)
        for key, wallet in ((requester_key, requester_wallet), (worker_key, worker_wallet)):
            try:
                run([
                    "cast", "send", registry, "setOffline()",
                    "--rpc-url", rpc_url, "--private-key", key,
                ])
            except Exception:
                pass


def main():
    return wqpu.run_async(check()) or 0


if __name__ == "__main__":
    raise SystemExit(main())
