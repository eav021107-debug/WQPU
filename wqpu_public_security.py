#!/usr/bin/env python3
"""Public WQPU transport/status security hooks.

This module is intentionally separate from the legacy mesh. For a v3 public network it:
- publishes the deterministic network UID;
- signs short-lived load/capacity status with the node TLS private key;
- verifies received status against the worker TLS fingerprint registered on-chain;
- signs control/dial/accept transport hellos for relay-side Registry authentication;
- sends signed status heartbeats over existing outbound relay control connections;
- marks NAT/relay workers schedulable only after end-to-end signed Registry verification.

Legacy/v1/v2 networks without a network_uid keep the existing transport behavior.
"""
from __future__ import print_function

import asyncio
import hashlib
import json

import wqpu
from wqpu_chain import PUBLIC_PROTOCOL
from wqpu_network_guard import peer_matches_network
from wqpu_node_identity import build_identity_proof
from wqpu_node_status import build_status_attestation, verify_status_attestation


def _network_uid(mesh):
    chain = getattr(mesh, "chain", None)
    network = getattr(chain, "network", {}) or {}
    return str(network.get("network_uid") or "").strip().lower()


def _wallet(mesh):
    return str(getattr(mesh, "wallet", "") or "").strip().lower()


def _hello_identity(mesh, role):
    uid = _network_uid(mesh)
    wallet = _wallet(mesh)
    if not uid:
        return {}
    if not wallet:
        raise RuntimeError("public WQPU transport requires a connected wallet")
    return {
        "network_uid": uid,
        "wallet": wallet,
        "identity_proof": build_identity_proof(uid, mesh.me, wallet, role),
    }


async def _open_tls_peer(mesh, peer):
    helper = getattr(mesh, "_open_tls_peer", None)
    if helper is not None:
        return await helper(peer)
    host, port = peer["host"], int(peer.get("port", wqpu.PORT))
    reader, writer = await asyncio.open_connection(
        host, port, ssl=wqpu.client_ssl(), server_hostname=host
    )
    sslobj = writer.get_extra_info("ssl_object")
    cert = sslobj.getpeercert(binary_form=True) if sslobj else b""
    actual = hashlib.sha256(cert).hexdigest()
    expected = str(peer.get("fingerprint") or "").lower().replace("0x", "")
    if expected and actual.lower() != expected:
        await wqpu.close_writer(writer)
        raise RuntimeError("fingerprint mismatch for {}".format(wqpu.peer_key(host, port)))
    return reader, writer


def _hub_for_route(mesh, route_key):
    resolver = getattr(mesh, "_hub_for_route", None)
    if resolver is not None:
        return resolver(route_key)
    peers = list(mesh.cfg.get("peers") or []) + list(wqpu.load_peer_cache().values())
    for peer in peers:
        if wqpu.peer_key(peer["host"], peer.get("port", wqpu.PORT)) == route_key:
            return peer
    return None


def install(cls):
    """Patch one public ChainMesh class once."""
    if getattr(cls, "_wqpu_public_security_installed", False):
        return cls

    original_my_info = cls.my_info
    original_merge_nodes = cls.merge_nodes
    original_broadcast_nodes = cls.broadcast_nodes

    def my_info(self):
        info = dict(original_my_info(self))
        uid = _network_uid(self)
        if not uid:
            return info
        info["network"] = PUBLIC_PROTOCOL
        info["network_uid"] = uid
        info["status_attestation"] = build_status_attestation(info)
        return info

    def merge_nodes(self, route_key, nodes):
        uid = _network_uid(self)
        verified_ids = set()
        if uid:
            accepted = []
            chain_nodes = getattr(self, "chain_nodes", {}) or {}
            for node in nodes or []:
                if not peer_matches_network(uid, node):
                    continue
                wallet = str(node.get("wallet") or "").lower()
                registered = chain_nodes.get(wallet) or {}
                expected_fp = str(registered.get("fingerprint") or "").lower().replace("0x", "")
                if not expected_fp:
                    continue
                try:
                    verify_status_attestation(node, expected_fp, uid)
                except Exception:
                    continue
                accepted.append(node)
                node_id = str(node.get("node_id") or "")
                if node_id:
                    verified_ids.add(node_id)
            nodes = accepted
        result = original_merge_nodes(self, route_key, nodes)
        # Direct peers used to be verified by route address. For a relay route, the route
        # address belongs to the relay, so the end-to-end signed Registry proof above is
        # the stronger criterion and is what makes the worker schedulable.
        verified = getattr(self, "verified_node_ids", None)
        if verified is not None:
            verified.update(verified_ids)
        return result

    async def broadcast_nodes(self):
        await original_broadcast_nodes(self)
        if not _network_uid(self):
            return
        info = self.my_info()
        msg = {
            "type": "open",
            "service": "status",
            "source": self.me,
            "info": info,
        }
        for ctrl in list(self.outbound.values()):
            try:
                await self.send(ctrl, msg)
            except Exception:
                pass

    async def connect_control(self, peer):
        host, port = peer["host"], int(peer.get("port", wqpu.PORT))
        key = wqpu.peer_key(host, port)
        if key in self.outbound:
            return
        reader, writer = await _open_tls_peer(self, peer)
        hello = {
            "role": "control",
            "secret": self.secret,
            "node_id": self.me,
            "info": self.my_info(),
        }
        hello.update(_hello_identity(self, "control"))
        writer.write((json.dumps(hello, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        ctrl = wqpu.Control(key, peer, reader, writer)
        self.outbound[key] = ctrl

        async def read_loop():
            try:
                while not self.stop.is_set():
                    line = await reader.readline()
                    if not line:
                        break
                    msg = json.loads(line.decode())
                    if msg.get("type") == "nodes":
                        self.merge_nodes(key, msg.get("nodes") or [])
                    elif msg.get("type") == "open":
                        await self.handle_open_request(msg, via=peer)
                    elif msg.get("type") == "pong":
                        pass
            finally:
                self.outbound.pop(key, None)
                for routes in self.routes.values():
                    routes.discard(key)
                await wqpu.close_writer(writer)

        asyncio.ensure_future(read_loop())

    async def open_accept(self, hub, sid):
        reader, writer = await _open_tls_peer(self, hub)
        hello = {
            "role": "accept",
            "secret": self.secret,
            "node_id": self.me,
            "stream": sid,
        }
        hello.update(_hello_identity(self, "accept"))
        writer.write((json.dumps(hello, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 15)
        if line != b"WQPU-READY\n":
            await wqpu.close_writer(writer)
            raise RuntimeError("relay accept failed")
        return reader, writer

    async def open_rpc(self, target):
        for route_key in list(self.routes.get(target) or []):
            hub = _hub_for_route(self, route_key)
            if not hub:
                continue
            try:
                reader, writer = await _open_tls_peer(self, hub)
                hello = {
                    "role": "dial",
                    "secret": self.secret,
                    "node_id": self.me,
                    "target": target,
                }
                hello.update(_hello_identity(self, "dial"))
                writer.write((json.dumps(hello, separators=(",", ":")) + "\n").encode())
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), 15)
                if line == b"WQPU-READY\n":
                    return reader, writer
                await wqpu.close_writer(writer)
            except Exception:
                pass
        raise RuntimeError("no route to peer")

    cls.my_info = my_info
    cls.merge_nodes = merge_nodes
    cls.broadcast_nodes = broadcast_nodes
    cls.connect_control = connect_control
    cls.open_accept = open_accept
    cls.open_rpc = open_rpc
    cls._wqpu_public_security_installed = True
    return cls
