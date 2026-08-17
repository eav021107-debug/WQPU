#!/usr/bin/env python3
"""Heavy E2E: real pinned llama.cpp inference, metering and payment through WQPU.

This intentionally uses a real ggml-rpc-server and llama-server rather than synthetic RPC
frames. The test succeeds only when the remote worker participates in real graph compute,
the requester/worker billing meters agree, the worker signs its report with its registered
TLS identity, a bounded session voucher is derived from those measured units, and the gas
relayer pays the same worker wallet.
"""
from __future__ import print_function

import asyncio
import json
import os
import secrets
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
import wqpu_claim  # noqa: E402
import wqpu_multistream  # noqa: E402
import wqpu_network_guard  # noqa: E402
import wqpu_payments  # noqa: E402
import wqpu_public_config  # noqa: E402
import wqpu_public_security  # noqa: E402
import wqpu_runtime as runtime  # noqa: E402
import wqpu_runtime_pin  # noqa: E402
import wqpu_session  # noqa: E402
from wqpu_chain import RegistryClient  # noqa: E402

STACK = ROOT / ".wqpu-testnet"
REAL_HOME = STACK / "real-inference-e2e"
MODEL = os.environ.get("WQPU_REAL_MODEL", "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M")
PRICE_UNITS = 1_000_000


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


def activate_payment_for_units(chain, config, operator_key, requester_wallet, requester_wallet_pem, units):
    """Create exactly-bounded testnet escrow/session after real work is measured.

    The requester EVM key is used only to sign permit + bounded spend authorization.
    Funding and activation transactions are submitted by the configured gas relayer.
    """
    token = str(config["token"]).lower()
    market = str(config["market"]).lower()
    price = int(chain.global_price())
    amount = (int(units) * price) // PRICE_UNITS
    if amount <= 0:
        raise RuntimeError("real measured work prices to zero")

    common.run([
        "cast", "send", token, "transfer(address,uint256)",
        requester_wallet, str(amount),
        "--rpc-url", chain.rpc_url, "--private-key", operator_key,
    ])

    block = chain.rpc("eth_getBlockByNumber", ["latest", False])
    now = int(block["timestamp"], 16)
    valid_until = now + 3600
    permit_raw = wqpu_session.sign_digest(
        common.permit_digest(chain, token, requester_wallet, market, amount, valid_until),
        requester_wallet_pem,
    )
    funding = common._recoverable_package(
        permit_raw,
        lambda signature: {
            "market": market,
            "requester": requester_wallet,
            "amount": amount,
            "deadline": valid_until,
            "permit_signature": signature,
        },
        wqpu_claim.simulate_funding,
        chain,
        "real-inference permit",
    )
    funding_tx = wqpu_claim.relay_funding(chain, funding)
    wqpu_claim.wait_receipt(chain, funding_tx, 120)

    session_pem = common.CLIENTS / "requester" / "session.pem"
    wqpu_session.ensure_session_key(session_pem)
    wqpu_session.SESSION_KEY = session_pem
    wqpu_session.SESSION_STATE = common.CLIENTS / "requester" / "session.json"
    wqpu_payments.PAYMENT_STATE = common.CLIENTS / "requester" / "payments.json"
    session_key = wqpu_session.session_address(chain, session_pem).lower()
    session_id = "0x" + secrets.token_hex(32)
    auth_raw = wqpu_session.sign_digest(
        wqpu_session.spend_authorization_digest(
            chain, market, requester_wallet, session_key, session_id,
            amount, price, valid_until,
        ),
        requester_wallet_pem,
    )
    activation = common._recoverable_package(
        auth_raw,
        lambda signature: {
            "market": market,
            "requester": requester_wallet,
            "session_key": session_key,
            "session_id": session_id,
            "max_amount": amount,
            "price_per_million_units": price,
            "valid_until": valid_until,
            "authorization_signature": signature,
        },
        wqpu_claim.simulate_activation,
        chain,
        "real-inference spend authorization",
    )
    activation_tx = wqpu_claim.relay_activation(chain, activation)
    wqpu_claim.wait_receipt(chain, activation_tx, 120)

    session = {
        "requester": requester_wallet,
        "session_key": session_key,
        "session_id": session_id,
        "max_amount": amount,
        "price_per_million_units": price,
        "valid_until": valid_until,
        "market": market,
        "chain_id": chain.chain_id(),
        "authorization_signature": activation["authorization_signature"],
        "funding_tx": funding_tx,
        "activation_tx": activation_tx,
    }
    wqpu_session.save_session(session)
    active = wqpu_session.active_session(chain, market, requester_wallet, session_id)
    if not active.get("active") or int(active.get("reserved_remaining") or 0) != amount:
        raise RuntimeError("real-inference bounded payment session was not activated")
    return session, amount


