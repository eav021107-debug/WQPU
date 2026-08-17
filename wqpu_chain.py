#!/usr/bin/env python3
"""Small dependency-free WQPU EVM registry reader.

The runtime never receives a seed phrase/private key. Reads happen through JSON-RPC;
wallet writes are done by the browser wallet connector.
"""

from __future__ import print_function

import hashlib
import json
import os
import re
import time
import urllib.request
from pathlib import Path
try:
    from urllib.parse import urlparse
except ImportError:  # pragma: no cover - Python 2 is unsupported, kept harmlessly portable.
    from urlparse import urlparse


MEMBER_COUNT_SELECTOR = "11aee380"              # memberCount()
MEMBER_AT_SELECTOR = "ac0250f7"                 # memberAt(uint256)
GLOBAL_PRICE_SELECTOR = "87a51cc9"              # globalPricePerMillionUnits()
BPS = 10000
NETWORK_FILE = Path(__file__).resolve().with_name("network-config.json")
PUBLIC_PROTOCOL = "wqpu-public-v1"
NETWORK_CONFIG_VERSION = 3
EXPECTED_LLAMA_CPP_TAG = "b10456"
EXPECTED_LLAMA_RPC_PROTOCOL_MAJOR = 5
EXPECTED_LLAMA_RPC_OP_COUNT = 101


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
    try:
        int(value[2:], 16)
    except Exception:
        raise ChainError("invalid EVM address")
    return value


def normalize_chain_id(value):
    if value is None or value == "":
        return None
    try:
        if isinstance(value, int):
            if value < 0:
                raise ValueError("negative chain id")
            return "0x{:x}".format(value)
        text = str(value).strip().lower()
        number = int(text, 16) if text.startswith("0x") else int(text)
        if number < 0:
            raise ValueError("negative chain id")
        return "0x{:x}".format(number)
    except Exception:
        raise ChainError("invalid chain id")


def compute_network_uid(chain_id, token, registry, market):
    chain_id = normalize_chain_id(chain_id)
    if not chain_id:
        raise ChainError("network uid requires chain id")
    token = normalize_address(token)
    registry = normalize_address(registry)
    market = normalize_address(market)
    canonical = "{}|{}|{}|{}|{}".format(
        PUBLIC_PROTOCOL, chain_id, token, registry, market
    ).encode("ascii")
    return "wqpu-" + hashlib.sha256(canonical).hexdigest()[:32]


def _require_http_url(value, field):
    text = str(value or "").strip()
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ChainError("invalid {} URL".format(field))
    if parsed.username or parsed.password:
        raise ChainError("credentials are not allowed in {} URL".format(field))
    return text


def _validate_relay(relay):
    if not isinstance(relay, dict):
        raise ChainError("invalid WQPU relay entry")
    host = str(relay.get("host") or "").strip()
    if not host:
        raise ChainError("WQPU relay host is required")
    try:
        port = int(relay.get("port"))
    except Exception:
        raise ChainError("invalid WQPU relay port")
    if port < 1 or port > 65535:
        raise ChainError("invalid WQPU relay port")
    fingerprint = str(relay.get("fingerprint") or "").lower().replace("0x", "")
    if not re.match(r"^[0-9a-f]{64}$", fingerprint):
        raise ChainError("invalid WQPU relay TLS fingerprint")


def validate_network_config(root, public):
    """Validate an enabled published network config.

    v3 is the first fail-closed config: protocol, deterministic identity and pinned
    llama.cpp RPC ABI must match this client exactly. Legacy v1/v2 configs remain
    readable for backward-compatible local/dev workflows.
    """
    if not isinstance(root, dict) or not isinstance(public, dict):
        raise ChainError("invalid WQPU network config")
    raw_version = root.get("version")
    if raw_version in (None, ""):
        return dict(public)
    try:
        version = int(raw_version)
    except Exception:
        raise ChainError("invalid WQPU network config version")
    if version < 1 or version > NETWORK_CONFIG_VERSION:
        raise ChainError(
            "unsupported WQPU network config version {}; client supports through {}".format(
                version, NETWORK_CONFIG_VERSION
            )
        )
    if version < NETWORK_CONFIG_VERSION:
        return dict(public)

    if str(public.get("protocol") or "") != PUBLIC_PROTOCOL:
        raise ChainError("incompatible WQPU public protocol")
    chain_id = normalize_chain_id(public.get("chain_id"))
    token = normalize_address(public.get("token"))
    registry = normalize_address(public.get("registry"))
    market = normalize_address(public.get("market"))
    expected_uid = compute_network_uid(chain_id, token, registry, market)
    if str(public.get("network_uid") or "").lower() != expected_uid:
        raise ChainError("WQPU network_uid does not match chain/contracts")

    _require_http_url(public.get("rpc_url"), "rpc_url")
    for field in ("relayer_url", "faucet_url"):
        if public.get(field):
            _require_http_url(public.get(field), field)

    if str(public.get("llama_cpp_tag") or "") != EXPECTED_LLAMA_CPP_TAG:
        raise ChainError("incompatible pinned llama.cpp tag")
    try:
        rpc_major = int(public.get("llama_rpc_protocol_major"))
        op_count = int(public.get("llama_rpc_op_count"))
    except Exception:
        raise ChainError("invalid pinned llama.cpp RPC metadata")
    if rpc_major != EXPECTED_LLAMA_RPC_PROTOCOL_MAJOR:
        raise ChainError("incompatible llama.cpp RPC protocol major")
    if op_count != EXPECTED_LLAMA_RPC_OP_COUNT:
        raise ChainError("incompatible llama.cpp RPC op count")

    relays = public.get("relays") or []
    if not isinstance(relays, list):
        raise ChainError("WQPU relays must be a list")
    for relay in relays:
        _validate_relay(relay)
    return dict(public)


def load_network_config(path=None):
    target = Path(path) if path else NETWORK_FILE
    try:
        root = json.loads(target.read_text())
    except Exception:
        return {}
    public = root.get("public") if isinstance(root, dict) else None
    if not isinstance(public, dict) or not public.get("enabled"):
        return {}
    return validate_network_config(root, public)


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
        self.network = load_network_config()
        self.rpc_url = (
            rpc_url
            or os.environ.get("WQPU_RPC_URL")
            or self.network.get("rpc_url")
            or ""
        )
        registry = (
            registry
            or os.environ.get("WQPU_REGISTRY")
            or self.network.get("registry")
            or ""
        )
        self.registry = normalize_address(registry) if registry else ""
        expected = os.environ.get("WQPU_CHAIN_ID") or self.network.get("chain_id")
        self.expected_chain_id = normalize_chain_id(expected) if expected not in (None, "") else None
        self.timeout = timeout
        self._rpc_id = 0

    @property
    def configured(self):
        return bool(self.rpc_url and self.registry)

    def rpc(self, method, params):
        if not self.configured:
            raise ChainError("WQPU chain is not configured")
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
        value = normalize_chain_id(value)
        if self.expected_chain_id and value != self.expected_chain_id:
            raise ChainError(
                "wrong WQPU chain: expected {}, RPC returned {}".format(
                    self.expected_chain_id, value
                )
            )
        return value

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
        now = self.latest_timestamp() if max_age else 0
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
        "chain_id": client.expected_chain_id,
        "network": client.network,
    }


if __name__ == "__main__":
    client = RegistryClient()
    if not client.configured:
        raise SystemExit("WQPU public chain is not configured")
    print(json.dumps({
        "chain_id": client.chain_id(),
        "price_per_million_units": client.global_price(),
        "nodes": client.discover(),
        "checked_at": int(time.time()),
    }, indent=2))