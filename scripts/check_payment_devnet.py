#!/usr/bin/env python3
"""End-to-end devnet check: local OpenSSL session key -> provider payout."""

from __future__ import print_function

import json
import subprocess
import tempfile
from pathlib import Path

from wqpu_chain import RegistryClient
from wqpu_session import (
    active_session,
    function_selector,
    provider_voucher_digest,
    session_address,
    sign_digest,
    spend_authorization_digest,
)
from scripts.devnet import DEFAULT_PRIVATE_KEY


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".wqpu-devnet.json"
PROVIDER = "0x000000000000000000000000000000000000bEEF"
SESSION_ID = "0x" + "77" * 32


def run(args):
    proc = subprocess.run(args, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if proc.returncode != 0:
        raise RuntimeError("command failed: {}\n{}".format(" ".join(args), proc.stdout))
    return proc.stdout.strip()


def sign_requester_digest(digest, private_key):
    out = run(["cast", "wallet", "sign", "--no-hash", digest, "--private-key", private_key])
    signature = next((part for part in reversed(out.split()) if part.startswith("0x") and len(part) == 132), "")
    if not signature:
        raise RuntimeError("could not parse cast wallet signature: {}".format(out))
    return signature


def erc20_balance(client, token, wallet):
    selector = function_selector(client, "balanceOf(address)")
    data = selector + wallet.lower().replace("0x", "").rjust(64, "0")
    raw = client.rpc("eth_call", [{"to": token, "data": "0x" + data}, "latest"])
    return int(raw, 16)


def main():
    state = json.loads(STATE.read_text())
    rpc_url = state["rpc_url"]
    token = state["token"]
    registry = state["registry"]
    market = state["market"]
    requester = state["deployer"].lower()
    price = int(state["price_per_million_units"])
    max_amount = 10 * price

    client = RegistryClient(rpc_url=rpc_url, registry=registry)
    block = client.rpc("eth_getBlockByNumber", ["latest", False])
    valid_until = int(block["timestamp"], 16) + 3600

    run(["cast", "send", token, "approve(address,uint256)", market, str(max_amount), "--rpc-url", rpc_url, "--private-key", DEFAULT_PRIVATE_KEY])
    run(["cast", "send", market, "deposit(uint256)", str(max_amount), "--rpc-url", rpc_url, "--private-key", DEFAULT_PRIVATE_KEY])

    with tempfile.TemporaryDirectory() as tmp:
        key_path = Path(tmp) / "session.pem"
        session_key = session_address(client, path=key_path)
        auth_digest = spend_authorization_digest(
            client, market, requester, session_key, SESSION_ID,
            max_amount, price, valid_until,
        )
        auth_signature = sign_requester_digest(auth_digest, DEFAULT_PRIVATE_KEY)

        auth_tuple = "({},{},{},{},{},{})".format(
            requester, session_key, SESSION_ID, max_amount, price, valid_until
        )
        run([
            "cast", "send", market,
            "activateSession((address,address,bytes32,uint128,uint128,uint64),bytes)",
            auth_tuple, auth_signature,
            "--rpc-url", rpc_url, "--private-key", DEFAULT_PRIVATE_KEY,
        ])

        active = active_session(client, market, requester, SESSION_ID)
        if not active["active"] or active["session_key"] != session_key.lower():
            raise RuntimeError("session did not activate")
        if active["reserved_remaining"] != max_amount:
            raise RuntimeError("wrong activated reserve")

        units = 2_000_000
        amount = (units * price) // 1_000_000
        digest = provider_voucher_digest(
            client, market, requester, PROVIDER, SESSION_ID, amount, units
        )
        voucher_signature = sign_digest(digest, path=key_path)
        voucher_tuple = "({},{},{})".format(PROVIDER, amount, units)

        before = erc20_balance(client, token, PROVIDER)
        run([
            "cast", "send", market,
            "claimEscrowWithSession(address,bytes32,(address,uint256,uint256),bytes)",
            requester, SESSION_ID, voucher_tuple, voucher_signature,
            "--rpc-url", rpc_url, "--private-key", DEFAULT_PRIVATE_KEY,
        ])
        after = erc20_balance(client, token, PROVIDER)
        if after - before != amount:
            raise RuntimeError("provider payout mismatch: {}".format(after - before))

    print("WQPU payment devnet round-trip OK: provider received {} token-wei".format(amount))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
