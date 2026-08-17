#!/usr/bin/env python3
"""Bounded local payment-session key for WQPU.

The user's wallet key never enters WQPU. This module creates a separate secp256k1
key locally with OpenSSL. The wallet authorizes only that session key, for a fixed
amount and expiry. Session vouchers can then be signed without wallet popups.
"""

from __future__ import print_function

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path


SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
HOME = Path(os.environ.get("WQPU_HOME", str(Path.home() / ".wqpu"))).expanduser()
SESSION_KEY = HOME / "session-key.pem"
SESSION_STATE = HOME / "session.json"


class SessionError(RuntimeError):
    pass


def _strip0x(value):
    text = str(value or "")
    return text[2:] if text.startswith("0x") else text


def _bytes32(value, label="bytes32"):
    raw = _strip0x(value)
    if len(raw) != 64:
        raise SessionError("invalid {}".format(label))
    try:
        return bytes.fromhex(raw)
    except ValueError:
        raise SessionError("invalid {}".format(label))


def _address(value):
    text = str(value or "").lower()
    if not text.startswith("0x") or len(text) != 42:
        raise SessionError("invalid address")
    int(text[2:], 16)
    return text


def _word(value):
    return "{:064x}".format(int(value))


def _address_word(value):
    return _strip0x(_address(value)).rjust(64, "0")


def _selector(client, signature):
    encoded = "0x" + signature.encode("utf-8").hex()
    hashed = client.rpc("web3_sha3", [encoded])
    if not isinstance(hashed, str) or len(_strip0x(hashed)) != 64:
        raise SessionError("RPC does not support web3_sha3")
    return _strip0x(hashed)[:8]


def _call(client, contract, data):
    result = client.rpc(
        "eth_call",
        [{"to": _address(contract), "data": "0x" + data}, "latest"],
    )
    if not isinstance(result, str) or not result.startswith("0x"):
        raise SessionError("bad eth_call result")
    return _strip0x(result)


def load_session():
    try:
        return json.loads(SESSION_STATE.read_text())
    except Exception:
        return {}


def save_session(state):
    HOME.mkdir(parents=True, exist_ok=True)
    SESSION_STATE.write_text(json.dumps(state, indent=2) + "\n")
    try:
        SESSION_STATE.chmod(0o600)
    except Exception:
        pass


