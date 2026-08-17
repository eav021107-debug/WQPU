#!/usr/bin/env python3
"""WQPU gas relayer and optional testnet faucet.

The relayer pays chain gas for funding/session/claim transactions. Production can use
an unlocked dedicated RPC signer as before. Testnet operators may instead provide a
private key via WQPU_RELAYER_PRIVATE_KEY; it is never sent to WQPU clients.
"""
from __future__ import print_function

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wqpu_chain import RegistryClient, normalize_address
from wqpu_claim import (
    activation_calldata,
    claim_calldata,
    funding_calldata,
    normalize_activation,
    normalize_funding,
    normalize_package,
    simulate_activation,
    simulate_claim,
    simulate_funding,
)

MAX_BODY = 64 * 1024
DEFAULT_RATE = 30
WINDOW_SECONDS = 60
TRANSFER_SELECTOR = "a9059cbb"
BALANCE_OF_SELECTOR = "70a08231"


class RelayerError(RuntimeError):
    pass


def configured_market(client):
    value = os.environ.get("WQPU_MARKET") or client.network.get("market") or ""
    if not value:
        raise RelayerError("WQPU market is not configured")
    return normalize_address(value)


def configured_token(client):
    value = os.environ.get("WQPU_TOKEN") or client.network.get("token") or ""
    return normalize_address(value) if value else ""


def _run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if proc.returncode != 0:
        raise RelayerError("relayer signer command failed: {}".format(proc.stdout.strip()))
    return proc.stdout


def _normalize_private_key(value):
    value = str(value or "").strip().lower()
    if not value:
        return ""
    if not re.match(r"^0x[0-9a-f]{64}$", value):
        raise RelayerError("WQPU_RELAYER_PRIVATE_KEY must be a 32-byte hex key")
    if int(value, 16) == 0:
        raise RelayerError("invalid relayer private key")
    return value


def top_up_amount(target, current):
    target = max(0, int(target))
    current = max(0, int(current))
    return max(0, target - current)


def _hex_uint(value):
    value = str(value or "0x0")
    try:
        return int(value, 16) if value.startswith("0x") else int(value)
    except Exception:
        raise RelayerError("invalid uint result from RPC")


def sender_from_private_key(private_key):
    if not shutil.which("cast"):
        raise RelayerError("cast is required for private-key relayer mode")
    out = _run(["cast", "wallet", "address", "--private-key", private_key])
    matches = re.findall(r"0x[0-9a-fA-F]{40}", out)
    if not matches:
        raise RelayerError("could not derive relayer address")
    return normalize_address(matches[-1])


def configured_sender(client, private_key=""):
    if private_key:
        return sender_from_private_key(private_key)
    explicit = os.environ.get("WQPU_RELAYER_FROM", "").strip()
    if explicit:
        return normalize_address(explicit)
    if os.environ.get("WQPU_RELAYER_ALLOW_RPC_ACCOUNT", "0") != "1":
        raise RelayerError("set WQPU_RELAYER_PRIVATE_KEY/WQPU_RELAYER_FROM or explicitly allow an unlocked RPC account")
    accounts = client.rpc("eth_accounts", [])
    if not accounts:
        raise RelayerError("RPC exposes no relayer account")
    return normalize_address(accounts[0])


def validate_tls_pair(cert, key):
    cert = str(cert or "").strip()
    key = str(key or "").strip()
    if bool(cert) != bool(key):
        raise RelayerError("both TLS certificate and key are required")
    return cert, key


def enable_tls(server, cert, key):
    cert, key = validate_tls_pair(cert, key)
    if not cert:
        return False
    context = ssl.SSLContext(getattr(ssl, "PROTOCOL_TLS_SERVER", ssl.PROTOCOL_TLS))
    context.load_cert_chain(certfile=cert, keyfile=key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return True


class RateLimiter(object):
    def __init__(self, limit=DEFAULT_RATE):
        self.limit = max(1, int(limit))
        self.rows = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key):
        now = time.time()
        cutoff = now - WINDOW_SECONDS
        with self.lock:
            row = self.rows[key]
            while row and row[0] < cutoff:
                row.popleft()
            if len(row) >= self.limit:
                return False
            row.append(now)
            return True


