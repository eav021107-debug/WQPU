#!/usr/bin/env python3
"""Small dependency-free WQPU EVM registry reader.

The runtime never receives a seed phrase/private key. Reads happen through JSON-RPC;
wallet writes/signatures are done by the browser wallet connector.
"""

from __future__ import print_function

import json
import os
import time
import urllib.request


MEMBER_COUNT_SELECTOR = "11aee380"              # memberCount()
MEMBER_AT_SELECTOR = "ac0250f7"                 # memberAt(uint256)
GLOBAL_PRICE_SELECTOR = "87a51cc9"              # globalPricePerMillionUnits()
BPS = 10000


class ChainError(RuntimeError):
    pass


def _strip0x(value):
    return value[2:] if isinstance(value, str) and value.startswith("0x") else value


def _word(raw, offset):
    part = raw[offset:offset + 64]
    if len(part) != 64:
        raise ChainError("short ABI word")
    return part


def _uint(raw, offset):
    return int(_word(raw, offset), 16)


def _address(raw, offset):
    return "0x" + _word(raw, offset)[24:]


def _bytes32(raw, offset):
    return "0x" + _word(raw, offset)


def _string(raw, base, relative_offset):
    start = base + relative_offset * 2
    length = _uint(raw, start)
    data_start = start + 64
    data_end = data_start + length * 2
    try:
        return bytes.fromhex(raw[data_start:data_end]).decode("utf-8")
    except Exception as exc:
        raise ChainError("invalid ABI string: {}".format(exc))


def normalize_address(value):
    value = str(value or "").strip().lower()
    if not value.startswith("0x") or len(value) != 42:
        raise ChainError("invalid EVM address")
    int(value[2:], 16)
    return value


def parse_endpoint(endpoint):
    endpoint = str(endpoint or "").strip()
    if not endpoint:
        raise ChainError("empty endpoint")
    if endpoint.startswith("["):
        end = endpoint.find("]")
        if end < 0 or len(endpoint) <= end + 2 or endpoint[end + 1] != ":":
            raise ChainError("bad IPv6 endpoint")
        host = endpoint[1:end]
        port = int(endpoint[end + 2:])
    else:
        if endpoint.count(":") != 1:
            raise ChainError("endpoint must be HOST:PORT")
        host, port_s = endpoint.rsplit(":", 1)
        port = int(port_s)
    if not host or port < 1 or port > 65535:
        raise ChainError("bad endpoint")
    return host, port


class RegistryClient(object):
    def __init__(self, rpc_url=None, registry=None, timeout=10):
        self.rpc_url = rpc_url or os.environ.get("WQPU_RPC_URL", "http://127.0.0.1:8545")
        registry = registry or os.environ.get("WQPU_REGISTRY", "")
        self.registry = normalize_address(registry) if registry else ""
        self.timeout = timeout
        self._rpc_id = 0

    @property
    def configured(self):
        return bool(self.rpc_url and self.registry)

    def rpc(self, method, params):
        if not self.configured:
            raise ChainError("WQPU chain is not configured (set WQPU_RPC_URL and WQPU_REGISTRY)")
        self._rpc_id += 1
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": self._rpc_id,
            "method": method,
            "params": params,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.rpc_url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "WQPU-chain/0.6"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.load(response)
        except Exception as exc:
            raise ChainError("RPC request failed: {}".format(exc))
        if body.get("error"):
            raise ChainError("RPC error: {}".format(body["error"]))
        return body.get("result")

    def chain_id(self):
        value = self.rpc("eth_chainId", [])
        if not isinstance(value, str) or not value.startswith("0x"):
            raise ChainError("bad eth_chainId result")
        int(value, 16)
        return value.lower()

    def eth_call(self, data):
        result = self.rpc("eth_call", [{"to": self.registry, "data": "0x" + data}, "latest"])
        if not isinstance(result, str) or not result.startswith("0x"):
            raise ChainError("bad eth_call result")
        return _strip0x(result)

    def latest_timestamp(self):
        block = self.rpc("eth_getBlockByNumber", ["latest", False])
        if not isinstance(block, dict) or "timestamp" not in block:
            raise ChainError("bad latest block")
        return int(block["timestamp"], 16)

    def global_price(self):
        raw = self.eth_call(GLOBAL_PRICE_SELECTOR)
        return _uint(raw, 0)

    def member_count(self):
        raw = self.eth_call(MEMBER_COUNT_SELECTOR)
        return _uint(raw, 0)

    def member_at(self, index):
        data = MEMBER_AT_SELECTOR + ("{:064x}".format(int(index)))
        raw = self.eth_call(data)

        wallet = _address(raw, 0).lower()
        tuple_offset_bytes = _uint(raw, 64)
        base = tuple_offset_bytes * 2

        endpoint_rel = _uint(raw, base)
        fingerprint = _bytes32(raw, base + 64)
        capacity = _uint(raw, base + 128)
        load_bps = _uint(raw, base + 192)
        updated_at = _uint(raw, base + 256)
        active = bool(_uint(raw, base + 320))
        endpoint = _string(raw, base, endpoint_rel)

        if load_bps > BPS:
            raise ChainError("registry returned invalid load")
        parse_endpoint(endpoint)
        return {
            "wallet": wallet,
            "endpoint": endpoint,
            "fingerprint": fingerprint,
            "capacity": capacity,
            "load_bps": load_bps,
            "updated_at": updated_at,
            "active": active,
        }

    def find_wallet(self, wallet, max_nodes=512):
        target = normalize_address(wallet)
        count = min(self.member_count(), int(max_nodes))
        for index in range(count):
            try:
                node = self.member_at(index)
            except Exception:
                continue
            if node["wallet"] == target:
                return node
        return None

    def discover(self, exclude_wallet=None, max_nodes=512, max_age=180):
        exclude = normalize_address(exclude_wallet) if exclude_wallet else None
        count = min(self.member_count(), int(max_nodes))
        now = self.latest_timestamp()
        nodes = []
        for index in range(count):
            try:
                node = self.member_at(index)
            except Exception:
                continue
            if not node["active"]:
                continue
            if exclude and node["wallet"] == exclude:
                continue
            if max_age and now - node["updated_at"] > int(max_age):
                continue
            node["available_capacity"] = max(
                0,
                (node["capacity"] * (BPS - node["load_bps"])) // BPS,
            )
            if node["available_capacity"] <= 0:
                continue
            nodes.append(node)

        nodes.sort(key=lambda n: (n["load_bps"], -n["available_capacity"], -n["updated_at"]))
        return nodes

    def choose_workers(self, required_capacity, exclude_wallet=None, max_nodes=512, max_age=180):
        required = max(1, int(required_capacity))
        selected = []
        total = 0
        for node in self.discover(exclude_wallet, max_nodes=max_nodes, max_age=max_age):
            selected.append(node)
            total += node["available_capacity"]
            if total >= required:
                break
        return selected, total


def chain_config_from_env():
    client = RegistryClient()
    return {
        "configured": client.configured,
        "rpc_url": client.rpc_url,
        "registry": client.registry,
    }


if __name__ == "__main__":
    client = RegistryClient()
    if not client.configured:
        raise SystemExit("set WQPU_RPC_URL and WQPU_REGISTRY")
    print(json.dumps({
        "chain_id": client.chain_id(),
        "price_per_million_units": client.global_price(),
        "nodes": client.discover(),
        "checked_at": int(time.time()),
    }, indent=2))
