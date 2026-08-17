#!/usr/bin/env python3
"""Minimal WQPU gas relayer service.

The relayer pays only chain gas. It cannot choose provider payouts or requester limits:
all meaningful values are verified by WQPU contracts against wallet/session signatures.
Production deployments should keep the relayer account in a dedicated signer/HSM; this
prototype can use an explicitly unlocked RPC account for devnet/operator testing.
"""

from __future__ import print_function

import argparse
import json
import os
import threading
import time
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from wqpu_chain import RegistryClient, normalize_address
from wqpu_claim import (
    activate_via_rpc,
    fund_via_rpc,
    normalize_activation,
    normalize_funding,
    normalize_package,
    relay_via_rpc,
)


MAX_BODY = 64 * 1024
DEFAULT_RATE = 30
WINDOW_SECONDS = 60


class RelayerError(RuntimeError):
    pass


def configured_market(client):
    value = os.environ.get("WQPU_MARKET") or client.network.get("market") or ""
    if not value:
        raise RelayerError("WQPU market is not configured")
    return normalize_address(value)


def configured_sender(client):
    explicit = os.environ.get("WQPU_RELAYER_FROM", "").strip()
    if explicit:
        return normalize_address(explicit)
    if os.environ.get("WQPU_RELAYER_ALLOW_RPC_ACCOUNT", "0") != "1":
        raise RelayerError("set WQPU_RELAYER_FROM or explicitly allow an unlocked RPC account")
    accounts = client.rpc("eth_accounts", [])
    if not accounts:
        raise RelayerError("RPC exposes no relayer account")
    return normalize_address(accounts[0])


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
    def __init__(self, client=None, sender=None, market=None):
        self.client = client or RegistryClient()
        if not self.client.configured:
            raise RelayerError("WQPU chain is not configured")
        self.market = normalize_address(market) if market else configured_market(self.client)
        self.sender = normalize_address(sender) if sender else configured_sender(self.client)

    def _market_guard(self, payload):
        market = normalize_address(payload.get("market"))
        if market != self.market:
            raise RelayerError("payload targets a different market")

    def submit(self, body):
        if not isinstance(body, dict):
            raise RelayerError("JSON object required")
        kind = str(body.get("kind") or "")
        if kind == "wqpu-relay-funding":
            funding = normalize_funding(body.get("funding"))
            self._market_guard(funding)
            return fund_via_rpc(self.client, funding, self.sender)
        if kind == "wqpu-relay-activation":
            session = normalize_activation(body.get("session"))
            self._market_guard(session)
            return activate_via_rpc(self.client, session, self.sender)
        if kind == "wqpu-relay-claim":
            voucher = normalize_package(body.get("voucher"))
            self._market_guard(voucher)
            return relay_via_rpc(self.client, voucher, self.sender)
        raise RelayerError("unsupported relayer request")


class RelayerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, relayer, rate_limit=DEFAULT_RATE):
        self.relayer = relayer
        self.rate_limiter = RateLimiter(rate_limit)
        super(RelayerHTTPServer, self).__init__(address, RelayerHandler)


class RelayerHandler(BaseHTTPRequestHandler):
    server_version = "WQPURelayer/0.6"

    def log_message(self, fmt, *args):
        if os.environ.get("WQPU_RELAYER_QUIET", "0") != "1":
            super(RelayerHandler, self).log_message(fmt, *args)

    def _json(self, status, value):
        raw = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "service": "wqpu-relayer"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/", "/relay"):
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
            tx_hash = self.server.relayer.submit(body)
            self._json(200, {"tx_hash": tx_hash})
        except Exception as exc:
            self._json(400, {"error": str(exc)})


def serve(host=None, port=None):
    host = host or os.environ.get("WQPU_RELAYER_HOST", "127.0.0.1")
    port = int(port or os.environ.get("WQPU_RELAYER_PORT", "8787"))
    rate = int(os.environ.get("WQPU_RELAYER_RATE_LIMIT", str(DEFAULT_RATE)))
    server = RelayerHTTPServer((host, port), Relayer(), rate)
    print("WQPU relayer listening on http://{}:{}".format(host, server.server_port))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(prog="wqpu-relayer")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
