#!/usr/bin/env python3
"""Provider-side WQPU claim helpers.

A worker never needs its wallet private key. It receives a cumulative claim package
signed by the requester's bounded session key, simulates it against the market, then
hands the same calldata to any gas-paying relayer. The contract always pays the
registered provider address contained in the signed voucher.
"""

from __future__ import print_function

import json
import os
import time
import urllib.request


CLAIM_SIGNATURE = (
    "claimEscrowWithSession("
    "(address,address,bytes32,uint128,uint128,uint64),"
    "(address,uint256,uint256),bytes,bytes)"
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
    auth_sig = "0x" + _strip0x(p.get("authorization_signature")).lower()
    if len(voucher_sig) != 2 + 64 * 2:
        raise ClaimError("provider voucher signature must be compact r||s")
    if len(auth_sig) != 2 + 65 * 2:
        raise ClaimError("wallet authorization signature must be 65 bytes")
    int(voucher_sig[2:], 16)
    int(auth_sig[2:], 16)
    p["voucher_signature"] = voucher_sig
    p["authorization_signature"] = auth_sig
    return p


def claim_calldata(client, package):
    p = normalize_package(package)
    voucher_tail = _dynamic_bytes(p["voucher_signature"], "voucher signature")
    auth_tail = _dynamic_bytes(p["authorization_signature"], "authorization signature")

    # Both structs are fully static. The top-level ABI head is therefore
    # 6 words (authorization) + 3 words (provider voucher) + 2 dynamic offsets.
    head_words = 11
    first_offset = head_words * 32
    second_offset = first_offset + len(voucher_tail) // 2
    head = "".join([
        _address_word(p["requester"], "requester"),
        _address_word(p["session_key"], "session key"),
        _bytes32_word(p["session_id"], "session id"),
        _word(p["max_amount"]),
        _word(p["price_per_million_units"]),
        _word(p["valid_until"]),
        _address_word(p["provider"], "provider"),
        _word(p["cumulative_amount"]),
        _word(p["cumulative_units"]),
        _word(first_offset),
        _word(second_offset),
    ])
    return "0x" + _selector(client, CLAIM_SIGNATURE) + head + voucher_tail + auth_tail


def simulate_claim(client, package):
    p = normalize_package(package)
    data = claim_calldata(client, p)
    result = client.rpc("eth_call", [{"to": p["market"], "data": data}, "latest"])
    if not isinstance(result, str) or not result.startswith("0x"):
        raise ClaimError("bad claim simulation result")
    return True


def relay_via_rpc(client, package, sender=None):
    """Use only with an RPC that deliberately exposes an unlocked relayer account."""
    p = normalize_package(package)
    simulate_claim(client, p)
    if sender is None:
        accounts = client.rpc("eth_accounts", [])
        if not accounts:
            raise ClaimError("RPC exposes no relayer account")
        sender = accounts[0]
    sender = _address(sender, "relayer")
    tx_hash = client.rpc("eth_sendTransaction", [{
        "from": sender,
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
                raise ClaimError("claim transaction reverted")
            return receipt
        time.sleep(0.25)
    raise ClaimError("claim transaction confirmation timed out")


def relay_via_http(url, package, timeout=20):
    p = normalize_package(package)
    payload = json.dumps({"kind": "wqpu-relay-claim", "voucher": p}).encode("utf-8")
    request = urllib.request.Request(
        str(url),
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "WQPU-claim/0.6"},
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


def relay(client, package):
    """Relay through configured service; local unlocked RPC relay is dev-only opt-in."""
    simulate_claim(client, package)
    url = os.environ.get("WQPU_RELAYER_URL", "").strip()
    if url:
        return relay_via_http(url, package)
    if os.environ.get("WQPU_ALLOW_UNLOCKED_RPC_RELAYER", "0") == "1":
        return relay_via_rpc(client, package)
    raise ClaimError(
        "claim is valid but no relayer is configured; set WQPU_RELAYER_URL "
        "or use WQPU_ALLOW_UNLOCKED_RPC_RELAYER=1 only on a local devnet"
    )
