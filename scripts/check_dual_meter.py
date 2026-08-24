#!/usr/bin/env python3
"""End-to-end dual-meter smoke test over the public transport relay."""

from __future__ import print_function

import asyncio
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wqpu
import wqpu_accounting
import wqpu_autopay
import wqpu_meter
from wqpu_transport_relay import TransportRelayMesh

REQUESTER_WALLET = "0x" + "11" * 20
WORKER_WALLET = "0x" + "22" * 20
REQUEST_ID = "ab" * 16


class FakeChain(object):
    def __init__(self, relay_peer):
        # Mirror the production path: AutoPayChainMesh learns public relays from
        # the published blockchain/network config, not from an implicit cache.
        self.network = {"relays": [dict(relay_peer)]}


def frame(cmd, payload=b""):
    return bytes([cmd]) + len(payload).to_bytes(8, "little") + payload


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
    raise RuntimeError("dual-meter topology did not converge")


async def close_mesh(mesh):
    mesh.stop.set()
    for ctrl in list(mesh.outbound.values()):
        await wqpu.close_writer(ctrl.writer)
    for ctrl in list(mesh.controls.values()):
        await wqpu.close_writer(ctrl.writer)
    if mesh.server:
        await wqpu.close_server(mesh.server)


async def main_async():
    wqpu.ensure_cert()
    secret = "dual-meter-test-secret"
    fp = wqpu.cert_fingerprint()

    relay = TransportRelayMesh({"secret": secret, "peers": []})
    relay.me = "relay-node"
    relay.server = await asyncio.start_server(
        relay.handle_inbound, "127.0.0.1", 0, ssl=wqpu.server_ssl()
    )
    relay_peer = {
        "host": "127.0.0.1",
        "port": relay.server.sockets[0].getsockname()[1],
        "fingerprint": fp,
    }

    rpc_server = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
    old_rpc_port = wqpu.RPC_PORT
    wqpu.RPC_PORT = rpc_server.sockets[0].getsockname()[1]

    requester = wqpu_autopay.AutoPayChainMesh(
        {"secret": secret, "peers": []}, FakeChain(relay_peer), REQUESTER_WALLET
    )
    requester.me = "requester-node"
    worker = wqpu_autopay.AutoPayChainMesh(
        {"secret": secret, "peers": []}, FakeChain(relay_peer), WORKER_WALLET
    )
    worker.me = "worker-node"

    requester.chain_nodes[WORKER_WALLET] = {"fingerprint": fp}
    worker.chain_nodes[REQUESTER_WALLET] = {"fingerprint": fp}

    try:
        await worker.connect_control(relay_peer)
        await requester.connect_control(relay_peer)
        await wait_until(lambda: worker.me in relay.controls and requester.me in relay.controls)
        await wait_until(lambda: bool(requester.routes.get(worker.me)))
        await wait_until(lambda: bool(worker.routes.get(requester.me)))

        requester.current_request_id = REQUEST_ID
        requester.provider_usage_reports.pop(REQUEST_ID, None)
        requester.begin_usage()

        proxy = await asyncio.start_server(
            lambda r, w: requester.proxy_handler(worker.me, r, w),
            "127.0.0.1",
            0,
        )
        reader, writer = await asyncio.open_connection(
            "127.0.0.1", proxy.sockets[0].getsockname()[1]
        )
        payload = frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph_payload())
        writer.write(payload)
        await writer.drain()
        echoed = await asyncio.wait_for(reader.readexactly(len(payload)), 10)
        if echoed != payload:
            raise RuntimeError("worker RPC echo mismatch")
        await wqpu.close_writer(writer)
        await wqpu.close_server(proxy)

        await requester.wait_provider_reports([worker.me], REQUEST_ID, 10)
        requester_stats = requester.end_usage()[worker.me]
        report = (requester.provider_usage_reports.get(REQUEST_ID) or {}).get(worker.me)
        if not report:
            raise RuntimeError("signed worker usage report was not received")
        worker_stats = report.get("rpc") or {}
        if not wqpu_accounting.meters_match(requester_stats, worker_stats):
            raise RuntimeError("requester and worker meters disagreed")
        if int(requester_stats.get("estimated_scalar_ops") or 0) != 24:
            raise RuntimeError("unexpected synthetic compute estimate")

        print("WQPU dual-meter round-trip OK: signed worker report matched 24 scalar ops")
    finally:
        requester.current_request_id = None
        wqpu.RPC_PORT = old_rpc_port
        await close_mesh(requester)
        await close_mesh(worker)
        await close_mesh(relay)
        await wqpu.close_server(rpc_server)


def main():
    return wqpu.run_async(main_async()) or 0


if __name__ == "__main__":
    raise SystemExit(main())
