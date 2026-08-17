#!/usr/bin/env python3
"""Restricted public JSON-RPC gateway for a WQPU testnet.

The backing Anvil process stays on loopback. This gateway exposes only methods a
normal wallet/client needs and deliberately blocks unlocked-account/admin/debug RPCs.
"""
from __future__ import print_function

import argparse
import json
import os
import threading
import time
import urllib.request
from collections import defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY = 1024 * 1024
MAX_BATCH = 50
DEFAULT_RATE = 240
WINDOW_SECONDS = 60

ALLOWED_METHODS = frozenset([
    "web3_clientVersion", "web3_sha3", "net_version", "net_listening",
    "eth_chainId", "eth_blockNumber", "eth_syncing",
    "eth_getBalance", "eth_getCode", "eth_getStorageAt",
    "eth_getTransactionCount", "eth_getTransactionByHash", "eth_getTransactionReceipt",
    "eth_getBlockByNumber", "eth_getBlockByHash", "eth_getBlockTransactionCountByNumber",
    "eth_getBlockTransactionCountByHash", "eth_getTransactionByBlockHashAndIndex",
    "eth_getTransactionByBlockNumberAndIndex", "eth_call", "eth_estimateGas",
    "eth_gasPrice", "eth_maxPriorityFeePerGas", "eth_feeHistory", "eth_getLogs",
    "eth_sendRawTransaction",
])

BLOCKED_PREFIXES = (
    "anvil_", "hardhat_", "evm_", "debug_", "trace_", "admin_", "personal_",
    "miner_", "txpool_", "engine_", "wallet_",
)


def method_allowed(method):
    method = str(method or "")
    if method in ALLOWED_METHODS:
        return True
    if method in ("eth_accounts", "eth_sendTransaction", "eth_sign", "eth_signTransaction"):
        return False
    if method.startswith(BLOCKED_PREFIXES):
        return False
    return False


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


def jsonrpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": int(code), "message": str(message)}}


class Gateway(object):
    def __init__(self, upstream=None, timeout=15):
        self.upstream = upstream or os.environ.get("WQPU_ANVIL_RPC", "http://127.0.0.1:28545")
        self.timeout = int(timeout)

    def _forward_one(self, item):
        if not isinstance(item, dict):
            return jsonrpc_error(None, -32600, "invalid request")
        request_id = item.get("id")
        method = str(item.get("method") or "")
        if not method_allowed(method):
            return jsonrpc_error(request_id, -32601, "RPC method blocked by WQPU testnet gateway")
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": item.get("params") if isinstance(item.get("params"), list) else [],
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(self.upstream, data=raw, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                result = json.load(response)
        except Exception as exc:
            return jsonrpc_error(request_id, -32000, "upstream RPC unavailable: {}".format(exc))
        if not isinstance(result, dict):
            return jsonrpc_error(request_id, -32000, "invalid upstream RPC response")
        result["id"] = request_id
        result.setdefault("jsonrpc", "2.0")
        return result

    def handle(self, payload):
        if isinstance(payload, list):
            if not payload or len(payload) > MAX_BATCH:
                return jsonrpc_error(None, -32600, "invalid batch")
            return [self._forward_one(item) for item in payload]
        return self._forward_one(payload)


class GatewayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, gateway, rate_limit=DEFAULT_RATE):
        self.gateway = gateway
        self.rate_limiter = RateLimiter(rate_limit)
        super(GatewayHTTPServer, self).__init__(address, GatewayHandler)


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "WQPURPCGateway/0.1"

    def log_message(self, fmt, *args):
        if os.environ.get("WQPU_GATEWAY_QUIET", "0") != "1":
            super(GatewayHandler, self).log_message(fmt, *args)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")

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
            self._json(200, {"ok": True, "service": "wqpu-rpc-gateway"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path not in ("/", "/rpc"):
            self._json(404, {"error": "not found"})
            return
        peer = self.client_address[0] if self.client_address else "unknown"
        if not self.server.rate_limiter.allow(peer):
            self._json(429, {"error": "rate limit"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise ValueError("invalid request size")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self._json(200, self.server.gateway.handle(payload))
        except Exception as exc:
            self._json(400, jsonrpc_error(None, -32700, str(exc)))


def serve(host=None, port=None, upstream=None):
    host = host or os.environ.get("WQPU_GATEWAY_HOST", "0.0.0.0")
    port = int(port or os.environ.get("WQPU_GATEWAY_PORT", "8545"))
    rate = int(os.environ.get("WQPU_GATEWAY_RATE_LIMIT", str(DEFAULT_RATE)))
    server = GatewayHTTPServer((host, port), Gateway(upstream), rate)
    print("WQPU RPC gateway listening on http://{}:{}".format(host, server.server_port))
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(prog="wqpu-rpc-gateway")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--upstream", default=None)
    args = parser.parse_args()
    serve(args.host, args.port, args.upstream)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