async def check():
    if REAL_HOME.exists():
        import shutil
        shutil.rmtree(str(REAL_HOME))
    REAL_HOME.mkdir(parents=True)
    common.CLIENTS = REAL_HOME / "identities"

    state = json.loads((STACK / "state.json").read_text())
    operator = json.loads((STACK / "operator.json").read_text())
    raw_config = json.loads((STACK / "network-config.json").read_text())
    config = wqpu_public_config.normalize_public(
        wqpu_chain, raw_config, raw_config["public"]
    )
    wqpu_chain.validate_network_config(raw_config, config)
    rpc_url = str(state["internal_rpc"])
    registry = str(config["registry"]).lower()
    token = str(config["token"]).lower()

    # Isolate the bounded requester session before AutoPay meshes are constructed.
    wqpu_session.SESSION_STATE = common.CLIENTS / "requester" / "session.json"
    wqpu_session.SESSION_KEY = common.CLIENTS / "requester" / "session.pem"
    wqpu_payments.PAYMENT_STATE = common.CLIENTS / "requester" / "payments.json"

    requester_wallet_pem = common.make_wallet_pem("requester")
    requester_key = common.private_key_from_pem(requester_wallet_pem)
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
    wqpu_multistream.install(wqpu_autopay.AutoPayChainMesh)
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
    old_auto_vouchers = os.environ.get("WQPU_AUTO_VOUCHERS")
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

        # Stopping llama-server closes every physical RPC socket. Finalize the requester
        # aggregate first; multistream then waits until verified worker stream reports add
        # up to billing-equivalent compute. Non-billable 13-byte housekeeping probes are
        # diagnostic only and cannot affect voucher units.
        stop_process(llama_proc)
        llama_proc = None
        await wqpu.close_server(proxy)
        proxy = None
        snapshot = requester.end_usage()
        requester_stats = snapshot.get(worker.me)
        if not requester_stats:
            raise RuntimeError("real llama.cpp requester produced no usage meter")
        matched = await requester.wait_provider_reports(snapshot.keys(), request_id, 30)
        report = (requester.provider_usage_reports.get(request_id) or {}).get(worker.me)
        if not report:
            raise RuntimeError("real llama.cpp worker produced no signed usage report")
        worker_stats = report.get("rpc") or {}
        if not matched:
            raise RuntimeError(
                "verified worker multistream aggregate did not match billable compute: requester={} worker={}".format(
                    json.dumps(requester_stats, sort_keys=True),
                    json.dumps(worker_stats, sort_keys=True),
                )
            )
        if not wqpu_accounting.meter_is_eligible(requester_stats):
            raise RuntimeError("requester meter rejected the real pinned llama.cpp stream")
        if not wqpu_accounting.meter_is_eligible(worker_stats):
            raise RuntimeError("worker meter rejected the real pinned llama.cpp stream")
        if not wqpu_accounting.meters_match(requester_stats, worker_stats):
            raise RuntimeError("real requester/worker billable-compute meters disagreed")
        graph_commands = int(requester_stats.get("graph_compute_calls") or 0) + int(
            requester_stats.get("graph_recompute_calls") or 0
        )
        scalar_ops = int(requester_stats.get("estimated_scalar_ops") or 0)
        if graph_commands <= 0 or scalar_ops <= 0:
            raise RuntimeError("llama-server answered without measured remote graph compute")
        if wqpu_attestation.certificate_fingerprint(report.get("certificate")) != worker_fp:
            raise RuntimeError("real worker usage report used the wrong TLS identity")
        if str(report.get("provider_wallet") or "").lower() != worker_wallet:
            raise RuntimeError("real worker report claimed the wrong provider wallet")

        # Now bind the *same real measured units* to a bounded requester authorization.
        session, expected_amount = activate_payment_for_units(
            chain, config, str(operator["private_key"]), requester_wallet,
            requester_wallet_pem, scalar_ops,
        )
        os.environ["WQPU_AUTO_VOUCHERS"] = "1"
        receipt, _ = wqpu_accounting.save_usage_receipt(requester, snapshot)
        rows = [row for row in (receipt.get("workers") or []) if row.get("node_id") == worker.me]
        if len(rows) != 1:
            raise RuntimeError("real measured worker did not produce exactly one receipt row")
        row = rows[0]
        voucher = row.get("voucher")
        if not row.get("dual_meter_match") or not voucher:
            raise RuntimeError("real matched compute did not issue voucher: {}".format(
                row.get("voucher_error") or receipt.get("payment_error") or "unknown error"
            ))
        if str(voucher.get("provider") or "").lower() != worker_wallet:
            raise RuntimeError("real compute voucher targets wrong provider wallet")
        if int(voucher.get("cumulative_units") or 0) != scalar_ops:
            raise RuntimeError("real compute voucher units differ from measured scalar ops")
        if int(voucher.get("cumulative_amount") or 0) != expected_amount:
            raise RuntimeError("real compute voucher amount differs from global price formula")

        delivered = await requester.send_payment_voucher(worker.me, voucher)
        if not delivered:
            raise RuntimeError("real compute voucher was not delivered through relay")
        await asyncio.sleep(0.1)

        before = common.balance_of(chain, token, worker_wallet)
        claim_tx = wqpu_claim.relay(chain, voucher)
        wqpu_claim.wait_receipt(chain, claim_tx, 120)
        after = common.balance_of(chain, token, worker_wallet)
        if after - before != expected_amount:
            raise RuntimeError("real worker received {}, expected {} WQPU wei".format(
                after - before, expected_amount
            ))
        try:
            wqpu_claim.simulate_claim(chain, voucher)
        except Exception:
            replay_blocked = True
        else:
            replay_blocked = False
        if not replay_blocked:
            raise RuntimeError("real compute voucher remained replayable after claim")

        active = wqpu_session.active_session(
            chain, session["market"], requester_wallet, session["session_id"]
        )
        if int(active.get("reserved_remaining") or 0) != 0:
            raise RuntimeError("exact real-compute reservation was not fully consumed")

        usage = result.get("usage") or {}
        print("WQPU REAL LLAMA MEASURED-PAYMENT E2E OK")
        print("model={} llama_tag={} requester={} worker={} verified_streams={} graph_commands={} scalar_ops={} paid_wei={} completion_tokens={}".format(
            MODEL, tag, requester_wallet, worker_wallet,
            int(report.get("verified_stream_count") or 1), graph_commands, scalar_ops,
            expected_amount, usage.get("completion_tokens", "?"),
        ))
        print("assistant={}".format(content.replace("\n", " ")[:160]))
    finally:
        requester.current_request_id = None
        if old_auto_vouchers is None:
            os.environ.pop("WQPU_AUTO_VOUCHERS", None)
        else:
            os.environ["WQPU_AUTO_VOUCHERS"] = old_auto_vouchers
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
