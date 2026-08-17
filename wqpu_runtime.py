#!/usr/bin/env python3
"""WQPU public-chain runtime.

Public mode uses the blockchain registry only for discovery/identity/price.
Private keys never enter WQPU: node registration is submitted by an injected
browser wallet through wqpu_wallet.py.
"""

from __future__ import print_function

import argparse
import asyncio
import hashlib
import json
import os
import signal
import socket
import sys
import time

import wqpu
from wqpu_chain import RegistryClient, parse_endpoint
from wqpu_wallet import connect_wallet

VERSION = "0.6.0-dev"
STATE_FILE = wqpu.HOME / "chain.json"
PUBLIC_PROTOCOL = "wqpu-public-v1"


def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state):
    wqpu.ensure_home()
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def local_endpoint():
    configured = os.environ.get("WQPU_PUBLIC_ENDPOINT", "").strip()
    if configured:
        parse_endpoint(configured)
        return configured
    host = ""
    try:
        host = socket.gethostbyname(socket.gethostname())
    except Exception:
        pass
    if not host or host.startswith("127."):
        host = socket.getfqdn() or socket.gethostname()
    return "{}:{}".format(host, wqpu.PORT)


def capacity_units():
    return max(1, int(wqpu.total_ram_mb() or 1))


def system_load_bps():
    try:
        load = os.getloadavg()[0]
        cpus = max(1, os.cpu_count() or 1)
        return max(0, min(10000, int((load / cpus) * 10000)))
    except Exception:
        return 0


def public_secret(chain_id, registry):
    material = "{}|{}|{}".format(PUBLIC_PROTOCOL, chain_id.lower(), registry.lower())
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def peer_from_registry(node):
    host, port = parse_endpoint(node["endpoint"])
    fp = str(node["fingerprint"] or "").lower()
    if fp.startswith("0x"):
        fp = fp[2:]
    return {
        "host": host,
        "port": port,
        "fingerprint": fp,
        "wallet": node.get("wallet"),
        "capacity": int(node.get("capacity") or 0),
        "load_bps": int(node.get("load_bps") or 0),
        "updated_at": int(node.get("updated_at") or 0),
    }


class ChainMesh(wqpu.Mesh):
    def __init__(self, cfg, chain, wallet):
        super(ChainMesh, self).__init__(cfg)
        self.chain = chain
        self.wallet = (wallet or "").lower()
        self.chain_price = None
        self.chain_nodes = {}

    def my_info(self):
        info = super(ChainMesh, self).my_info()
        info.update({
            "wallet": self.wallet,
            "capacity": capacity_units(),
            "load_bps": system_load_bps(),
            "network": PUBLIC_PROTOCOL,
        })
        return info

    def merge_chain_nodes(self, nodes):
        cache = wqpu.load_peer_cache()
        found = {}
        for node in nodes:
            wallet = str(node.get("wallet") or "").lower()
            if not wallet or wallet == self.wallet:
                continue
            try:
                peer = peer_from_registry(node)
            except Exception:
                continue
            key = wqpu.peer_key(peer["host"], peer["port"])
            cache[key] = {
                "host": peer["host"],
                "port": peer["port"],
                "fingerprint": peer["fingerprint"],
                "wallet": wallet,
                "capacity": peer["capacity"],
                "load_bps": peer["load_bps"],
            }
            found[wallet] = peer
        self.chain_nodes = found
        wqpu.save_peer_cache(cache)

    async def refresh_chain(self):
        try:
            nodes = await wqpu.to_thread(self.chain.discover, self.wallet or None, 512, 300)
            self.merge_chain_nodes(nodes)
            self.chain_price = await wqpu.to_thread(self.chain.global_price)
        except Exception:
            return

    async def connector_loop(self):
        tick = 0
        while not self.stop.is_set():
            if tick % 4 == 0:
                await self.refresh_chain()

            candidates = {}
            for p in self.cfg.get("peers") or []:
                if p.get("host"):
                    candidates[wqpu.peer_key(p["host"], p.get("port", wqpu.PORT))] = p
            candidates.update(wqpu.load_peer_cache())

            for key, peer in list(candidates.items()):
                if key in self.outbound:
                    continue
                try:
                    await asyncio.wait_for(self.connect_control(peer), 5)
                except Exception:
                    pass

            try:
                await self.broadcast_nodes()
            except Exception:
                pass
            for ctrl in list(self.outbound.values()):
                try:
                    await self.send(ctrl, {"type": "ping"})
                except Exception:
                    pass

            tick += 1
            await asyncio.sleep(5)

    def peers(self):
        peers = super(ChainMesh, self).peers()

        def rank(item):
            _, info = item
            load = int(info.get("load_bps") or 0)
            capacity = int(info.get("capacity") or info.get("ram_mb") or 0)
            return (load, -capacity)

        peers.sort(key=rank)
        limit = max(1, int(os.environ.get("WQPU_MAX_WORKERS", "8")))
        return peers[:limit]


