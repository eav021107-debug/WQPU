#!/usr/bin/env python3
"""Gasless funding, session activation and provider claims for WQPU."""

from __future__ import print_function

import argparse
import json
import os
import time
import urllib.request


FUND_SIGNATURE = "depositWithPermit(address,uint256,uint256,bytes)"
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


def normalize_funding(funding):
    f = dict(funding or {})
    f["market"] = _address(f.get("market"), "market")
    f["requester"] = _address(f.get("requester"), "requester")
    f["amount"] = int(f.get("amount") or 0)
    f["deadline"] = int(f.get("deadline") or 0)
    if f["amount"] <= 0 or f["amount"] >= 2 ** 128:
        raise ClaimError("invalid funding amount")
    if f["deadline"] <= 0:
        raise ClaimError("invalid permit deadline")
    signature = "0x" + _strip0x(f.get("permit_signature")).lower()
    if len(signature) != 132:
        raise ClaimError("permit signature must be 65 bytes")
    try:
        int(signature[2:], 16)
    except ValueError:
        raise ClaimError("invalid permit signature")
    f["permit_signature"] = signature
    return f


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
    if len(voucher_sig) != 130:
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
    if len(auth_sig) != 132:
        raise ClaimError("wallet authorization signature must be 65 bytes")
    try:
        int(auth_sig[2:], 16)
    except ValueError:
        raise ClaimError("invalid wallet authorization signature")
    s["authorization_signature"] = auth_sig
    return s


def funding_calldata(client, funding):
    f = normalize_funding(funding)
    tail = _dynamic_bytes(f["permit_signature"], "permit signature")
    head = "".join([
        _address_word(f["requester"], "requester"),
        _word(f["amount"]),
        _word(f["deadline"]),
        _word(4 * 32),
    ])
    return "0x" + _selector(client, FUND_SIGNATURE) + head + tail


def activation_calldata(client, session):
    s = normalize_activation(session)
    tail = _dynamic_bytes(s["authorization_signature"], "authorization signature")
    head = "".join([
        _address_word(s["requester"], "requester"),
        _address_word(s["session_key"], "session key"),
        _bytes32_word(s["session_id"], "session id"),
        _word(s["max_amount"]),
        _word(s["price_per_million_units"]),
        _word(s["valid_until"]),
        _word(7 * 32),
    ])
    return "0x" + _selector(client, ACTIVATE_SIGNATURE) + head + tail


def claim_calldata(client, package):
    p = normalize_package(package)
    tail = _dynamic_bytes(p["voucher_signature"], "voucher signature")
    head = "".join([
        _address_word(p["requester"], "requester"),
        _bytes32_word(p["session_id"], "session id"),
        _address_word(p["provider"], "provider"),
        _word(p["cumulative_amount"]),
        _word(p["cumulative_units"]),
        _word(6 * 32),
    ])
    return "0x" + _selector(client, CLAIM_SIGNATURE) + head + tail


def _simulate(client, market, data):
    result = client.rpc("eth_call", [{"to": market, "data": data}, "latest"])
    if not isinstance(result, str) or not result.startswith("0x"):
        raise ClaimError("bad EVM simulation result")
    return True


def simulate_funding(client, funding):
    f = normalize_funding(funding)
    return _simulate(client, f["market"], funding_calldata(client, f))


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


def fund_via_rpc(client, funding, sender=None):
    f = normalize_funding(funding)
    simulate_funding(client, f)
    tx_hash = client.rpc("eth_sendTransaction", [{
        "from": _unlocked_sender(client, sender),
        "to": f["market"],
        "data": funding_calldata(client, f),
    }])
    if not isinstance(tx_hash, str) or not tx_hash.startswith("0x"):
        raise ClaimError("bad funding transaction hash")
    return tx_hash


def activate_via_rpc(client, session, sender=None):
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
        str(url), data=payload,
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


def relay_funding(client, funding):
    f = normalize_funding(funding)
    simulate_funding(client, f)
    url = _relayer_url(client)
    if url:
        return _relay_http(url, "wqpu-relay-funding", "funding", f)
    if os.environ.get("WQPU_ALLOW_UNLOCKED_RPC_RELAYER", "0") == "1":
        return fund_via_rpc(client, f)
    raise ClaimError("funding permit is valid but no relayer is configured")


def relay_activation(client, session):
    s = normalize_activation(session)
    simulate_activation(client, s)
    url = _relayer_url(client)
    if url:
        return _relay_http(url, "wqpu-relay-activation", "session", s)
    if os.environ.get("WQPU_ALLOW_UNLOCKED_RPC_RELAYER", "0") == "1":
        return activate_via_rpc(client, s)
    raise ClaimError("session authorization is valid but no relayer is configured")


def relay(client, package):
    p = normalize_package(package)
    simulate_claim(client, p)
    url = _relayer_url(client)
    if url:
        return _relay_http(url, "wqpu-relay-claim", "voucher", p)
    if os.environ.get("WQPU_ALLOW_UNLOCKED_RPC_RELAYER", "0") == "1":
        return relay_via_rpc(client, p)
    raise ClaimError("claim is valid but no relayer is configured")


def main():
    from wqpu_chain import RegistryClient
    from wqpu_vouchers import mark_claimed, pending

    parser = argparse.ArgumentParser(prog="wqpu claim")
    parser.add_argument("--submit", action="store_true", help="relay all pending provider vouchers")
    args = parser.parse_args()
    rows = pending()
    if not rows:
        print("No pending WQPU provider vouchers.")
        return 0

    if not args.submit:
        print("Pending provider vouchers: {}".format(len(rows)))
        for row in rows:
            print("- requester {} | amount {} | units {}".format(
                str(row.get("requester") or "?")[:12],
                row.get("cumulative_amount", 0),
                row.get("cumulative_units", 0),
            ))
        print("Run `wqpu claim --submit` to relay them.")
        return 0

    client = RegistryClient()
    failures = 0
    for row in rows:
        try:
            tx_hash = relay(client, row)
            wait_receipt(client, tx_hash, 120)
            mark_claimed(row, tx_hash)
            print("claimed {} -> {}".format(row.get("cumulative_amount"), tx_hash))
        except Exception as exc:
            failures += 1
            print("claim failed: {}".format(exc))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
