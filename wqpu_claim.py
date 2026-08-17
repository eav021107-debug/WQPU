#!/usr/bin/env python3
"""Gasless activation and provider-claim helpers for WQPU.

The wallet signs a bounded SpendAuthorization once. A relayer activates that session,
which reserves its maximum spend on-chain. Workers later receive compact cumulative
vouchers signed only by the local session key and can relay claims without holding a
wallet private key.
"""

from __future__ import print_function

import json
import os
import time
import urllib.request


ACTIVATE_SIGNATURE = (
    "activateSession((address,address,bytes32,uint128,uint128,uint64),bytes)"
)
CLAIM_SIGNATURE = (
    "claimEscrowWithSession(address,bytes32,(address,uint256,uint256),bytes)"
)


class ClaimError(RuntimeError):
    pass


def _strip0x(value):
    text = str(value or "")
    return text[2:] if text.startswith("0x") else text


def _address(value, label="address"):
    text = str(value or "").lower()
    if not text.startswith("0x") or len(text) != 42:
        raise ClaimError("invalid {}".format(label))
    try:
        int(text[2:], 16)
    except ValueError:
        raise ClaimError("invalid {}".format(label))
    return text


def _word(value):
    value = int(value)
    if value < 0 or value >= 2 ** 256:
        raise ClaimError("integer outside uint256")
    return "{:064x}".format(value)


def _address_word(value, label="address"):
    return _strip0x(_address(value, label)).rjust(64, "0")


def _bytes32_word(value, label="bytes32"):
    raw = _strip0x(value)
    if len(raw) != 64:
        raise ClaimError("invalid {}".format(label))
    try:
        int(raw, 16)
    except ValueError:
        raise ClaimError("invalid {}".format(label))
    return raw.lower()


