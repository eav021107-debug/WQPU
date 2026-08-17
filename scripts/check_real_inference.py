#!/usr/bin/env python3
"""Heavy E2E: real pinned llama.cpp inference through authenticated WQPU relay.

This intentionally uses a real ggml-rpc-server and llama-server rather than synthetic RPC
frames. The test succeeds only when the remote worker participates in real graph compute,
the requester/worker meters agree, the worker signs its report with its registered TLS
identity, and llama-server returns an actual chat completion.
"""
from __future__ import print_function

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_two_client_relay as common  # noqa: E402
import wqpu  # noqa: E402
import wqpu_accounting  # noqa: E402
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
REAL_HOME = STACK / "real-inference-e2e"
MODEL = os.environ.get("WQPU_REAL_MODEL", "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M")


def start_process(command, logfile):
    REAL_HOME.mkdir(parents=True, exist_ok=True)
    handle = (REAL_HOME / logfile).open("a", encoding="utf-8")
    return subprocess.Popen(
        [str(x) for x in command],
        stdout=handle,
        stderr=subprocess.STDOUT,
        cwd=str(ROOT),
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
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("ggml-rpc-server exited before becoming ready")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", int(port))
            await wqpu.close_writer(writer)
            return
        except Exception:
            await asyncio.sleep(0.25)
    raise RuntimeError("ggml-rpc-server did not become ready")


async def wait_http(port, proc, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            log = REAL_HOME / "llama-server.log"
            tail = ""
            try:
                tail = "\n".join(log.read_text(errors="replace").splitlines()[-40:])
            except Exception:
                pass
            raise RuntimeError("llama-server exited during model load:\n{}".format(tail))
        try:
            with urllib.request.urlopen(
                "http://127.0.0.1:{}/health".format(int(port)), timeout=2
            ) as response:
                body = json.load(response)
            if body.get("status") in ("ok", "no slot available") or body.get("ok") is True:
                return
        except Exception:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("llama-server did not become ready within {}s".format(timeout))


def chat(port):
    payload = json.dumps({
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": "Reply briefly. What is two plus two?",
        }],
        "temperature": 0,
        "max_tokens": 24,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:{}/v1/chat/completions".format(int(port)),
        data=payload,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        return json.load(response)


async def check():
    if REAL_HOME.exists():
        import shutil
        shutil.rmtree(str(REAL_HOME))
    REAL_HOME.mkdir(parents=True)
    common.CLIENTS = REAL_HOME / "identities"

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
    common.register(rpc_url, registry, requester_key, requester_wallet, requester_fp, 42001)
    common.register(rpc_url, registry, worker_key, worker_wallet, worker_fp, 42002)

    chain = RegistryClient(rpc_url=rpc_url, registry=registry)
    chain.expected_chain_id = str(config["chain_id"]).lower()
    chain.network = dict(config)
    requester_record = chain.find_wallet(requester_wallet, 512)
    worker_record = chain.find_wallet(worker_wallet, 512)
    if not requester_record or not worker_record:
        raise RuntimeError("real inference identities were not registered")

    wqpu_network_guard.install(runtime)
    wqpu_public_security.install(wqpu_autopay.AutoPayChainMesh)
    relay_peer = dict(config["relays"][0])
    mesh_cfg = {
        "secret": runtime.public_secret(config["chain_id"], registry),
        "peers": [relay_peer],
    }
    requester = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, requester_wallet)
    worker = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, worker_wallet)
    requester.me = "real-inference-requester"
    worker.me = "real-inference-worker"
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
    server_bin, rpc_bin, tag = wqpu_runtime_pin.ensure_runtime()
    if tag != "b10456":
        raise RuntimeError("real inference did not use pinned llama.cpp b10456")

    old_rpc_port = wqpu.RPC_PORT
    worker_port = wqpu.free_port()
    wqpu.RPC_PORT = worker_port
    rpc_proc = start_process([
        rpc_bin,
        "--host", "127.0.0.1",
        "--port", str(worker_port),
        "--threads", "2",
        "--device", "CPU",
        "--cache",
    ], "ggml-rpc-server.log")
    llama_proc = None
    proxy = None
    request_id = os.urandom(16).hex()
    try:
        await wait_tcp(worker_port, rpc_proc, 60)
        await worker.connect_control(relay_peer)
        await requester.connect_control(relay_peer)
        await common.wait_until(lambda: worker.me in requester.peer_info, 15)
        await common.wait_until(lambda: any(nid == worker.me for nid, _ in requester.peers()), 15)

        requester.current_request_id = request_id
        requester.provider_usage_reports.pop(request_id, None)
        requester.begin_usage()
        proxy = await asyncio.start_server(
            lambda reader, writer: requester.proxy_handler(worker.me, reader, writer),
            "127.0.0.1", 0,
        )
        proxy_endpoint = "127.0.0.1:{}".format(proxy.sockets[0].getsockname()[1])
        api_port = wqpu.free_port()
        llama_proc = start_process([
            server_bin,
            "--hf-repo", MODEL,
            "--threads", "2",
            "--threads-batch", "2",
            "--ctx-size", "512",
            "--host", "127.0.0.1",
            "--port", str(api_port),
            "--parallel", "1",
            "--rpc", proxy_endpoint,
        ], "llama-server.log")

        await wait_http(api_port, llama_proc, 900)
        result = await wqpu.to_thread(chat, api_port)
        choices = result.get("choices") or []
        content = ""
        if choices:
            content = str(((choices[0].get("message") or {}).get("content") or "")).strip()
        if not content:
            raise RuntimeError("real llama-server returned no assistant content")

        # Stopping llama-server closes the RPC connection, which finalizes the worker's
        # independent meter and causes its TLS-signed report to travel back through relay.
        stop_process(llama_proc)
        llama_proc = None
        await wqpu.close_server(proxy)
        proxy = None
        await requester.wait_provider_reports([worker.me], request_id, 30)
        snapshot = requester.end_usage()
        requester_stats = snapshot.get(worker.me)
        report = (requester.provider_usage_reports.get(request_id) or {}).get(worker.me)
        if not requester_stats or not report:
            raise RuntimeError("real llama.cpp worker produced no signed usage report")
        worker_stats = report.get("rpc") or {}
        if not wqpu_accounting.meter_eligible(requester_stats):
            raise RuntimeError("requester meter rejected the real pinned llama.cpp stream")
        if not wqpu_accounting.meter_eligible(worker_stats):
            raise RuntimeError("worker meter rejected the real pinned llama.cpp stream")
        if not wqpu_accounting.meters_match(requester_stats, worker_stats):
            raise RuntimeError("real requester/worker llama.cpp meters disagreed")
        graph_commands = int(requester_stats.get("graph_compute_commands") or 0)
        scalar_ops = int(requester_stats.get("estimated_scalar_ops") or 0)
        if graph_commands <= 0 or scalar_ops <= 0:
            raise RuntimeError("llama-server answered without measured remote graph compute")
        if wqpu_attestation.certificate_fingerprint(report.get("certificate")) != worker_fp:
            raise RuntimeError("real worker usage report used the wrong TLS identity")
        if str(report.get("provider_wallet") or "").lower() != worker_wallet:
            raise RuntimeError("real worker report claimed the wrong provider wallet")

        usage = result.get("usage") or {}
        print("WQPU REAL LLAMA INFERENCE E2E OK")
        print("model={} llama_tag={} worker={} graph_commands={} scalar_ops={} completion_tokens={}".format(
            MODEL, tag, worker_wallet, graph_commands, scalar_ops,
            usage.get("completion_tokens", "?"),
        ))
        print("assistant={}".format(content.replace("\n", " ")[:160]))
    finally:
        requester.current_request_id = None
        if proxy:
            await wqpu.close_server(proxy)
        stop_process(llama_proc)
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
