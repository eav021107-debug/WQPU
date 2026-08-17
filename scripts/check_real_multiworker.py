#!/usr/bin/env python3
"""Heavy E2E: one real llama.cpp model uses and pays two distinct WQPU workers.

The workers intentionally share one CI Python process only as a test harness; each still has
its own EVM wallet, TLS identity and real ggml-rpc-server on a distinct localhost port. The
requester reaches both only through the authenticated WQPU relay. Success requires non-zero
real graph compute, a verified independent meter and a provider-specific WQPU payment for
*each* worker.
"""
from __future__ import print_function

import asyncio
import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_real_inference as single  # noqa: E402
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
from wqpu_attestation import sign_report  # noqa: E402
from wqpu_chain import RegistryClient  # noqa: E402
from wqpu_meter import MeterError, RPCRequestMeter  # noqa: E402

STACK = ROOT / ".wqpu-testnet"
MULTI_HOME = STACK / "real-multiworker-e2e"
MODEL = os.environ.get("WQPU_REAL_MODEL", "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M")
PRICE_UNITS = 1_000_000


class IsolatedWorkerMesh(wqpu_autopay.AutoPayChainMesh):
    """Test harness version of one normal WQPU process with its own localhost RPC port."""
    def __init__(self, cfg, chain, wallet, local_rpc_port):
        self.local_rpc_port = int(local_rpc_port)
        super(IsolatedWorkerMesh, self).__init__(cfg, chain, wallet)

    async def _worker_rpc(self, msg, via):
        sid = str(msg.get("stream") or "")
        if not sid or not via:
            return
        rr = rw = lr = lw = None
        try:
            rr, rw = await self.open_accept(via, sid)
            line = await asyncio.wait_for(rr.readline(), 5)
            if not line.startswith(wqpu_autopay.METER_PRELUDE) or len(line) > 2048:
                lr, lw = await asyncio.open_connection("127.0.0.1", self.local_rpc_port)
                lw.write(line)
                await lw.drain()
                await wqpu.bridge(rr, rw, lr, lw)
                return
            meta = json.loads(line[len(wqpu_autopay.METER_PRELUDE):].decode("utf-8"))
            request_id = str(meta.get("request_id") or "")
            requester = str(meta.get("requester_node_id") or "")
            if len(request_id) != 32 or not requester:
                raise RuntimeError("bad WQPU meter prelude")
            int(request_id, 16)

            lr, lw = await asyncio.open_connection("127.0.0.1", self.local_rpc_port)
            meter = RPCRequestMeter()
            await asyncio.gather(
                self._copy_worker_metered(rr, lw, meter),
                wqpu.copy_stream(lr, rw),
            )
            report = sign_report({
                "version": 1,
                "kind": "wqpu-worker-usage-attestation",
                "request_id": request_id,
                "requester_node_id": requester,
                "provider_node_id": self.me,
                "provider_wallet": self.wallet,
                "rpc": meter.snapshot(),
            })
            await self._route_usage_report(requester, report)
        except Exception:
            if rw:
                await wqpu.close_writer(rw)
            if lw:
                await wqpu.close_writer(lw)