def ensure_session_key(path=None):
    target = Path(path) if path else SESSION_KEY
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 0:
        return target
    openssl = shutil.which("openssl")
    if not openssl:
        raise SessionError("OpenSSL is required for the local WQPU session key")
    proc = subprocess.run(
        [openssl, "ecparam", "-name", "secp256k1", "-genkey", "-noout", "-out", str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise SessionError("could not create session key: {}".format(proc.stderr.decode("utf-8", "replace")))
    try:
        target.chmod(0o600)
    except Exception:
        pass
    return target


def public_point(path=None):
    key = ensure_session_key(path)
    openssl = shutil.which("openssl")
    proc = subprocess.run(
        [openssl, "ec", "-in", str(key), "-pubout", "-conv_form", "uncompressed", "-outform", "DER"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise SessionError("could not read session public key")
    der = proc.stdout
    if len(der) < 65 or der[-65] != 4:
        raise SessionError("unexpected OpenSSL public-key format")
    return der[-65:]


def session_address(client, path=None):
    point = public_point(path)
    hashed = client.rpc("web3_sha3", ["0x" + point[1:].hex()])
    raw = _strip0x(hashed)
    if len(raw) != 64:
        raise SessionError("could not derive session address")
    return "0x" + raw[-40:]


def new_session_authorization(client, requester, market, max_amount, lifetime_seconds=86400, path=None, now=None):
    requester = _address(requester)
    market = _address(market)
    max_amount = int(max_amount)
    lifetime_seconds = int(lifetime_seconds)
    if max_amount <= 0 or max_amount >= 2 ** 128:
        raise SessionError("session max amount must fit uint128")
    if lifetime_seconds < 60:
        raise SessionError("session lifetime is too short")

    if now is None:
        block = client.rpc("eth_getBlockByNumber", ["latest", False])
        now = int(block["timestamp"], 16)

    return {
        "requester": requester,
        "session_key": session_address(client, path),
        "session_id": "0x" + secrets.token_hex(32),
        "max_amount": max_amount,
        "valid_until": int(now) + lifetime_seconds,
        "market": market,
        "chain_id": client.chain_id(),
    }


def voucher_digest(client, market, channel_id, cumulative_amount, cumulative_units):
    selector = _selector(client, "voucherDigest(bytes32,uint256,uint256)")
    data = (
        selector
        + _bytes32(channel_id, "channel id").hex()
        + _word(cumulative_amount)
        + _word(cumulative_units)
    )
    result = _call(client, market, data)
    if len(result) < 64:
        raise SessionError("short voucher digest")
    return "0x" + result[:64]


def session_authorization_digest(client, market, requester, session_key, session_id, max_amount, valid_until):
    selector = _selector(
        client,
        "sessionAuthorizationDigest(address,address,bytes32,uint128,uint64)",
    )
    data = (
        selector
        + _address_word(requester)
        + _address_word(session_key)
        + _bytes32(session_id, "session id").hex()
        + _word(max_amount)
        + _word(valid_until)
    )
    result = _call(client, market, data)
    if len(result) < 64:
        raise SessionError("short session authorization digest")
    return "0x" + result[:64]


def _read_der_length(data, offset):
    if offset >= len(data):
        raise SessionError("short DER signature")
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    count = first & 0x7F
    if count == 0 or count > 2 or offset + 1 + count > len(data):
        raise SessionError("bad DER length")
    value = int.from_bytes(data[offset + 1:offset + 1 + count], "big")
    return value, offset + 1 + count


def parse_der_signature(data):
    if not data or data[0] != 0x30:
        raise SessionError("bad DER signature")
    total, pos = _read_der_length(data, 1)
    if pos + total != len(data):
        raise SessionError("bad DER sequence length")

    values = []
    for _ in range(2):
        if pos >= len(data) or data[pos] != 0x02:
            raise SessionError("bad DER integer")
        length, pos = _read_der_length(data, pos + 1)
        if length == 0 or pos + length > len(data):
            raise SessionError("bad DER integer length")
        raw = data[pos:pos + length]
        pos += length
        if raw[0] & 0x80:
            raise SessionError("negative DER integer")
        values.append(int.from_bytes(raw, "big"))

    if pos != len(data):
        raise SessionError("trailing DER bytes")
    r, s = values
    if not (0 < r < SECP256K1_N and 0 < s < SECP256K1_N):
        raise SessionError("ECDSA scalar out of range")
    if s > SECP256K1_N // 2:
        s = SECP256K1_N - s
    return r, s


def sign_digest(digest, path=None):
    digest_bytes = _bytes32(digest, "digest")
    key = ensure_session_key(path)
    openssl = shutil.which("openssl")
    proc = subprocess.run(
        [openssl, "pkeyutl", "-sign", "-inkey", str(key)],
        input=digest_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise SessionError("session signing failed: {}".format(proc.stderr.decode("utf-8", "replace")))
    r, s = parse_der_signature(proc.stdout)
    # Session signatures are r||s (64 bytes). The contract tries recovery IDs 27/28
    # and accepts only the one that recovers the authorized session address.
    return "0x{:064x}{:064x}".format(r, s)


def sign_voucher(client, market, channel_id, cumulative_amount, cumulative_units, path=None):
    digest = voucher_digest(client, market, channel_id, cumulative_amount, cumulative_units)
    return sign_digest(digest, path)


if __name__ == "__main__":
    from wqpu_chain import RegistryClient

    client = RegistryClient()
    market = os.environ.get("WQPU_MARKET") or client.network.get("market")
    if not client.configured or not market:
        raise SystemExit("configure WQPU chain + market first")
    print(session_address(client))