class Relayer(object):
    def __init__(self, client=None, sender=None, market=None, private_key=None):
        self.client = client or RegistryClient()
        if not self.client.configured:
            raise RelayerError("WQPU chain is not configured")
        self.market = normalize_address(market) if market else configured_market(self.client)
        self.private_key = _normalize_private_key(
            private_key if private_key is not None else os.environ.get("WQPU_RELAYER_PRIVATE_KEY", "")
        )
        self.sender = normalize_address(sender) if sender else configured_sender(self.client, self.private_key)
        self.token = configured_token(self.client)
        self.faucet_enabled = os.environ.get("WQPU_TESTNET_FAUCET", "0") == "1"
        old_native = os.environ.get("WQPU_FAUCET_NATIVE_WEI", str(2 * 10 ** 18))
        old_token = os.environ.get("WQPU_FAUCET_TOKEN_WEI", str(1000 * 10 ** 18))
        self.faucet_native_target = int(os.environ.get("WQPU_FAUCET_NATIVE_TARGET_WEI", old_native))
        self.faucet_token_target = int(os.environ.get("WQPU_FAUCET_TOKEN_TARGET_WEI", old_token))
        self.faucet_interval = int(os.environ.get("WQPU_FAUCET_INTERVAL", "3600"))
        self._faucet_last = {}
        self._faucet_lock = threading.Lock()

    def _market_guard(self, payload):
        market = normalize_address(payload.get("market"))
        if market != self.market:
            raise RelayerError("payload targets a different market")

    def _cast_send(self, to, data="0x", value=0):
        if not shutil.which("cast"):
            raise RelayerError("cast is required for private-key relayer mode")
        cmd = ["cast", "send", normalize_address(to)]
        if data and str(data) != "0x":
            cmd.append(str(data))
        cmd += [
            "--rpc-url", self.client.rpc_url,
            "--private-key", self.private_key,
            "--json",
        ]
        if int(value) > 0:
            cmd += ["--value", str(int(value))]
        out = _run(cmd)
        try:
            parsed = json.loads(out)
            for key in ("transactionHash", "transaction_hash", "txHash", "hash"):
                found = parsed.get(key) if isinstance(parsed, dict) else None
                if isinstance(found, str) and re.match(r"^0x[0-9a-fA-F]{64}$", found):
                    return found
        except Exception:
            pass
        matches = re.findall(r"0x[0-9a-fA-F]{64}", out)
        if not matches:
            raise RelayerError("could not parse relayer transaction hash")
        return matches[-1]

    def _send(self, to, data="0x", value=0):
        if self.private_key:
            return self._cast_send(to, data, value)
        tx = {"from": self.sender, "to": normalize_address(to)}
        if data and data != "0x":
            tx["data"] = str(data)
        if int(value) > 0:
            tx["value"] = hex(int(value))
        tx_hash = self.client.rpc("eth_sendTransaction", [tx])
        if not isinstance(tx_hash, str) or not tx_hash.startswith("0x"):
            raise RelayerError("bad relayer transaction hash")
        return tx_hash

    def _native_balance(self, wallet):
        return _hex_uint(self.client.rpc("eth_getBalance", [wallet, "latest"]))

    def _token_balance(self, wallet):
        if not self.token:
            return 0
        data = "0x" + BALANCE_OF_SELECTOR + wallet[2:].rjust(64, "0")
        return _hex_uint(self.client.rpc("eth_call", [{"to": self.token, "data": data}, "latest"]))

    def submit(self, body):
        if not isinstance(body, dict):
            raise RelayerError("JSON object required")
        kind = str(body.get("kind") or "")
        if kind == "wqpu-relay-funding":
            funding = normalize_funding(body.get("funding"))
            self._market_guard(funding)
            simulate_funding(self.client, funding)
            return self._send(self.market, funding_calldata(self.client, funding))
        if kind == "wqpu-relay-activation":
            session = normalize_activation(body.get("session"))
            self._market_guard(session)
            simulate_activation(self.client, session)
            return self._send(self.market, activation_calldata(self.client, session))
        if kind == "wqpu-relay-claim":
            voucher = normalize_package(body.get("voucher"))
            self._market_guard(voucher)
            simulate_claim(self.client, voucher)
            return self._send(self.market, claim_calldata(self.client, voucher))
        raise RelayerError("unsupported relayer request")

    def faucet(self, wallet):
        if not self.faucet_enabled:
            raise RelayerError("testnet faucet is disabled")
        wallet = normalize_address(wallet)
        now = time.time()
        with self._faucet_lock:
            last = float(self._faucet_last.get(wallet) or 0)
            if last and now - last < self.faucet_interval:
                return {"wallet": wallet, "already_funded": True, "transactions": []}
            # Reserve the cooldown immediately so concurrent requests for one wallet cannot
            # race and each send a full top-up. Roll it back if a transaction fails.
            self._faucet_last[wallet] = now
        txs = []
        try:
            native_before = self._native_balance(wallet)
            token_before = self._token_balance(wallet) if self.token else 0
            native_needed = top_up_amount(self.faucet_native_target, native_before)
            token_needed = top_up_amount(self.faucet_token_target, token_before) if self.token else 0
            if native_needed > 0:
                txs.append(self._send(wallet, "0x", native_needed))
            if self.token and token_needed > 0:
                data = "0x" + TRANSFER_SELECTOR + wallet[2:].rjust(64, "0") + "{:064x}".format(token_needed)
                txs.append(self._send(self.token, data, 0))
        except Exception:
            with self._faucet_lock:
                self._faucet_last.pop(wallet, None)
            raise
        return {
            "wallet": wallet,
            "already_funded": not bool(txs),
            "transactions": txs,
            "native_before": native_before,
            "native_target": self.faucet_native_target,
            "token_before": token_before,
            "token_target": self.faucet_token_target if self.token else 0,
        }


class RelayerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, relayer, rate_limit=DEFAULT_RATE):
        self.relayer = relayer
        self.rate_limiter = RateLimiter(rate_limit)
        super(RelayerHTTPServer, self).__init__(address, RelayerHandler)


class RelayerHandler(BaseHTTPRequestHandler):
    server_version = "WQPURelayer/0.9"

    def log_message(self, fmt, *args):
        if os.environ.get("WQPU_RELAYER_QUIET", "0") != "1":
            super(RelayerHandler, self).log_message(fmt, *args)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, status, value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "service": "wqpu-relayer", "faucet": self.server.relayer.faucet_enabled})
            return
        if self.path == "/network-config.json":
            target = os.environ.get("WQPU_NETWORK_CONFIG", "").strip()
            if not target:
                self._json(404, {"error": "network config not published"})
                return
            try:
                value = json.loads(Path(target).read_text())
                self._json(200, value)
            except Exception as exc:
                self._json(500, {"error": "network config unavailable: {}".format(exc)})
            return
        if self.path in ("/join.sh", "/join.ps1"):
            env_name = "WQPU_JOIN_SH" if self.path.endswith(".sh") else "WQPU_JOIN_PS1"
            target = os.environ.get(env_name, "").strip()
            if not target:
                self._json(404, {"error": "join script not published"})
                return
            try:
                raw = Path(target).read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self._cors()
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception as exc:
                self._json(500, {"error": "join script unavailable: {}".format(exc)})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/", "/relay", "/faucet"):
            self._json(404, {"error": "not found"})
            return
        peer = self.client_address[0] if self.client_address else "unknown"
        if not self.server.rate_limiter.allow(peer):
            self._json(429, {"error": "rate limit"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise RelayerError("invalid request size")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if self.path == "/faucet":
                self._json(200, self.server.relayer.faucet(body.get("wallet")))
                return
            tx_hash = self.server.relayer.submit(body)
            self._json(200, {"tx_hash": tx_hash})
        except Exception as exc:
            self._json(400, {"error": str(exc)})


def serve(host=None, port=None, tls_cert=None, tls_key=None):
    host = host or os.environ.get("WQPU_RELAYER_HOST", "127.0.0.1")
    port = int(port or os.environ.get("WQPU_RELAYER_PORT", "8787"))
    rate = int(os.environ.get("WQPU_RELAYER_RATE_LIMIT", str(DEFAULT_RATE)))
    tls_cert = tls_cert or os.environ.get("WQPU_RELAYER_TLS_CERT", "")
    tls_key = tls_key or os.environ.get("WQPU_RELAYER_TLS_KEY", "")
    server = RelayerHTTPServer((host, port), Relayer(), rate)
    secure = enable_tls(server, tls_cert, tls_key)
    scheme = "https" if secure else "http"
    print("WQPU relayer listening on {}://{}:{}".format(scheme, host, server.server_port))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(prog="wqpu-relayer")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--tls-cert", default=None)
    parser.add_argument("--tls-key", default=None)
    args = parser.parse_args()
    serve(args.host, args.port, args.tls_cert, args.tls_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())