async def check():
    import shutil
    if MULTI_HOME.exists():
        shutil.rmtree(str(MULTI_HOME))
    MULTI_HOME.mkdir(parents=True)
    common.CLIENTS = MULTI_HOME / "identities"
    single.REAL_HOME = MULTI_HOME
    single.MODEL = MODEL

    state = json.loads((STACK / "state.json").read_text())
    operator = json.loads((STACK / "operator.json").read_text())
    raw_config = json.loads((STACK / "network-config.json").read_text())
    config = wqpu_public_config.normalize_public(wqpu_chain, raw_config, raw_config["public"])
    wqpu_chain.validate_network_config(raw_config, config)
    rpc_url = str(state["internal_rpc"])
    registry = str(config["registry"]).lower()
    token = str(config["token"]).lower()

    wqpu_session.SESSION_STATE = common.CLIENTS / "requester" / "session.json"
    wqpu_session.SESSION_KEY = common.CLIENTS / "requester" / "session.pem"
    wqpu_payments.PAYMENT_STATE = common.CLIENTS / "requester" / "payments.json"

    requester_pem = common.make_wallet_pem("requester")
    requester_key = common.private_key_from_pem(requester_pem)
    requester_wallet = common.wallet_address(requester_key)
    worker_keys = [common.private_key(), common.private_key()]
    worker_wallets = [common.wallet_address(key) for key in worker_keys]

    requester_cert, requester_tls_key, requester_fp = common.make_tls_identity("requester")
    worker_identities = [common.make_tls_identity("worker-a"), common.make_tls_identity("worker-b")]
    common.register(rpc_url, registry, requester_key, requester_wallet, requester_fp, 45001)
    for index in range(2):
        common.register(
            rpc_url, registry, worker_keys[index], worker_wallets[index],
            worker_identities[index][2], 45002 + index,
        )

    chain = RegistryClient(rpc_url=rpc_url, registry=registry)
    chain.expected_chain_id = str(config["chain_id"]).lower()
    chain.network = dict(config)
    records = {requester_wallet: chain.find_wallet(requester_wallet, 512)}
    for wallet in worker_wallets:
        records[wallet] = chain.find_wallet(wallet, 512)
    if not all(records.values()):
        raise RuntimeError("multiworker identities were not registered")

    wqpu_network_guard.install(runtime)
    wqpu_multistream.install(wqpu_autopay.AutoPayChainMesh)
    wqpu_multistream.install(IsolatedWorkerMesh)
    wqpu_public_security.install(wqpu_autopay.AutoPayChainMesh)
    wqpu_public_security.install(IsolatedWorkerMesh)
    relay_peer = dict(config["relays"][0])
    mesh_cfg = {
        "secret": runtime.public_secret(config["chain_id"], registry),
        "peers": [relay_peer],
    }

    os.environ["WQPU_LLAMA_TAG"] = "b10456"
    wqpu.ensure_runtime = wqpu_runtime_pin.ensure_runtime
    server_bin, rpc_bin, tag = wqpu_runtime_pin.ensure_runtime()
    if tag != "b10456":
        raise RuntimeError("multiworker inference did not use pinned llama.cpp b10456")

    worker_ports = [wqpu.free_port(), wqpu.free_port()]
    requester = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, requester_wallet)
    workers = [
        IsolatedWorkerMesh(mesh_cfg, chain, worker_wallets[0], worker_ports[0]),
        IsolatedWorkerMesh(mesh_cfg, chain, worker_wallets[1], worker_ports[1]),
    ]
    requester.me = "real-multi-requester"
    requester.identity_cert_path = requester_cert
    requester.identity_key_path = requester_tls_key
    wqpu_attestation.register_identity(requester.me, requester_cert, requester_tls_key)
    for index, worker in enumerate(workers):
        worker.me = "real-multi-worker-{}".format(index + 1)
        worker.identity_cert_path = worker_identities[index][0]
        worker.identity_key_path = worker_identities[index][1]
        wqpu_attestation.register_identity(
            worker.me, worker_identities[index][0], worker_identities[index][1]
        )
    for mesh in [requester] + workers:
        mesh.chain_nodes.update(records)

    rpc_procs = []
    llama_proc = None
    proxies = []
    old_auto_vouchers = os.environ.get("WQPU_AUTO_VOUCHERS")
    request_id = os.urandom(16).hex()
    try:
        for index, port in enumerate(worker_ports):
            proc = single.start_process([
                rpc_bin, "--host", "127.0.0.1", "--port", str(port),
                "--threads", "2", "--device", "CPU", "--cache",
            ], "ggml-rpc-worker-{}.log".format(index + 1))
            rpc_procs.append(proc)
            await single.wait_tcp(port, proc, 60)

        for worker in workers:
            await worker.connect_control(relay_peer)
        await requester.connect_control(relay_peer)
        for worker in workers:
            await common.wait_until(lambda w=worker: w.me in requester.peer_info, 20)
            await common.wait_until(
                lambda w=worker: any(nid == w.me for nid, _ in requester.peers()), 20
            )

        requester.current_request_id = request_id
        requester.provider_usage_reports.pop(request_id, None)
        requester.begin_usage()
        endpoints = []
        for worker in workers:
            proxy = await asyncio.start_server(
                lambda reader, writer, target=worker.me: requester.proxy_handler(target, reader, writer),
                "127.0.0.1", 0,
            )
            proxies.append(proxy)
            endpoints.append("127.0.0.1:{}".format(proxy.sockets[0].getsockname()[1]))

        api_port = wqpu.free_port()
        llama_proc = single.start_process([
            server_bin,
            "--hf-repo", MODEL,
            "--threads", "2",
            "--threads-batch", "2",
            "--ctx-size", "512",
            "--host", "127.0.0.1",
            "--port", str(api_port),
            "--parallel", "1",
            "--rpc", ",".join(endpoints),
        ], "llama-server.log")
        await single.wait_http(api_port, llama_proc, 900)
        result = await wqpu.to_thread(single.chat, api_port)
        choices = result.get("choices") or []
        content = ""
        if choices:
            content = str(((choices[0].get("message") or {}).get("content") or "")).strip()
        if not content:
            raise RuntimeError("multiworker llama-server returned no assistant content")

        single.stop_process(llama_proc)
        llama_proc = None
        for proxy in proxies:
            await wqpu.close_server(proxy)
        proxies = []
        snapshot = requester.end_usage()

        worker_ids = [worker.me for worker in workers]
        missing = [worker_id for worker_id in worker_ids if worker_id not in snapshot]
        if missing:
            raise RuntimeError("real model did not open RPC work for workers: {}".format(missing))
        matched = await requester.wait_provider_reports(worker_ids, request_id, 45)
        if not matched:
            details = {
                worker_id: {
                    "requester": snapshot.get(worker_id),
                    "provider": ((requester.provider_usage_reports.get(request_id) or {}).get(worker_id) or {}).get("rpc"),
                }
                for worker_id in worker_ids
            }
            raise RuntimeError("multiworker billable meters did not converge: {}".format(
                json.dumps(details, sort_keys=True)
            ))

        units_by_worker = {}
        for index, worker in enumerate(workers):
            requester_stats = snapshot.get(worker.me) or {}
            report = (requester.provider_usage_reports.get(request_id) or {}).get(worker.me) or {}
            provider_stats = report.get("rpc") or {}
            if not wqpu_accounting.meter_is_eligible(requester_stats):
                raise RuntimeError("requester meter rejected worker {}".format(index + 1))
            if not wqpu_accounting.meter_is_eligible(provider_stats):
                raise RuntimeError("provider meter rejected worker {}".format(index + 1))
            if not wqpu_accounting.meters_match(requester_stats, provider_stats):
                raise RuntimeError("billable compute mismatch for worker {}".format(index + 1))
            units = int(requester_stats.get("estimated_scalar_ops") or 0)
            graph_commands = int(requester_stats.get("graph_compute_calls") or 0) + int(
                requester_stats.get("graph_recompute_calls") or 0
            )
            if units <= 0 or graph_commands <= 0:
                raise RuntimeError("worker {} did not perform real graph compute".format(index + 1))
            if str(report.get("provider_wallet") or "").lower() != worker_wallets[index]:
                raise RuntimeError("worker {} report wallet mismatch".format(index + 1))
            if wqpu_attestation.certificate_fingerprint(report.get("certificate")) != worker_identities[index][2]:
                raise RuntimeError("worker {} report TLS identity mismatch".format(index + 1))
            units_by_worker[worker.me] = units

        total_units = sum(units_by_worker.values())
        session, reserved_amount = single.activate_payment_for_units(
            chain, config, str(operator["private_key"]), requester_wallet,
            requester_pem, total_units,
        )
        os.environ["WQPU_AUTO_VOUCHERS"] = "1"
        receipt, _ = wqpu_accounting.save_usage_receipt(requester, snapshot)
        rows = {row.get("node_id"): row for row in (receipt.get("workers") or [])}
        if set(worker_ids) - set(rows):
            raise RuntimeError("receipt omitted one or more real workers")

        price = int(session["price_per_million_units"])
        paid_total = 0
        payment_summary = []
        for index, worker in enumerate(workers):
            row = rows[worker.me]
            voucher = row.get("voucher")
            units = units_by_worker[worker.me]
            expected = (units * price) // PRICE_UNITS
            if not row.get("dual_meter_match") or not voucher:
                raise RuntimeError("worker {} did not receive a measured voucher: {}".format(
                    index + 1, row.get("voucher_error") or receipt.get("payment_error")
                ))
            if str(voucher.get("provider") or "").lower() != worker_wallets[index]:
                raise RuntimeError("worker {} voucher provider mismatch".format(index + 1))
            if int(voucher.get("cumulative_units") or 0) != units:
                raise RuntimeError("worker {} voucher units mismatch".format(index + 1))
            if int(voucher.get("cumulative_amount") or 0) != expected:
                raise RuntimeError("worker {} voucher price mismatch".format(index + 1))
            if not await requester.send_payment_voucher(worker.me, voucher):
                raise RuntimeError("worker {} voucher relay delivery failed".format(index + 1))

            before = common.balance_of(chain, token, worker_wallets[index])
            tx_hash = wqpu_claim.relay(chain, voucher)
            wqpu_claim.wait_receipt(chain, tx_hash, 120)
            after = common.balance_of(chain, token, worker_wallets[index])
            if after - before != expected:
                raise RuntimeError("worker {} payment delta mismatch".format(index + 1))
            try:
                wqpu_claim.simulate_claim(chain, voucher)
            except Exception:
                pass
            else:
                raise RuntimeError("worker {} voucher replay remained valid".format(index + 1))
            paid_total += expected
            report = (requester.provider_usage_reports.get(request_id) or {}).get(worker.me) or {}
            payment_summary.append({
                "worker": worker_wallets[index],
                "units": units,
                "paid_wei": expected,
                "verified_streams": int(report.get("verified_stream_count") or 1),
            })

        if paid_total != reserved_amount:
            raise RuntimeError("multiworker paid total does not equal exact session reservation")
        active = wqpu_session.active_session(
            chain, session["market"], requester_wallet, session["session_id"]
        )
        if int(active.get("reserved_remaining") or 0) != 0:
            raise RuntimeError("multiworker exact payment reserve was not fully consumed")

        print("WQPU REAL TWO-WORKER LLAMA MEASURED-PAYMENT E2E OK")
        print(json.dumps({
            "model": MODEL,
            "llama_tag": tag,
            "requester": requester_wallet,
            "total_units": total_units,
            "paid_total_wei": paid_total,
            "workers": payment_summary,
            "assistant": content.replace("\n", " ")[:160],
        }, sort_keys=True))
    finally:
        requester.current_request_id = None
        if old_auto_vouchers is None:
            os.environ.pop("WQPU_AUTO_VOUCHERS", None)
        else:
            os.environ["WQPU_AUTO_VOUCHERS"] = old_auto_vouchers
        for proxy in proxies:
            await wqpu.close_server(proxy)
        single.stop_process(llama_proc)
        for proc in rpc_procs:
            single.stop_process(proc)
        await common.close_mesh(requester)
        for worker in workers:
            await common.close_mesh(worker)
        wqpu_attestation.unregister_identity(requester.me)
        for worker in workers:
            wqpu_attestation.unregister_identity(worker.me)
        for key in [requester_key] + worker_keys:
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
