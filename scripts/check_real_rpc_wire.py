#!/usr/bin/env python3
"""Fast real-wire probe for pinned llama.cpp RPC through WQPU.

Exercises the exact b10456 initialization sequence needed before model loading:
HELLO -> DEVICE_COUNT -> GET_DEVICE_MEMORY. Runs once directly against the real
`ggml-rpc-server`, then byte-for-byte through the authenticated WQPU relay tunnel.
"""
from __future__ import print_function

import asyncio
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_two_client_relay as common  # noqa: E402
import wqpu  # noqa: E402
import wqpu_attestation  # noqa: E402
import wqpu_autopay  # noqa: E402
import wqpu_chain  # noqa: E402
import wqpu_network_guard  # noqa: E402
import wqpu_public_config  # noqa: E402
import wqpu_public_security  # noqa: E402
import wqpu_runtime as runtime  # noqa: E402
import wqpu_runtime_pin  # noqa: E402
from wqpu_chain import RegistryClient  # noqa: E402

STACK = ROOT / ".wqpu-testnet"
WIRE_HOME = STACK / "real-wire-e2e"
RPC_CMD_GET_DEVICE_MEMORY = 11
RPC_CMD_HELLO = 14
RPC_CMD_DEVICE_COUNT = 15
CAPS_SIZE = 24
HELLO_RSP_SIZE = 28


def start_process(command, logfile):
    WIRE_HOME.mkdir(parents=True, exist_ok=True)
    handle = (WIRE_HOME / logfile).open("a", encoding="utf-8")
    env = os.environ.copy()
    env["GGML_RPC_DEBUG"] = "1"
    return subprocess.Popen(
        [str(x) for x in command], stdout=handle, stderr=subprocess.STDOUT,
        cwd=str(ROOT), env=env,
    )


def stop_process(proc):
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(5)


async def wait_tcp(port, proc, timeout=60):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("ggml-rpc-server exited before wire probe")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", int(port))
            await wqpu.close_writer(writer)
            return
        except Exception:
            await asyncio.sleep(0.2)
    raise RuntimeError("ggml-rpc-server did not listen")


def request_frame(command, payload=b""):
    return bytes([int(command)]) + struct.pack("<Q", len(payload)) + payload


async def response(reader, expected_size, label):
    raw_size = await asyncio.wait_for(reader.readexactly(8), 10)
    size = struct.unpack("<Q", raw_size)[0]
    if size != int(expected_size):
        raise RuntimeError("{} response size {} != {}".format(label, size, expected_size))
    return await asyncio.wait_for(reader.readexactly(size), 10)


async def probe(reader, writer, label):
    # Zero caps deliberately force plain TCP even if a build has optional RDMA support.
    writer.write(request_frame(RPC_CMD_HELLO, b"\x00" * CAPS_SIZE))
    await writer.drain()
    hello = await response(reader, HELLO_RSP_SIZE, label + " HELLO")
    major, minor, patch = hello[0], hello[1], hello[2]

    writer.write(request_frame(RPC_CMD_DEVICE_COUNT))
    await writer.drain()
    count = struct.unpack("<I", await response(reader, 4, label + " DEVICE_COUNT"))[0]
    if count <= 0:
        raise RuntimeError("{} exposes no RPC devices".format(label))

    writer.write(request_frame(RPC_CMD_GET_DEVICE_MEMORY, struct.pack("<I", 0)))
    await writer.drain()
    free_mem, total_mem = struct.unpack(
        "<QQ", await response(reader, 16, label + " GET_DEVICE_MEMORY")
    )
    if total_mem <= 0 or free_mem > total_mem:
        raise RuntimeError("{} returned invalid device memory {}/{}".format(
            label, free_mem, total_mem
        ))
    return {
        "rpc_version": "{}.{}.{}".format(major, minor, patch),
        "device_count": count,
        "free_mem": free_mem,
        "total_mem": total_mem,
        "server_caps_nonzero": any(hello[4:]),
    }


