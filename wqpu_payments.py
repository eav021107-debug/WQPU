#!/usr/bin/env python3
"""Cumulative WQPU provider vouchers backed by an activated requester escrow session."""

from __future__ import print_function

import json
import os
import time
from pathlib import Path

from wqpu_session import (
    active_session,
    escrow_balance,
    load_session,
    session_address,
    session_spent,
    sign_provider_voucher,
)


PRICE_UNITS = 1_000_000
HOME = Path(os.environ.get("WQPU_HOME", str(Path.home() / ".wqpu"))).expanduser()
PAYMENT_STATE = HOME / "payments.json"


class PaymentError(RuntimeError):
    pass


def _address(value, label="address"):
    text = str(value or "").lower()
    if not text.startswith("0x") or len(text) != 42:
        raise PaymentError("invalid {}".format(label))
    int(text[2:], 16)
    return text


def load_payment_state():
    try:
        state = json.loads(PAYMENT_STATE.read_text())
        return state if isinstance(state, dict) else {}
    except Exception:
        return {}


def save_payment_state(state):
    HOME.mkdir(parents=True, exist_ok=True)
    PAYMENT_STATE.write_text(json.dumps(state, indent=2) + "\n")
    try:
        PAYMENT_STATE.chmod(0o600)
    except Exception:
        pass


class PaymentSession(object):
    def __init__(self, chain, session=None):
        self.chain = chain
        self.session = dict(session or load_session())
        if not self.session:
            raise PaymentError("no WQPU payment session is authorized")
        self.requester = _address(self.session.get("requester"), "requester")
        self.market = _address(self.session.get("market"), "market")
        self.session_key = _address(self.session.get("session_key"), "session key")
        self.session_id = str(self.session.get("session_id") or "").lower()
        if not self.session_id.startswith("0x") or len(self.session_id) != 66:
            raise PaymentError("invalid session id")
        self.max_amount = int(self.session.get("max_amount") or 0)
        self.price = int(self.session.get("price_per_million_units") or 0)
        self.valid_until = int(self.session.get("valid_until") or 0)
        self.authorization_signature = str(self.session.get("authorization_signature") or "")
        if self.max_amount <= 0 or self.price <= 0:
            raise PaymentError("invalid payment session limits")

    def validate(self):
        chain_id = self.chain.chain_id().lower()
        if str(self.session.get("chain_id") or "").lower() != chain_id:
            raise PaymentError("payment session belongs to another chain")
        if session_address(self.chain).lower() != self.session_key:
            raise PaymentError("local payment key changed; authorize a new session")
        block = self.chain.rpc("eth_getBlockByNumber", ["latest", False])
        now = int(block["timestamp"], 16)
        if now > self.valid_until:
            raise PaymentError("payment session expired for new work")

        active = active_session(self.chain, self.market, self.requester, self.session_id)
        if not active.get("active"):
            raise PaymentError("payment session is not activated on-chain")
        if active.get("session_key") != self.session_key:
            raise PaymentError("activated session key mismatch")
        if int(active.get("max_amount") or 0) != self.max_amount:
            raise PaymentError("activated session limit mismatch")
        if int(active.get("price_per_million_units") or 0) != self.price:
            raise PaymentError("activated session price mismatch")
        if int(active.get("valid_until") or 0) != self.valid_until:
            raise PaymentError("activated session expiry mismatch")
        return active

    def quote(self, cumulative_units):
        units = int(cumulative_units)
        if units < 0:
            raise PaymentError("negative compute units")
        return (units * self.price) // PRICE_UNITS

    def _state(self):
        root = load_payment_state()
        if root.get("session_id") != self.session_id:
            root = {"session_id": self.session_id, "providers": {}}
        root.setdefault("providers", {})
        return root

    def provider_totals(self, provider):
        provider = _address(provider, "provider")
        row = self._state()["providers"].get(provider) or {}
        return int(row.get("units") or 0), int(row.get("amount") or 0)

    def local_spent(self):
        return sum(int(row.get("amount") or 0) for row in self._state()["providers"].values())

    def issue(self, provider, delta_units):
        active = self.validate()
        provider = _address(provider, "provider")
        if provider == self.requester:
            raise PaymentError("cannot pay requester as provider")
        delta_units = int(delta_units)
        if delta_units <= 0:
            raise PaymentError("compute units must increase")

        root = self._state()
        row = root["providers"].get(provider) or {"units": 0, "amount": 0}
        cumulative_units = int(row.get("units") or 0) + delta_units
        cumulative_amount = self.quote(cumulative_units)
        previous_amount = int(row.get("amount") or 0)
        delta_amount = cumulative_amount - previous_amount
        if delta_amount <= 0:
            raise PaymentError("compute increment is too small for current price precision")

        local_total = self.local_spent()
        projected_spend = local_total + delta_amount
        if projected_spend > self.max_amount:
            raise PaymentError("payment session spending limit reached")

        claimed = session_spent(self.chain, self.market, self.requester, self.session_id)
        if claimed > local_total:
            raise PaymentError("local payment state is behind on-chain session state")
        outstanding = local_total - claimed
        reserved_remaining = int(active.get("reserved_remaining") or 0)
        if outstanding + delta_amount > reserved_remaining:
            raise PaymentError("activated session reserve cannot cover outstanding vouchers")

        signature = sign_provider_voucher(
            self.chain,
            self.market,
            self.requester,
            provider,
            self.session_id,
            cumulative_amount,
            cumulative_units,
        )

        root["providers"][provider] = {
            "units": cumulative_units,
            "amount": cumulative_amount,
            "updated_at": int(time.time()),
        }
        save_payment_state(root)

        return {
            "version": 2,
            "kind": "wqpu-provider-voucher",
            "market": self.market,
            "requester": self.requester,
            "provider": provider,
            "session_key": self.session_key,
            "session_id": self.session_id,
            "max_amount": self.max_amount,
            "price_per_million_units": self.price,
            "valid_until": self.valid_until,
            "cumulative_amount": cumulative_amount,
            "cumulative_units": cumulative_units,
            "voucher_signature": signature,
            "authorization_signature": self.authorization_signature,
        }


if __name__ == "__main__":
    from wqpu_chain import RegistryClient

    client = RegistryClient()
    session = PaymentSession(client)
    active = session.validate()
    print(json.dumps({
        "requester": session.requester,
        "session_id": session.session_id,
        "local_spent": session.local_spent(),
        "max_amount": session.max_amount,
        "reserved_remaining": active.get("reserved_remaining"),
        "escrow": escrow_balance(client, session.market, session.requester),
        "claimed": session_spent(client, session.market, session.requester, session.session_id),
    }, indent=2))
