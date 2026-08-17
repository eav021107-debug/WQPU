#!/usr/bin/env python3
"""Three-node transport smoke test: requester -> public relay -> outbound-only worker.

The worker never listens on a public WQPU port. It opens only an outbound control
connection to the relay. The requester then reaches the worker's localhost RPC service
through that relay and verifies a real bidirectional byte stream.
"""

from __future__ import print_function

import asyncio
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wqpu  # noqa: E402


async def wait_until(predicate, timeout=10):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise RuntimeError("NAT relay topology did not converge")


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
    secret = secrets.token_urlsafe(32)

    # Public relay: the only WQPU listener needed by the topology.
    relay = wqpu.Mesh({"secret": secret, "peers": []})
    relay.me = "relay-node"
    relay.server = await asyncio.start_server(
        relay.handle_inbound,
        "127.0.0.1",
        0,
        ssl=wqpu.server_ssl(),
    )
    relay_port = relay.server.sockets[0].getsockname()[1]
    relay_peer = {
        "host": "127.0.0.1",
        "port": relay_port,
        "fingerprint": wqpu.cert_fingerprint(),
    }

    # The fake llama.cpp RPC worker is localhost-only. This represents a home/CGNAT
    # machine: it contributes compute but exposes no inbound public WQPU listener.
    echo_server = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
    old_rpc_port = wqpu.RPC_PORT
    wqpu.RPC_PORT = echo_server.sockets[0].getsockname()[1]

    worker = wqpu.Mesh({"secret": secret, "peers": [relay_peer]})
    worker.me = "nat-worker"
    requester = wqpu.Mesh({"secret": secret, "peers": [relay_peer]})
    requester.me = "requester-node"

    try:
        await worker.connect_control(relay_peer)
        await requester.connect_control(relay_peer)

        await wait_until(lambda: worker.me in relay.controls and requester.me in relay.controls)
        await wait_until(lambda: bool(requester.routes.get(worker.me)))

        reader, writer = await requester.open_rpc(worker.me)
        payload = b"WQPU-NAT-RELAY-ROUNDTRIP\x00\x01\x02"
        writer.write(payload)
        await writer.drain()
        echoed = await asyncio.wait_for(reader.readexactly(len(payload)), 10)
        if echoed != payload:
            raise RuntimeError("relay corrupted RPC byte stream")
        await wqpu.close_writer(writer)

        # Critical property: the worker had no public listener at all.
        if worker.server is not None:
            raise RuntimeError("worker unexpectedly opened an inbound listener")
        print("WQPU NAT relay round-trip OK: requester -> relay -> outbound-only worker")
    finally:
        wqpu.RPC_PORT = old_rpc_port
        await close_mesh(requester)
        await close_mesh(worker)
        await close_mesh(relay)
        await wqpu.close_server(echo_server)


def main():
    return wqpu.run_async(main_async()) or 0


if __name__ == "__main__":
    raise SystemExit(main())