async def wait_for_registration(chain, wallet, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            node = await wqpu.to_thread(chain.find_wallet, wallet, 512)
            if node and node.get("active"):
                return node
        except Exception:
            pass
        await asyncio.sleep(2)
    return None


def configured_chain(args, state):
    rpc_url = args.rpc_url or os.environ.get("WQPU_RPC_URL") or state.get("rpc_url")
    registry = args.registry or os.environ.get("WQPU_REGISTRY") or state.get("registry")
    if not rpc_url or not registry:
        return None
    return RegistryClient(rpc_url=rpc_url, registry=registry)


async def ensure_wallet(chain, state, force=False):
    wallet = str(state.get("wallet") or "").lower()
    if wallet and not force:
        return wallet

    endpoint = local_endpoint()
    chain_id = await wqpu.to_thread(chain.chain_id)
    print("WQPU: opening browser wallet connector...")
    result = await wqpu.to_thread(
        connect_wallet,
        chain.registry,
        endpoint,
        "0x" + wqpu.cert_fingerprint(),
        capacity_units(),
        system_load_bps(),
        chain_id,
        180,
    )
    wallet = str(result["wallet"]).lower()
    state.update({
        "wallet": wallet,
        "rpc_url": chain.rpc_url,
        "registry": chain.registry,
        "chain_id": chain_id,
        "public_endpoint": endpoint,
        "registration_tx": result.get("tx_hash"),
    })
    save_state(state)

    print("WQPU: wallet connected: {}".format(wallet))
    registered = await wait_for_registration(chain, wallet)
    if registered:
        print("WQPU: node registration confirmed on-chain.")
    else:
        print("WQPU: transaction submitted; registration is still waiting for confirmation.")
    return wallet


async def interactive(mesh, server_bin):
    wqpu.ensure_console_stdin()
    print("\nWQPU public peer is online. Type a question.")
    print("Commands: /status  /peers  /chain  /wallet  /exit\n")
    while not mesh.stop.is_set():
        try:
            line = (await wqpu.to_thread(input, "wqpu> ")).strip()
        except (EOFError, KeyboardInterrupt):
            line = "/exit"
        if not line:
            continue
        if line == "/exit":
            mesh.stop.set()
            break
        if line == "/status":
            print("WQPU {} | public chain peer | reachable workers: {}".format(
                VERSION, len(mesh.peers())
            ))
            continue
        if line == "/wallet":
            print(mesh.wallet or "No wallet connected")
            continue
        if line == "/chain":
            price = mesh.chain_price
            print("RPC: {}".format(mesh.chain.rpc_url))
            print("Registry: {}".format(mesh.chain.registry))
            print("Global price / 1M units: {}".format(price if price is not None else "unavailable"))
            print("Known active wallets: {}".format(len(mesh.chain_nodes)))
            continue
        if line == "/peers":
            peers = mesh.peers()
            if not peers:
                print("No reachable workers yet.")
            for nid, info in peers:
                print("- {} | load {}% | capacity {} | wallet {} | {}".format(
                    info.get("hostname", "peer"),
                    round(int(info.get("load_bps") or 0) / 100.0, 1),
                    info.get("capacity", info.get("ram_mb", "?")),
                    str(info.get("wallet") or "?")[:12],
                    nid[:8],
                ))
            continue
        try:
            await wqpu.ask(mesh, server_bin, line)
        except Exception as exc:
            print("WQPU error: {}".format(exc))


async def run_public(args):
    state = load_state()
    chain = configured_chain(args, state)
    if chain is None:
        raise RuntimeError(
            "public chain is not configured; set WQPU_RPC_URL and WQPU_REGISTRY "
            "or use --legacy for the old private join-code mesh"
        )

    wqpu.ensure_cert()
    chain_id = await wqpu.to_thread(chain.chain_id)
    wallet = await ensure_wallet(chain, state, force=args.connect_wallet)
    cfg = {
        "secret": public_secret(chain_id, chain.registry),
        "peers": [],
        "mode": "public-chain",
        "chain_id": chain_id,
        "registry": chain.registry,
    }

    server_bin, rpc_bin, tag = wqpu.ensure_runtime()
    mesh = ChainMesh(cfg, chain, wallet)
    await mesh.start_listener()
    rpc = wqpu.start_proc([
        str(rpc_bin), "--host", "127.0.0.1", "--port", str(wqpu.RPC_PORT),
        "--threads", str(wqpu.threads_for()), "--device", "CPU", "--cache"
    ], "rpc.log")

    print("WQPU {} | llama.cpp {}".format(VERSION, tag))
    print("Wallet {} | endpoint {}".format(wallet, state.get("public_endpoint") or local_endpoint()))
    print("Node {} | RAM {} MiB | contributes {}/{} CPU threads".format(
        socket.gethostname(), wqpu.total_ram_mb(), wqpu.threads_for(), os.cpu_count() or "?"
    ))

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, mesh.stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass

    connector = asyncio.ensure_future(mesh.connector_loop())
    try:
        await mesh.refresh_chain()
        await interactive(mesh, server_bin)
    finally:
        mesh.stop.set()
        connector.cancel()
        await wqpu.close_server(mesh.server)
        wqpu.stop_proc(rpc)


def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        all_tasks = getattr(asyncio, "all_tasks", None)
        pending = all_tasks(loop=loop) if all_tasks else asyncio.Task.all_tasks(loop=loop)
        for task in pending:
            task.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        loop.close()


def main():
    ap = argparse.ArgumentParser(prog="wqpu")
    ap.add_argument("--version", action="version", version="WQPU {}".format(VERSION))
    ap.add_argument("--rpc-url", help="WQPU EVM JSON-RPC URL")
    ap.add_argument("--registry", help="WQPURegistry contract address")
    ap.add_argument("--connect-wallet", action="store_true", help="connect/register another browser wallet")
    ap.add_argument("--legacy", action="store_true", help="run the old private join-code mesh")
    ap.add_argument("--join", help="legacy WQPU1 join code")
    args = ap.parse_args()

    if args.legacy or args.join:
        return wqpu.run_async(wqpu.run(args.join or os.environ.get("WQPU_JOIN"))) or 0

    try:
        run_async(run_public(args))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print("WQPU error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
