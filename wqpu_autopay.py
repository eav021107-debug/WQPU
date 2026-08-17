#!/usr/bin/env python3
"""WQPU runtime extension: gasless funding, session activation and provider payments."""

from __future__ import print_function

import asyncio
import os

import wqpu
import wqpu_runtime as runtime
from wqpu_claim import relay, relay_activation, relay_funding, wait_receipt
from wqpu_session import active_session, escrow_balance, load_session, reserved_escrow, save_session
from wqpu_vouchers import accept as accept_voucher
from wqpu_vouchers import mark_claimed
from wqpu_wallet import clear_funding_permit, load_funding_permit


PAYMENT_SERVICE = "payment"
MAX_PAYMENT_HOPS = 4


class AutoPayChainMesh(runtime.ChainMesh):
    def __init__(self, cfg, chain, wallet):
        super(AutoPayChainMesh, self).__init__(cfg, chain, wallet)
        self._ensure_payment_session_active()

    def _free_escrow(self, market, requester):
        balance = int(escrow_balance(self.chain, market, requester))
        reserved = int(reserved_escrow(self.chain, market, requester))
        if reserved > balance:
            raise RuntimeError("on-chain reserved escrow exceeds balance")
        return balance - reserved

    def _fund_if_needed(self, session):
        market = str(session.get("market") or "").lower()
        requester = str(session.get("requester") or "").lower()
        maximum = int(session.get("max_amount") or 0)
        free = self._free_escrow(market, requester)
        needed = max(0, maximum - free)
        if needed == 0:
            clear_funding_permit()
            return True

        funding = load_funding_permit()
        if not funding:
            print("[WQPU payment session needs {} more token-wei in escrow]".format(needed))
            return False
        if str(funding.get("requester") or "").lower() != requester:
            print("[WQPU stored funding permit belongs to another wallet]")
            return False
        if str(funding.get("market") or "").lower() != market:
            print("[WQPU stored funding permit belongs to another market]")
            return False
        if int(funding.get("amount") or 0) < needed:
            print("[WQPU stored funding permit is smaller than current escrow shortfall]")
            return False

        try:
            tx_hash = relay_funding(self.chain, funding)
            wait_receipt(self.chain, tx_hash, 120)
            session["funding_tx"] = tx_hash
            save_session(session)
            clear_funding_permit()
            free = self._free_escrow(market, requester)
            if free < maximum:
                raise RuntimeError("funding confirmed but free escrow is still below session limit")
            print("[WQPU escrow funded through wallet permit]")
            return True
        except Exception as exc:
            # Permit remains stored. A reverted permit+deposit transaction consumes neither
            # permit nonce nor funds, so it can be retried after tokens/relayer become available.
            print("[WQPU escrow funding pending: {}]".format(exc))
            return False

    def _ensure_payment_session_active(self):
        session = load_session()
        if not session:
            return
        market = str(session.get("market") or "").lower()
        requester = str(session.get("requester") or "").lower()
        session_id = str(session.get("session_id") or "").lower()
        if not market or requester != self.wallet or not session_id:
            return
        try:
            current = active_session(self.chain, market, requester, session_id)
        except Exception as exc:
            print("[WQPU payment session check unavailable: {}]".format(exc))
            return

        if current.get("active"):
            expected = (
                str(current.get("session_key") or "").lower() == str(session.get("session_key") or "").lower()
                and int(current.get("max_amount") or 0) == int(session.get("max_amount") or 0)
                and int(current.get("price_per_million_units") or 0) == int(session.get("price_per_million_units") or 0)
                and int(current.get("valid_until") or 0) == int(session.get("valid_until") or 0)
            )
            if expected:
                self.payment_session = session
            else:
                print("[WQPU active payment session does not match local authorization]")
            return

        if not str(session.get("authorization_signature") or "").startswith("0x"):
            print("[WQPU payment session has no wallet authorization signature]")
            return
        if not self._fund_if_needed(session):
            return

        try:
            tx_hash = relay_activation(self.chain, session)
            wait_receipt(self.chain, tx_hash, 120)
            current = active_session(self.chain, market, requester, session_id)
            if not current.get("active"):
                raise RuntimeError("activation transaction confirmed but session is inactive")
            session["activation_tx"] = tx_hash
            save_session(session)
            self.payment_session = session
            print("[WQPU payment session activated and funds reserved]")
        except Exception as exc:
            print("[WQPU payment session pending activation: {}]".format(exc))

    async def _route_payment(self, target, voucher, ttl=MAX_PAYMENT_HOPS, trace=None):
        target = str(target or "")
        ttl = int(ttl)
        trace = list(trace or [])
        if not target or ttl < 0:
            return False
        if self.me in trace:
            return False
        trace.append(self.me)

        if target == self.me:
            return await self._receive_payment(voucher)

        message = {
            "type": "open",
            "service": PAYMENT_SERVICE,
            "target": target,
            "voucher": voucher,
            "ttl": ttl,
            "trace": trace,
        }

        direct = self.controls.get(target)
        if direct:
            try:
                await self.send(direct, message)
                return True
            except Exception:
                pass

        for route_key in list(self.routes.get(target) or []):
            ctrl = self.outbound.get(route_key)
            if not ctrl:
                continue
            try:
                await self.send(ctrl, message)
                return True
            except Exception:
                pass
        return False

    async def _receive_payment(self, voucher):
        try:
            changed = await wqpu.to_thread(accept_voucher, self.wallet, voucher)
        except Exception:
            return False
        if not changed:
            return True

        if os.environ.get("WQPU_AUTO_CLAIM", "0") != "1":
            return True
        try:
            tx_hash = await wqpu.to_thread(relay, self.chain, voucher)
            await wqpu.to_thread(wait_receipt, self.chain, tx_hash, 120)
            await wqpu.to_thread(mark_claimed, voucher, tx_hash)
            print("[WQPU payment claimed: {}]".format(tx_hash))
        except Exception as exc:
            print("[WQPU payment stored; auto-claim unavailable: {}]".format(exc))
        return True

    async def send_payment_voucher(self, target, voucher):
        return await self._route_payment(target, voucher, MAX_PAYMENT_HOPS, [])

    async def handle_open_request(self, msg, via=None):
        if msg.get("service") == PAYMENT_SERVICE:
            target = str(msg.get("target") or "")
            voucher = msg.get("voucher") or {}
            ttl = int(msg.get("ttl", MAX_PAYMENT_HOPS))
            trace = list(msg.get("trace") or [])
            if target == self.me:
                await self._receive_payment(voucher)
                return
            if ttl <= 0:
                return
            await self._route_payment(target, voucher, ttl - 1, trace)
            return
        await super(AutoPayChainMesh, self).handle_open_request(msg, via=via)


async def run_metered_request(mesh, server_bin, text):
    mesh.begin_usage()
    try:
        await wqpu.ask(mesh, server_bin, text)
    finally:
        snapshot = mesh.end_usage()
        receipt, path = await wqpu.to_thread(runtime.save_usage_receipt, mesh, snapshot)
        for worker in receipt.get("workers") or []:
            print("[worker {} | prototype units {}]".format(
                str(worker.get("wallet") or worker.get("node_id") or "?")[:12],
                worker.get("prototype_compute_units", 0),
            ))
            voucher = worker.get("voucher")
            if voucher:
                delivered = await mesh.send_payment_voucher(worker.get("node_id"), voucher)
                print("[automatic cumulative voucher {}]".format(
                    "delivered" if delivered else "stored locally; worker route unavailable"
                ))
            elif worker.get("voucher_error"):
                print("[voucher not issued: {}]".format(worker["voucher_error"]))
        if path:
            print("[usage receipt: {}]".format(path))


def install_extension():
    runtime.ChainMesh = AutoPayChainMesh
    runtime.run_metered_request = run_metered_request


def main():
    install_extension()
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
