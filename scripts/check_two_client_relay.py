#!/usr/bin/env python3
"""E2E: distinct WQPU clients route, meter and pay measured work through one relay."""
from __future__ import print_function

import asyncio
import json
import os
import secrets
import shutil
import struct
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

import wqpu  # noqa: E402
import wqpu_accounting  # noqa: E402
import wqpu_attestation  # noqa: E402
import wqpu_autopay  # noqa: E402
import wqpu_chain  # noqa: E402
import wqpu_claim  # noqa: E402
import wqpu_meter  # noqa: E402
import wqpu_network_guard  # noqa: E402
import wqpu_payments  # noqa: E402
import wqpu_public_config  # noqa: E402
import wqpu_public_security  # noqa: E402
import wqpu_runtime as runtime  # noqa: E402
import wqpu_session  # noqa: E402
from wqpu_chain import RegistryClient  # noqa: E402
from wqpu_node_identity import certificate_der, certificate_fingerprint_from_der  # noqa: E402

STACK = ROOT / ".wqpu-testnet"
CLIENTS = STACK / "two-client-e2e"
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
PRICE_UNITS = 1_000_000


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if proc.returncode != 0:
        raise RuntimeError("command failed: {}\n{}".format(" ".join(map(str, cmd)), proc.stdout))
    return proc.stdout.strip()


def rpc(url, method, params):
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    request = urllib.request.Request(url, data=raw, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    if body.get("error"):
        raise RuntimeError("RPC {} failed: {}".format(method, body["error"]))
    return body.get("result")


def strip0x(value):
    text = str(value or "")
    return text[2:] if text.startswith("0x") else text


def word(value):
    return "{:064x}".format(int(value))


def address_word(value):
    return strip0x(value).lower().rjust(64, "0")


def selector(client, signature):
    hashed = client.rpc("web3_sha3", ["0x" + signature.encode("utf-8").hex()])
    return strip0x(hashed)[:8]


def keccak(client, raw_hex):
    value = client.rpc("web3_sha3", ["0x" + strip0x(raw_hex)])
    if not isinstance(value, str) or len(strip0x(value)) != 64:
        raise RuntimeError("bad web3_sha3 result")
    return value.lower()


def eth_call_word(client, contract, signature, args=""):
    data = selector(client, signature) + args
    value = client.rpc("eth_call", [{"to": contract, "data": "0x" + data}, "latest"])
    raw = strip0x(value)
    if len(raw) < 64:
        raise RuntimeError("short eth_call result for {}".format(signature))
    return raw[-64:]


def balance_of(client, token, wallet):
    return int(eth_call_word(client, token, "balanceOf(address)", address_word(wallet)), 16)


def permit_digest(client, token, owner, spender, amount, deadline):
    domain = eth_call_word(client, token, "DOMAIN_SEPARATOR()")
    typehash = eth_call_word(client, token, "PERMIT_TYPEHASH()")
    nonce = int(eth_call_word(client, token, "nonces(address)", address_word(owner)), 16)
    struct_hash = strip0x(keccak(client, "".join([
        typehash,
        address_word(owner),
        address_word(spender),
        word(amount),
        word(nonce),
        word(deadline),
    ])))
    return keccak(client, "1901" + domain + struct_hash)


def make_wallet_pem(name):
    path = CLIENTS / name / "wallet.pem"
    path.parent.mkdir(parents=True, exist_ok=True)
    wqpu_session.ensure_session_key(path)
    return path


def private_key_from_pem(path):
    out = run(["openssl", "ec", "-in", str(path), "-text", "-noout"])
    collecting = False
    chunks = []
    for line in out.splitlines():
        text = line.strip()
        if text == "priv:":
            collecting = True
            continue
        if collecting and text == "pub:":
            break
        if collecting:
            chunks.append(text.replace(":", "").replace(" ", ""))
    raw = "".join(chunks).lower()
    if len(raw) != 64:
        raise RuntimeError("could not extract 32-byte secp256k1 private key from PEM")
    int(raw, 16)
    return "0x" + raw


def private_key():
    return "0x{:064x}".format(secrets.randbelow(SECP256K1_N - 1) + 1)


def wallet_address(key):
    out = run(["cast", "wallet", "address", "--private-key", key])
    for token in out.split():
        if token.startswith("0x") and len(token) == 42:
            return token.lower()
    raise RuntimeError("could not derive EVM wallet")


def make_tls_identity(name):
    root = CLIENTS / name
    root.mkdir(parents=True, exist_ok=True)
    cert = root / "cert.pem"
    key = root / "key.pem"
    run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(key), "-out", str(cert), "-days", "1",
        "-subj", "/CN=WQPU-{}".format(name),
    ])
    try:
        key.chmod(0o600)
    except Exception:
        pass
    fp = certificate_fingerprint_from_der(certificate_der(cert))
    return cert, key, fp