async def check():
    import shutil
    if WIRE_HOME.exists():
        shutil.rmtree(str(WIRE_HOME))
    WIRE_HOME.mkdir(parents=True)
    common.CLIENTS = WIRE_HOME / "identities"

    state = json.loads((STACK / "state.json").read_text())
    raw_config = json.loads((STACK / "network-config.json").read_text())
    config = wqpu_public_config.normalize_public(
        wqpu_chain, raw_config, raw_config["public"]
    )
    wqpu_chain.validate_network_config(raw_config, config)
    rpc_url = str(state["internal_rpc"])
    registry = str(config["registry"]).lower()

    requester_key = common.private_key()
    worker_key = common.private_key()
    requester_wallet = common.wallet_address(requester_key)
    worker_wallet = common.wallet_address(worker_key)
    requester_cert, requester_tls_key, requester_fp = common.make_tls_identity("requester")
    worker_cert, worker_tls_key, worker_fp = common.make_tls_identity("worker")
    common.register(rpc_url, registry, requester_key, requester_wallet, requester_fp, 43001)
    common.register(rpc_url, registry, worker_key, worker_wallet, worker_fp, 43002)

    chain = RegistryClient(rpc_url=rpc_url, registry=registry)
    chain.expected_chain_id = str(config["chain_id"]).lower()
    chain.network = dict(config)
    requester_record = chain.find_wallet(requester_wallet, 512)
    worker_record = chain.find_wallet(worker_wallet, 512)
    if not requester_record or not worker_record:
        raise RuntimeError("wire-probe identities were not registered")

    wqpu_network_guard.install(runtime)
    wqpu_public_security.install(wqpu_autopay.AutoPayChainMesh)
    relay_peer = dict(config["relays"][0])
    mesh_cfg = {
        "secret": runtime.public_secret(config["chain_id"], registry),
        "peers": [relay_peer],
    }
    requester = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, requester_wallet)
    worker = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, worker_wallet)
    requester.me = "real-wire-requester"
    worker.me = "real-wire-worker"
    requester.identity_cert_path = requester_cert
    requester.identity_key_path = requester_tls_key
    worker.identity_cert_path = worker_cert
    worker.identity_key_path = worker_tls_key
    wqpu_attestation.register_identity(requester.me, requester_cert, requester_tls_key)
    wqpu_attestation.register_identity(worker.me, worker_cert, worker_tls_key)
    for mesh in (requester, worker):
        mesh.chain_nodes[requester_wallet] = requester_record
        mesh.chain_nodes[worker_wallet] = worker_record

    os.environ["WQPU_LLAMA_TAG"] = "b10456"
    wqpu.ensure_runtime = wqpu_runtime_pin.ensure_runtime
    _, rpc_bin, tag = wqpu_runtime_pin.ensure_runtime()
    if tag != "b10456":
        raise RuntimeError("wire probe did not use pinned b10456")

    old_rpc_port = wqpu.RPC_PORT
    worker_port = wqpu.free_port()
    wqpu.RPC_PORT = worker_port
    rpc_proc = start_process([
        rpc_bin, "--host", "127.0.0.1", "--port", str(worker_port),
        "--threads", "2", "--device", "CPU", "--cache",
    ], "ggml-rpc-server.log")
    tunneled_writer = None
    try:
        await wait_tcp(worker_port, rpc_proc)

        direct_reader, direct_writer = await asyncio.open_connection("127.0.0.1", worker_port)
        try:
            direct = await probe(direct_reader, direct_writer, "direct")
        finally:
            await wqpu.close_writer(direct_writer)

        await worker.connect_control(relay_peer)
        await requester.connect_control(relay_peer)
        await common.wait_until(lambda: worker.me in requester.peer_info, 15)
        await common.wait_until(lambda: any(nid == worker.me for nid, _ in requester.peers()), 15)

        tunneled_reader, tunneled_writer = await requester.open_rpc(worker.me)
        request_id = os.urandom(16).hex()
        prelude = {
            "request_id": request_id,
            "requester_node_id": requester.me,
        }
        tunneled_writer.write(
            wqpu_autopay.METER_PRELUDE
            + json.dumps(prelude, separators=(",", ":")).encode()
            + b"\n"
        )
        await tunneled_writer.drain()
        tunneled = await probe(tunneled_reader, tunneled_writer, "wqpu")

        if direct["device_count"] != tunneled["device_count"]:
            raise RuntimeError("WQPU changed RPC device count")
        if direct["total_mem"] != tunneled["total_mem"]:
            raise RuntimeError("WQPU changed RPC device total memory")

        print("WQPU REAL RPC WIRE PROBE OK")
        print(json.dumps({"direct": direct, "wqpu": tunneled}, sort_keys=True))
    finally:
        if tunneled_writer:
            await wqpu.close_writer(tunneled_writer)
        stop_process(rpc_proc)
        wqpu.RPC_PORT = old_rpc_port
        await common.close_mesh(requester)
        await common.close_mesh(worker)
        wqpu_attestation.unregister_identity(requester.me)
        wqpu_attestation.unregister_identity(worker.me)
        for key in (requester_key, worker_key):
            try:
                common.run([
                    "cast", "send", registry, "setOffline()",
                    "--rpc-url", rpc_url, "--private-key", key,
                ])
            except Exception:
                pass


def main():
    return wqpu.run_async(check()) or 0


if __name__ == "__main__":
    raise SystemExit(main())
