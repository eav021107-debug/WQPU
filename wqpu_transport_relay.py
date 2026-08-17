#!/usr/bin/env python3
"""Public WQPU transport relay for NAT/CGNAT peers.

The relay never runs a model and never handles wallet keys or WQPU funds. It accepts
outbound TLS control connections from peers, forwards llama.cpp RPC streams using the
existing Mesh pair protocol, and forwards targeted control messages such as signed
usage attestations and payment vouchers.

For v3 public networks the relay fail-closes before routing: the first hello must carry
the expected network UID and a fresh TLS-key identity proof whose certificate fingerprint
matches the claimed wallet's active WQPURegistry entry.
"""

from __future__ import print_function

import argparse
import asyncio
import json
import os
import signal

import wqpu
from wqpu_chain import load_network_config, normalize_chain_id
from wqpu_node_identity import RegistryIdentityVerifier
from wqpu_runtime import public_secret


FORWARDED_SERVICES = {"payment", "usage_report"}
_IDENTITY_VERIFIERS = {}


def hello_network_uid(hello):
    if not isinstance(hello, dict):
        return ""
    direct = str(hello.get("network_uid") or "").strip().lower()
    if direct:
        return direct
    info = hello.get("info")
    if isinstance(info, dict):
        return str(info.get("network_uid") or "").strip().lower()
    return ""


class _ReplayReader(object):
    """Put one already-read line back in front of an asyncio-like reader."""
    def __init__(self, reader, first_line):
        self.reader = reader
        self.first_line = first_line

    async def readline(self, *args, **kwargs):
        if self.first_line is not None:
            line = self.first_line
            self.first_line = None
            return line
        return await self.reader.readline(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.reader, name)


def registry_identity_verifier(network_uid):
    uid = str(network_uid or "").strip().lower()
    verifier = _IDENTITY_VERIFIERS.get(uid)
    if verifier is None:
        verifier = RegistryIdentityVerifier(uid)
        _IDENTITY_VERIFIERS[uid] = verifier
    return verifier


def expected_network_uid():
    explicit = str(os.environ.get("WQPU_NETWORK_UID") or "").strip().lower()
    if explicit:
        return explicit
    try:
        network = load_network_config()
        return str(network.get("network_uid") or "").strip().lower()
    except Exception:
        return ""


async def _reject_transport(writer, message):
    try:
        writer.write((json.dumps({"type": "error", "error": str(message)}) + "\n").encode())
        await writer.drain()
    except Exception:
        pass
    await wqpu.close_writer(writer)


def _install_network_uid_transport_guard():
    """Guard the current wqpu.Mesh class once, preserving its existing handler."""
    cls = wqpu.Mesh
    if getattr(cls, "_wqpu_transport_identity_guard_installed", False):
        return cls
    original = cls.handle_inbound

    async def guarded(self, reader, writer):
        raw = b""
        try:
            raw = await asyncio.wait_for(reader.readline(), 10)
            if not raw or len(raw) > 256 * 1024:
                raise RuntimeError("invalid WQPU transport hello")
            hello = json.loads(raw.decode("utf-8"))
            expected = expected_network_uid()
            if expected:
                actual = hello_network_uid(hello)
                if actual != expected:
                    raise RuntimeError("WQPU network identity mismatch")
                # Network separation is checked before any Registry lookup/signature work.
                registry_identity_verifier(expected).verify_hello(hello)
            return await original(self, _ReplayReader(reader, raw), writer)
        except Exception as exc:
            await _reject_transport(writer, exc)
            return None

    cls.handle_inbound = guarded
    cls._wqpu_transport_identity_guard_installed = True
    return cls


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
    _install_network_uid_transport_guard()
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
    uid = expected_network_uid()
    if uid:
        print("Network UID: {} (registry-authenticated hellos required)".format(uid))

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