def register(rpc_url, registry, wallet_key, wallet, fingerprint, port):
    rpc(rpc_url, "anvil_setBalance", [wallet, hex(10 * 10 ** 18)])
    run([
        "cast", "send", registry,
        "announce(string,bytes32,uint64,uint16)",
        "127.0.0.1:{}".format(port), "0x" + fingerprint, "32768", "0",
        "--rpc-url", rpc_url, "--private-key", wallet_key,
    ])


def _recoverable_package(raw_signature, builder, simulator, client, label):
    raw = strip0x(raw_signature)
    for recovery in (27, 28):
        candidate = builder("0x" + raw + "{:02x}".format(recovery))
        try:
            simulator(client, candidate)
            return candidate
        except Exception:
            pass
    raise RuntimeError("neither recovery id produced a valid {} signature".format(label))


def activate_payment_session(chain, config, operator_key, requester_wallet, requester_wallet_pem):
    token = str(config["token"]).lower()
    market = str(config["market"]).lower()
    price = int(chain.global_price())
    max_amount = max(price, 1)
    deposit = max_amount

    # Operator owns the fixed testnet supply. Give requester only the amount it will
    # permit into escrow; requester wallet key does not fund/activate/claim transactions.
    run([
        "cast", "send", token, "transfer(address,uint256)",
        requester_wallet, str(deposit),
        "--rpc-url", chain.rpc_url, "--private-key", operator_key,
    ])

    block = chain.rpc("eth_getBlockByNumber", ["latest", False])
    now = int(block["timestamp"], 16)
    valid_until = now + 3600

    permit_raw = wqpu_session.sign_digest(
        permit_digest(chain, token, requester_wallet, market, deposit, valid_until),
        requester_wallet_pem,
    )
    funding = _recoverable_package(
        permit_raw,
        lambda signature: {
            "market": market,
            "requester": requester_wallet,
            "amount": deposit,
            "deadline": valid_until,
            "permit_signature": signature,
        },
        wqpu_claim.simulate_funding,
        chain,
        "permit",
    )
    funding_tx = wqpu_claim.relay_funding(chain, funding)
    wqpu_claim.wait_receipt(chain, funding_tx, 120)

    session_pem = CLIENTS / "requester" / "session.pem"
    wqpu_session.ensure_session_key(session_pem)
    wqpu_session.SESSION_KEY = session_pem
    wqpu_session.SESSION_STATE = CLIENTS / "requester" / "session.json"
    wqpu_payments.PAYMENT_STATE = CLIENTS / "requester" / "payments.json"
    session_key = wqpu_session.session_address(chain, session_pem).lower()
    session_id = "0x" + secrets.token_hex(32)

    auth_raw = wqpu_session.sign_digest(
        wqpu_session.spend_authorization_digest(
            chain, market, requester_wallet, session_key, session_id,
            max_amount, price, valid_until,
        ),
        requester_wallet_pem,
    )
    activation = _recoverable_package(
        auth_raw,
        lambda signature: {
            "market": market,
            "requester": requester_wallet,
            "session_key": session_key,
            "session_id": session_id,
            "max_amount": max_amount,
            "price_per_million_units": price,
            "valid_until": valid_until,
            "authorization_signature": signature,
        },
        wqpu_claim.simulate_activation,
        chain,
        "spend authorization",
    )
    activation_tx = wqpu_claim.relay_activation(chain, activation)
    wqpu_claim.wait_receipt(chain, activation_tx, 120)

    session = {
        "requester": requester_wallet,
        "session_key": session_key,
        "session_id": session_id,
        "max_amount": max_amount,
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
    if not active.get("active") or int(active.get("reserved_remaining") or 0) != max_amount:
        raise RuntimeError("bounded requester payment session was not activated")
    return session


def frame(command, payload=b""):
    return bytes([command]) + len(payload).to_bytes(8, "little") + payload


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
    raise RuntimeError("two-client WQPU topology did not converge")


async def close_mesh(mesh):
    mesh.stop.set()
    for ctrl in list(mesh.outbound.values()):
        await wqpu.close_writer(ctrl.writer)
    for ctrl in list(mesh.controls.values()):
        await wqpu.close_writer(ctrl.writer)


async def check():
    if CLIENTS.exists():
        shutil.rmtree(str(CLIENTS))
    CLIENTS.mkdir(parents=True)

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

    # Isolate payment/session state before AutoPay meshes are constructed. Their startup
    # sees no session yet; the bounded session is authorized only after both nodes exist.
    wqpu_session.SESSION_STATE = CLIENTS / "requester" / "session.json"
    wqpu_session.SESSION_KEY = CLIENTS / "requester" / "session.pem"
    wqpu_payments.PAYMENT_STATE = CLIENTS / "requester" / "payments.json"

    requester_wallet_pem = make_wallet_pem("requester")
    requester_key = private_key_from_pem(requester_wallet_pem)
    worker_key = private_key()
    requester_wallet = wallet_address(requester_key)
    worker_wallet = wallet_address(worker_key)
    requester_cert, requester_tls_key, requester_fp = make_tls_identity("requester")
    worker_cert, worker_tls_key, worker_fp = make_tls_identity("worker")

    register(rpc_url, registry, requester_key, requester_wallet, requester_fp, 41001)
    register(rpc_url, registry, worker_key, worker_wallet, worker_fp, 41002)

    chain = RegistryClient(rpc_url=rpc_url, registry=registry)
    chain.expected_chain_id = str(config["chain_id"]).lower()
    chain.network = dict(config)
    requester_record = chain.find_wallet(requester_wallet, 512)
    worker_record = chain.find_wallet(worker_wallet, 512)
    if not requester_record or not worker_record:
        raise RuntimeError("both test wallets were not registered")

    wqpu_network_guard.install(runtime)
    wqpu_public_security.install(wqpu_autopay.AutoPayChainMesh)
    relay_peer = dict(config["relays"][0])
    secret = runtime.public_secret(config["chain_id"], registry)
    mesh_cfg = {"secret": secret, "peers": [relay_peer]}

    requester = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, requester_wallet)
    worker = wqpu_autopay.AutoPayChainMesh(mesh_cfg, chain, worker_wallet)
    requester.me = "two-client-requester"
    worker.me = "two-client-worker"
    requester.identity_cert_path = requester_cert
    requester.identity_key_path = requester_tls_key
    worker.identity_cert_path = worker_cert
    worker.identity_key_path = worker_tls_key
    wqpu_attestation.register_identity(requester.me, requester_cert, requester_tls_key)
    wqpu_attestation.register_identity(worker.me, worker_cert, worker_tls_key)
    for mesh in (requester, worker):
        mesh.chain_nodes[requester_wallet] = requester_record
        mesh.chain_nodes[worker_wallet] = worker_record

    echo = await asyncio.start_server(echo_handler, "127.0.0.1", 0)
    old_rpc_port = wqpu.RPC_PORT
    old_auto_vouchers = os.environ.get("WQPU_AUTO_VOUCHERS")
    wqpu.RPC_PORT = echo.sockets[0].getsockname()[1]
    proxy = None
    client_writer = None
    request_id = secrets.token_hex(16)
    try:
        await worker.connect_control(relay_peer)
        await requester.connect_control(relay_peer)
        await wait_until(lambda: worker.me in requester.peer_info)
        await wait_until(lambda: any(nid == worker.me for nid, _ in requester.peers()))
        await wait_until(lambda: requester.me in worker.peer_info)

        seen = requester.peer_info.get(worker.me) or {}
        if str(seen.get("wallet") or "").lower() != worker_wallet:
            raise RuntimeError("requester associated worker node with wrong wallet")
        if str(seen.get("fingerprint") or "").lower().replace("0x", "") != worker_fp:
            raise RuntimeError("requester associated worker with wrong TLS fingerprint")
        if requester_wallet == worker_wallet or requester_fp == worker_fp:
            raise RuntimeError("two-client test did not create distinct identities")

        session = activate_payment_session(
            chain, config, str(operator["private_key"]), requester_wallet, requester_wallet_pem
        )

        requester.current_request_id = request_id
        requester.provider_usage_reports.pop(request_id, None)
        requester.begin_usage()
        proxy = await asyncio.start_server(
            lambda r, w: requester.proxy_handler(worker.me, r, w),
            "127.0.0.1", 0,
        )
        reader, client_writer = await asyncio.open_connection(
            "127.0.0.1", proxy.sockets[0].getsockname()[1]
        )
        payload = frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph_payload())
        client_writer.write(payload)
        await client_writer.drain()
        echoed = await asyncio.wait_for(reader.readexactly(len(payload)), 10)
        if echoed != payload:
            raise RuntimeError("authenticated dual-meter RPC echo mismatch")
        await wqpu.close_writer(client_writer)
        client_writer = None
        await wqpu.close_server(proxy)
        proxy = None

        await requester.wait_provider_reports([worker.me], request_id, 10)
        snapshot = requester.end_usage()
        requester_stats = snapshot.get(worker.me)
        report = (requester.provider_usage_reports.get(request_id) or {}).get(worker.me)
        if not requester_stats or not report:
            raise RuntimeError("dual-meter worker report was not received")
        worker_stats = report.get("rpc") or {}
        if not wqpu_accounting.meters_match(requester_stats, worker_stats):
            raise RuntimeError("distinct requester and worker meters disagreed")
        measured_units = int(requester_stats.get("estimated_scalar_ops") or 0)
        if measured_units != 24:
            raise RuntimeError("unexpected synthetic compute estimate")
        if wqpu_attestation.certificate_fingerprint(report.get("certificate")) != worker_fp:
            raise RuntimeError("usage report was not signed by registered worker TLS identity")
        if str(report.get("provider_wallet") or "").lower() != worker_wallet:
            raise RuntimeError("usage report claimed wrong provider wallet")

        os.environ["WQPU_AUTO_VOUCHERS"] = "1"
        receipt, _ = wqpu_accounting.save_usage_receipt(requester, snapshot)
        rows = [row for row in (receipt.get("workers") or []) if row.get("node_id") == worker.me]
        if len(rows) != 1:
            raise RuntimeError("measured worker did not produce exactly one usage receipt row")
        row = rows[0]
        if not row.get("dual_meter_match"):
            raise RuntimeError("receipt lost the successful dual-meter match")
        voucher = row.get("voucher")
        if not voucher:
            raise RuntimeError("matched measured work did not issue a bounded voucher: {}".format(
                row.get("voucher_error") or receipt.get("payment_error") or "unknown error"
            ))
        if str(voucher.get("provider") or "").lower() != worker_wallet:
            raise RuntimeError("voucher targets wrong provider wallet")
        if int(voucher.get("cumulative_units") or 0) != measured_units:
            raise RuntimeError("voucher units differ from measured compute")
        expected_amount = (measured_units * int(session["price_per_million_units"])) // PRICE_UNITS
        if expected_amount <= 0 or int(voucher.get("cumulative_amount") or 0) != expected_amount:
            raise RuntimeError("voucher price does not match global measured-unit formula")

        delivered = await requester.send_payment_voucher(worker.me, voucher)
        if not delivered:
            raise RuntimeError("measured-work voucher was not delivered to worker through relay")
        await asyncio.sleep(0.1)

        before = balance_of(chain, token, worker_wallet)
        claim_tx = wqpu_claim.relay(chain, voucher)
        wqpu_claim.wait_receipt(chain, claim_tx, 120)
        after = balance_of(chain, token, worker_wallet)
        if after - before != expected_amount:
            raise RuntimeError("worker received {}, expected {} WQPU wei".format(
                after - before, expected_amount
            ))
        try:
            wqpu_claim.simulate_claim(chain, voucher)
        except Exception:
            replay_blocked = True
        else:
            replay_blocked = False
        if not replay_blocked:
            raise RuntimeError("claimed measured-work voucher remained replayable")

        active = wqpu_session.active_session(
            chain, session["market"], requester_wallet, session["session_id"]
        )
        if int(active.get("reserved_remaining") or 0) != int(session["max_amount"]) - expected_amount:
            raise RuntimeError("claim did not consume the bounded requester reservation")

        print("WQPU measured-payment two-client E2E OK")
        print("requester={} worker={} scalar_ops={} paid_wei={} distinct_tls=true".format(
            requester_wallet, worker_wallet, measured_units, expected_amount
        ))
    finally:
        requester.current_request_id = None
        if old_auto_vouchers is None:
            os.environ.pop("WQPU_AUTO_VOUCHERS", None)
        else:
            os.environ["WQPU_AUTO_VOUCHERS"] = old_auto_vouchers
        if client_writer:
            await wqpu.close_writer(client_writer)
        if proxy:
            await wqpu.close_server(proxy)
        wqpu.RPC_PORT = old_rpc_port
        await close_mesh(requester)
        await close_mesh(worker)
        await wqpu.close_server(echo)
        wqpu_attestation.unregister_identity(requester.me)
        wqpu_attestation.unregister_identity(worker.me)
        for key in (requester_key, worker_key):
            try:
                run([
                    "cast", "send", registry, "setOffline()",
                    "--rpc-url", rpc_url, "--private-key", key,
                ])
            except Exception:
                pass


def main():
    return wqpu.run_async(check()) or 0


if __name__ == "__main__":
    raise SystemExit(main())
