#!/usr/bin/env python3
"""Public WQPU transport relay for NAT/CGNAT peers.

The relay never runs a model and never handles wallet keys or WQPU funds. It accepts
outbound TLS control connections from peers, forwards llama.cpp RPC streams using the
existing Mesh pair protocol, and forwards targeted control messages such as signed
usage attestations and payment vouchers. Worker identity is still checked end-to-end
by clients against the on-chain wallet/TLS fingerprint registry.
"""

from __future__ import print_function

import argparse
import asyncio
import os
import signal

import wqpu
from wqpu_chain import load_network_config, normalize_chain_id
from wqpu_runtime import public_secret


FORWARDED_SERVICES = {"payment", "usage_report"}


class TransportRelayMesh(wqpu.Mesh):
    async def handle_open_request(self, msg, via=None):
        service = str(msg.get("service") or "")
        if service in FORWARDED_SERVICES:
            target = str(msg.get("target") or "")
            if not target or target == self.me:
                return
            ctrl = self.controls.get(target)
            if not ctrl:
                return
            await self.send(ctrl, msg)
            return
        await super(TransportRelayMesh, self).handle_open_request(msg, via=via)


def relay_network():
    network = load_network_config()
    chain_id = os.environ.get("WQPU_CHAIN_ID") or network.get("chain_id")
    registry = os.environ.get("WQPU_REGISTRY") or network.get("registry")
    if chain_id in (None, "") or not registry:
        raise RuntimeError("relay needs published WQPU chain_id and registry")
    chain_id = normalize_chain_id(chain_id)
    registry = str(registry).strip().lower()
    return chain_id, registry


async def run(host="0.0.0.0", port=None):
    wqpu.ensure_cert()
    chain_id, registry = relay_network()
    cfg = {
        "secret": public_secret(chain_id, registry),
        "peers": [],
        "mode": "public-transport-relay",
    }
    mesh = TransportRelayMesh(cfg)
    listen_port = int(port or os.environ.get("WQPU_RELAY_PORT", str(wqpu.PORT)))
    mesh.server = await asyncio.start_server(
        mesh.handle_inbound,
        host,
        listen_port,
        ssl=wqpu.server_ssl(),
    )
    actual = mesh.server.sockets[0].getsockname()[1]
    print("WQPU transport relay online on {}:{}".format(host, actual))
    print("TLS fingerprint: {}".format(mesh.fp))

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, mesh.stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    try:
        await mesh.stop.wait()
    finally:
        await wqpu.close_server(mesh.server)
        for ctrl in list(mesh.controls.values()):
            await wqpu.close_writer(ctrl.writer)


def main():
    parser = argparse.ArgumentParser(prog="wqpu-transport-relay")
    parser.add_argument("--host", default=os.environ.get("WQPU_RELAY_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    return wqpu.run_async(run(args.host, args.port)) or 0


if __name__ == "__main__":
    raise SystemExit(main())
