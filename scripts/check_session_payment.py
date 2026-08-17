#!/usr/bin/env python3
"""End-to-end shared-escrow payment smoke test on the local Anvil devnet."""

from __future__ import print_function

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wqpu_chain import RegistryClient  # noqa: E402
from wqpu_claim import ClaimError, relay_via_rpc, simulate_claim, wait_receipt  # noqa: E402
from wqpu_session import (  # noqa: E402
    ensure_session_key,
    provider_voucher_digest,
    session_address,
    sign_digest,
    spend_authorization_digest,
)
from devnet import STATE  # noqa: E402


def strip0x(value):
    text = str(value or "")
    return text[2:] if text.startswith("0x") else text


def word(value):
    return "{:064x}".format(int(value))


def address_word(value):
    return strip0x(value).lower().rjust(64, "0")


def selector(client, signature):
    hashed = client.rpc("web3_sha3", ["0x" + signature.encode("utf-8").hex()])
    return strip0x(hashed)[:8]


def send(client, sender, to, data):
    tx = client.rpc("eth_sendTransaction", [{"from": sender, "to": to, "data": "0x" + data}])
    return wait_receipt(client, tx)


def balance_of(client, token, wallet):
    data = selector(client, "balanceOf(address)") + address_word(wallet)
    result = client.rpc("eth_call", [{"to": token, "data": "0x" + data}, "latest"])
    return int(result, 16)


def main():
    state = json.loads(STATE.read_text())
    client = RegistryClient(rpc_url=state["rpc_url"], registry=state["registry"])
    token = state["token"].lower()
    market = state["market"].lower()
    price = int(state["price_per_million_units"])
    accounts = [str(x).lower() for x in client.rpc("eth_accounts", [])]
    if len(accounts) < 2:
        raise RuntimeError("Anvil returned fewer than two unlocked accounts")
    deployer = accounts[0]
    provider = accounts[1]

    with tempfile.TemporaryDirectory() as tmp:
        requester_pem = Path(tmp) / "requester.pem"
        session_pem = Path(tmp) / "session.pem"
        ensure_session_key(requester_pem)
        ensure_session_key(session_pem)
        requester = session_address(client, requester_pem).lower()
        session_key = session_address(client, session_pem).lower()

        # Anvil-only test setup: impersonate the independently generated requester so the
        # test can deposit its tokens without ever exporting that key into an RPC wallet.
        client.rpc("anvil_setBalance", [requester, hex(100 * 10 ** 18)])
        client.rpc("anvil_impersonateAccount", [requester])

        deposit = 10 * 10 ** 18
        transfer_data = selector(client, "transfer(address,uint256)") + address_word(requester) + word(deposit)
        send(client, deployer, token, transfer_data)

        approve_data = selector(client, "approve(address,uint256)") + address_word(market) + word(2 ** 256 - 1)
        send(client, requester, token, approve_data)
        deposit_data = selector(client, "deposit(uint256)") + word(deposit)
        send(client, requester, market, deposit_data)

        block = client.rpc("eth_getBlockByNumber", ["latest", False])
        now = int(block["timestamp"], 16)
        session_id = "0x" + "51" * 32
        max_amount = 5 * 10 ** 18
        valid_until = now + 3600

        auth_digest = spend_authorization_digest(
            client,
            market,
            requester,
            session_key,
            session_id,
            max_amount,
            price,
            valid_until,
        )
        compact_auth = strip0x(sign_digest(auth_digest, requester_pem))

        units = 1_000_000
        amount = price
        voucher_digest = provider_voucher_digest(
            client,
            market,
            requester,
            provider,
            session_id,
            amount,
            units,
        )
        voucher_signature = sign_digest(voucher_digest, session_pem)

        base = {
            "version": 1,
            "kind": "wqpu-provider-voucher",
            "market": market,
            "requester": requester,
            "provider": provider,
            "session_key": session_key,
            "session_id": session_id,
            "max_amount": max_amount,
            "price_per_million_units": price,
            "valid_until": valid_until,
            "cumulative_amount": amount,
            "cumulative_units": units,
            "voucher_signature": voucher_signature,
        }

        package = None
        for recovery in (27, 28):
            candidate = dict(base)
            candidate["authorization_signature"] = "0x" + compact_auth + "{:02x}".format(recovery)
            try:
                simulate_claim(client, candidate)
                package = candidate
                break
            except Exception:
                pass
        if package is None:
            raise RuntimeError("neither wallet recovery id produced a valid spend authorization")

        before = balance_of(client, token, provider)
        tx_hash = relay_via_rpc(client, package, deployer)
        wait_receipt(client, tx_hash)
        after = balance_of(client, token, provider)
        if after - before != amount:
            raise RuntimeError("provider received {}, expected {}".format(after - before, amount))

        # The exact same cumulative voucher must not pay twice.
        try:
            simulate_claim(client, package)
        except Exception:
            replay_blocked = True
        else:
            replay_blocked = False
        if not replay_blocked:
            raise RuntimeError("already-claimed cumulative voucher remained claimable")

        client.rpc("anvil_stopImpersonatingAccount", [requester])
        print("WQPU session payment round-trip OK: {} wei -> {}".format(amount, provider))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