def _dynamic_bytes(value, label="bytes"):
    raw = _strip0x(value)
    if len(raw) % 2:
        raise ClaimError("invalid {}".format(label))
    try:
        data = bytes.fromhex(raw)
    except ValueError:
        raise ClaimError("invalid {}".format(label))
    padded = raw.ljust(((len(data) + 31) // 32) * 64, "0")
    return _word(len(data)) + padded


def _selector(client, signature):
    hashed = client.rpc("web3_sha3", ["0x" + signature.encode("utf-8").hex()])
    raw = _strip0x(hashed)
    if len(raw) != 64:
        raise ClaimError("RPC does not support web3_sha3")
    return raw[:8]


def normalize_package(package):
    p = dict(package or {})
    if p.get("kind") != "wqpu-provider-voucher":
        raise ClaimError("not a WQPU provider voucher")
    p["market"] = _address(p.get("market"), "market")
    p["requester"] = _address(p.get("requester"), "requester")
    p["provider"] = _address(p.get("provider"), "provider")
    p["session_key"] = _address(p.get("session_key"), "session key")
    p["session_id"] = "0x" + _bytes32_word(p.get("session_id"), "session id")
    p["max_amount"] = int(p.get("max_amount") or 0)
    p["price_per_million_units"] = int(p.get("price_per_million_units") or 0)
    p["valid_until"] = int(p.get("valid_until") or 0)
    p["cumulative_amount"] = int(p.get("cumulative_amount") or 0)
    p["cumulative_units"] = int(p.get("cumulative_units") or 0)
    if p["max_amount"] <= 0 or p["max_amount"] >= 2 ** 128:
        raise ClaimError("invalid session max amount")
    if p["price_per_million_units"] <= 0 or p["price_per_million_units"] >= 2 ** 128:
        raise ClaimError("invalid session price")
    if p["valid_until"] <= 0 or p["valid_until"] >= 2 ** 64:
        raise ClaimError("invalid session expiry")
    if p["cumulative_amount"] <= 0 or p["cumulative_units"] <= 0:
        raise ClaimError("empty provider voucher")
    voucher_sig = "0x" + _strip0x(p.get("voucher_signature")).lower()
    if len(voucher_sig) != 2 + 64 * 2:
        raise ClaimError("provider voucher signature must be compact r||s")
    try:
        int(voucher_sig[2:], 16)
    except ValueError:
        raise ClaimError("invalid provider voucher signature")
    p["voucher_signature"] = voucher_sig
    return p


def normalize_activation(session):
    s = dict(session or {})
    s["market"] = _address(s.get("market"), "market")
    s["requester"] = _address(s.get("requester"), "requester")
    s["session_key"] = _address(s.get("session_key"), "session key")
    s["session_id"] = "0x" + _bytes32_word(s.get("session_id"), "session id")
    s["max_amount"] = int(s.get("max_amount") or 0)
    s["price_per_million_units"] = int(s.get("price_per_million_units") or 0)
    s["valid_until"] = int(s.get("valid_until") or 0)
    if s["max_amount"] <= 0 or s["max_amount"] >= 2 ** 128:
        raise ClaimError("invalid session max amount")
    if s["price_per_million_units"] <= 0 or s["price_per_million_units"] >= 2 ** 128:
        raise ClaimError("invalid session price")
    if s["valid_until"] <= 0 or s["valid_until"] >= 2 ** 64:
        raise ClaimError("invalid session expiry")
    auth_sig = "0x" + _strip0x(s.get("authorization_signature")).lower()
    if len(auth_sig) != 2 + 65 * 2:
        raise ClaimError("wallet authorization signature must be 65 bytes")
    try:
        int(auth_sig[2:], 16)
    except ValueError:
        raise ClaimError("invalid wallet authorization signature")
    s["authorization_signature"] = auth_sig
    return s


def activation_calldata(client, session):
    s = normalize_activation(session)
    auth_tail = _dynamic_bytes(s["authorization_signature"], "authorization signature")
    # Static authorization tuple = six words; dynamic signature offset = seventh word.
    head = "".join([
        _address_word(s["requester"], "requester"),
        _address_word(s["session_key"], "session key"),
        _bytes32_word(s["session_id"], "session id"),
        _word(s["max_amount"]),
        _word(s["price_per_million_units"]),
        _word(s["valid_until"]),
        _word(7 * 32),
    ])
    return "0x" + _selector(client, ACTIVATE_SIGNATURE) + head + auth_tail


def claim_calldata(client, package):
    p = normalize_package(package)
    voucher_tail = _dynamic_bytes(p["voucher_signature"], "voucher signature")
    # requester + sessionId + static ProviderVoucher tuple (3 words) + bytes offset.
    head = "".join([
        _address_word(p["requester"], "requester"),
        _bytes32_word(p["session_id"], "session id"),
        _address_word(p["provider"], "provider"),
        _word(p["cumulative_amount"]),
        _word(p["cumulative_units"]),
        _word(6 * 32),
    ])
    return "0x" + _selector(client, CLAIM_SIGNATURE) + head + voucher_tail


def _simulate(client, market, data):
    result = client.rpc("eth_call", [{"to": market, "data": data}, "latest"])
    if not isinstance(result, str) or not result.startswith("0x"):
        raise ClaimError("bad EVM simulation result")
    return True


def simulate_activation(client, session):
    s = normalize_activation(session)
    return _simulate(client, s["market"], activation_calldata(client, s))


def simulate_claim(client, package):
    p = normalize_package(package)
    return _simulate(client, p["market"], claim_calldata(client, p))


def _unlocked_sender(client, sender=None):
    if sender is None:
        accounts = client.rpc("eth_accounts", [])
        if not accounts:
            raise ClaimError("RPC exposes no relayer account")
        sender = accounts[0]
    return _address(sender, "relayer")


def activate_via_rpc(client, session, sender=None):
    """Dev/test helper for an RPC that deliberately exposes an unlocked relayer."""
    s = normalize_activation(session)
    simulate_activation(client, s)
    tx_hash = client.rpc("eth_sendTransaction", [{
        "from": _unlocked_sender(client, sender),
        "to": s["market"],
        "data": activation_calldata(client, s),
    }])
    if not isinstance(tx_hash, str) or not tx_hash.startswith("0x"):
        raise ClaimError("bad activation transaction hash")
    return tx_hash


def relay_via_rpc(client, package, sender=None):
    """Dev/test helper for an RPC that deliberately exposes an unlocked relayer."""
    p = normalize_package(package)
    simulate_claim(client, p)
    tx_hash = client.rpc("eth_sendTransaction", [{
        "from": _unlocked_sender(client, sender),
        "to": p["market"],
        "data": claim_calldata(client, p),
    }])
    if not isinstance(tx_hash, str) or not tx_hash.startswith("0x"):
        raise ClaimError("bad relayer transaction hash")
    return tx_hash


def wait_receipt(client, tx_hash, timeout=60):
    deadline = time.time() + float(timeout)
    while time.time() < deadline:
        receipt = client.rpc("eth_getTransactionReceipt", [tx_hash])
        if receipt:
            if int(receipt.get("status", "0x0"), 16) != 1:
                raise ClaimError("transaction reverted")
            return receipt
        time.sleep(0.25)
    raise ClaimError("transaction confirmation timed out")


def _relayer_url(client):
    return (
        os.environ.get("WQPU_RELAYER_URL", "").strip()
        or str(getattr(client, "network", {}).get("relayer_url") or "").strip()
    )


def _relay_http(url, kind, field, value, timeout=20):
    payload = json.dumps({"kind": kind, field: value}).encode("utf-8")
    request = urllib.request.Request(
        str(url),
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "WQPU-relay/0.6"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except Exception as exc:
        raise ClaimError("relayer request failed: {}".format(exc))
    tx_hash = str((result or {}).get("tx_hash") or "")
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        raise ClaimError("relayer returned no transaction hash")
    return tx_hash


def relay_activation(client, session):
    s = normalize_activation(session)
    simulate_activation(client, s)
    url = _relayer_url(client)
    if url:
        return _relay_http(url, "wqpu-relay-activation", "session", s)
    if os.environ.get("WQPU_ALLOW_UNLOCKED_RPC_RELAYER", "0") == "1":
        return activate_via_rpc(client, s)
    raise ClaimError(
        "session authorization is valid but no relayer is configured; set WQPU_RELAYER_URL"
    )


def relay(client, package):
    p = normalize_package(package)
    simulate_claim(client, p)
    url = _relayer_url(client)
    if url:
        return _relay_http(url, "wqpu-relay-claim", "voucher", p)
    if os.environ.get("WQPU_ALLOW_UNLOCKED_RPC_RELAYER", "0") == "1":
        return relay_via_rpc(client, p)
    raise ClaimError(
        "claim is valid but no relayer is configured; set WQPU_RELAYER_URL"
    )
