#!/usr/bin/env python3
"""WQPU runtime extension: NAT relays, dual metering, gasless funding and payments."""

from __future__ import print_function

import asyncio
import hashlib
import json
import os
import secrets

import wqpu
import wqpu_runtime as runtime
from wqpu_attestation import sign_report, verify_report
from wqpu_claim import relay, relay_activation, relay_funding, wait_receipt
from wqpu_meter import MeterError, RPCRequestMeter
from wqpu_session import active_session, escrow_balance, load_session, reserved_escrow, save_session
from wqpu_vouchers import accept as accept_voucher
from wqpu_vouchers import mark_claimed
from wqpu_wallet import clear_funding_permit, load_funding_permit


PAYMENT_SERVICE = "payment"
USAGE_SERVICE = "usage_report"
METER_PRELUDE = b"WQPU-METER2 "
MAX_PAYMENT_HOPS = 4
MAX_USAGE_REPORT_BYTES = 64 * 1024


def configured_relays(chain):
    raw = os.environ.get("WQPU_RELAYS_JSON", "").strip()
    if raw:
        try:
            values = json.loads(raw)
        except Exception as exc:
            raise RuntimeError("invalid WQPU_RELAYS_JSON: {}".format(exc))
    else:
        values = (getattr(chain, "network", {}) or {}).get("relays") or []
    if not isinstance(values, list):
        raise RuntimeError("WQPU relays must be a list")

    out = []
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        host = str(value.get("host") or "").strip()
        port = int(value.get("port") or wqpu.PORT)
        fp = str(value.get("fingerprint") or "").lower().replace("0x", "")
        if not host or port < 1 or port > 65535 or len(fp) != 64:
            continue
        try:
            int(fp, 16)
        except ValueError:
            continue
        key = wqpu.peer_key(host, port)
        if key in seen:
            continue
        seen.add(key)
        out.append({"host": host, "port": port, "fingerprint": fp, "relay": True})
    return out


class AutoPayChainMesh(runtime.ChainMesh):
    def __init__(self, cfg, chain, wallet):
        relays = configured_relays(chain)
        cfg = dict(cfg)
        cfg["peers"] = relays
        super(AutoPayChainMesh, self).__init__(cfg, chain, wallet)
        self.bootstrap_relays = relays
        self.current_request_id = None
        self.provider_usage_reports = {}
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
        needed = max(0, maximum - self._free_escrow(market, requester))
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
            if self._free_escrow(market, requester) < maximum:
                raise RuntimeError("funding confirmed but free escrow is still below session limit")
            print("[WQPU escrow funded through wallet permit]")
            return True
        except Exception as exc:
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

    async def _open_tls_peer(self, peer):
        host, port = peer["host"], int(peer.get("port", wqpu.PORT))
        reader, writer = await asyncio.open_connection(
            host, port, ssl=wqpu.client_ssl(), server_hostname=host
        )
        sslobj = writer.get_extra_info("ssl_object")
        cert = sslobj.getpeercert(binary_form=True) if sslobj else b""
        actual = hashlib.sha256(cert).hexdigest()
        expected = str(peer.get("fingerprint") or "").lower().replace("0x", "")
        if not expected or actual.lower() != expected:
            await wqpu.close_writer(writer)
            raise RuntimeError("TLS fingerprint mismatch for relay {}:{}".format(host, port))
        return reader, writer

    def _hub_for_route(self, route_key):
        candidates = list(self.bootstrap_relays) + list(self.chain_peers.values())
        try:
            candidates += list(wqpu.load_peer_cache().values())
        except Exception:
            pass
        for peer in candidates:
            try:
                if wqpu.peer_key(peer["host"], peer.get("port", wqpu.PORT)) == route_key:
                    return peer
            except Exception:
                continue
        return None

    async def open_accept(self, hub, sid):
        reader, writer = await self._open_tls_peer(hub)
        hello = {
            "role": "accept", "secret": self.secret,
            "node_id": self.me, "stream": sid,
        }
        writer.write((json.dumps(hello, separators=(",", ":")) + "\n").encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), 15)
        if line != b"WQPU-READY\n":
            await wqpu.close_writer(writer)
            raise RuntimeError("relay accept failed")
        return reader, writer

    async def open_rpc(self, target):
        routes = list(self.routes.get(target) or [])
        for route_key in routes:
            hub = self._hub_for_route(route_key)
            if not hub:
                continue
            try:
                reader, writer = await self._open_tls_peer(hub)
                hello = {
                    "role": "dial", "secret": self.secret,
                    "node_id": self.me, "target": target,
                }
                writer.write((json.dumps(hello, separators=(",", ":")) + "\n").encode())
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), 15)
                if line == b"WQPU-READY\n":
                    return reader, writer
                await wqpu.close_writer(writer)
            except Exception:
                pass
        raise RuntimeError("no route to peer")

    def merge_nodes(self, route_key, nodes):
        for node in nodes:
            wallet = str(node.get("wallet") or "").lower()
            nid = str(node.get("node_id") or "")
            fp = str(node.get("fingerprint") or "").lower().replace("0x", "")
            registered = self.chain_nodes.get(wallet)
            expected = str((registered or {}).get("fingerprint") or "").lower().replace("0x", "")
            if nid and expected and fp == expected:
                self.verified_node_ids.add(nid)
        super(AutoPayChainMesh, self).merge_nodes(route_key, nodes)

    async def connector_loop(self):
        tick = 0
        while not self.stop.is_set():
            if tick % 4 == 0:
                await self.refresh_chain()
            candidates = {}
            source = self.bootstrap_relays if self.bootstrap_relays else list(self.chain_peers.values())
            for peer in source:
                candidates[wqpu.peer_key(peer["host"], peer.get("port", wqpu.PORT))] = peer
            for key, peer in list(candidates.items()):
                if key in self.outbound:
                    continue
                try:
                    await asyncio.wait_for(self.connect_control(peer), 5)
                except Exception:
                    pass
            try:
                await self.broadcast_nodes()
            except Exception:
                pass
            for ctrl in list(self.outbound.values()):
                try:
                    await self.send(ctrl, {"type": "ping"})
                except Exception:
                    pass
            tick += 1
            await asyncio.sleep(5)

    async def proxy_handler(self, target, cr, cw):
        """Requester side: tag RPC stream and independently meter exactly its ggml bytes."""
        try:
            rr, rw = await self.open_rpc(target)
            request_id = self.current_request_id or secrets.token_hex(16)
            prelude = {
                "request_id": request_id,
                "requester_node_id": self.me,
            }
            rw.write(METER_PRELUDE + json.dumps(prelude, separators=(",", ":")).encode() + b"\n")
            await rw.drain()
            meter = self.usage_book.meter(target) if self.usage_book else None
            await asyncio.gather(
                self._copy_metered(cr, rw, meter),
                wqpu.copy_stream(rr, cw),
            )
        except Exception:
            await wqpu.close_writer(cw)

    async def _copy_worker_metered(self, reader, writer, meter):
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                try:
                    meter.feed(data)
                except MeterError:
                    # Preserve transport, but mark the worker report ineligible.
                    meter.invalid_frames += 1
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        await wqpu.close_writer(writer)

    async def _worker_rpc(self, msg, via):
        sid = str(msg.get("stream") or "")
        if not sid or not via:
            return
        rr = rw = lr = lw = None
        try:
            rr, rw = await self.open_accept(via, sid)
            line = await asyncio.wait_for(rr.readline(), 5)
            if not line.startswith(METER_PRELUDE) or len(line) > 2048:
                # Compatibility fallback: do not bill an untagged stream.
                lr, lw = await asyncio.open_connection("127.0.0.1", wqpu.RPC_PORT)
                lw.write(line)
                await lw.drain()
                await wqpu.bridge(rr, rw, lr, lw)
                return
            meta = json.loads(line[len(METER_PRELUDE):].decode("utf-8"))
            request_id = str(meta.get("request_id") or "")
            requester = str(meta.get("requester_node_id") or "")
            if len(request_id) != 32 or not requester:
                raise RuntimeError("bad WQPU meter prelude")
            int(request_id, 16)

            lr, lw = await asyncio.open_connection("127.0.0.1", wqpu.RPC_PORT)
            meter = RPCRequestMeter()
            await asyncio.gather(
                self._copy_worker_metered(rr, lw, meter),
                wqpu.copy_stream(lr, rw),
            )
            report = sign_report({
                "version": 1,
                "kind": "wqpu-worker-usage-attestation",
                "request_id": request_id,
                "requester_node_id": requester,
                "provider_node_id": self.me,
                "provider_wallet": self.wallet,
                "rpc": meter.snapshot(),
            })
            await self._route_usage_report(requester, report)
        except Exception:
            if rw:
                await wqpu.close_writer(rw)
            if lw:
                await wqpu.close_writer(lw)

    async def _route_control_message(self, target, message):
        if target == self.me:
            return False
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

    async def _route_usage_report(self, target, report, ttl=MAX_PAYMENT_HOPS, trace=None):
        trace = list(trace or [])
        if not target or ttl < 0 or self.me in trace:
            return False
        trace.append(self.me)
        if target == self.me:
            return self._receive_usage_report(report)
        message = {
            "type": "open",
            "service": USAGE_SERVICE,
            "target": target,
            "report": report,
            "ttl": ttl,
            "trace": trace,
        }
        return await self._route_control_message(target, message)

    def _receive_usage_report(self, report):
        try:
            if not isinstance(report, dict):
                return False
            if len(json.dumps(report, separators=(",", ":"))) > MAX_USAGE_REPORT_BYTES:
                return False
            if report.get("kind") != "wqpu-worker-usage-attestation":
                return False
            if str(report.get("requester_node_id") or "") != self.me:
                return False
            request_id = str(report.get("request_id") or "")
            provider_node = str(report.get("provider_node_id") or "")
            wallet = str(report.get("provider_wallet") or "").lower()
            if len(request_id) != 32 or not provider_node or len(wallet) != 42:
                return False
            int(request_id, 16)
            registered = self.chain_nodes.get(wallet)
            expected_fp = str((registered or {}).get("fingerprint") or "").lower().replace("0x", "")
            if not expected_fp:
                return False
            info = self.peer_info.get(provider_node) or {}
            known_wallet = str(info.get("wallet") or "").lower()
            if known_wallet and known_wallet != wallet:
                return False
            verify_report(report, expected_fp)
            self.provider_usage_reports.setdefault(request_id, {})[provider_node] = dict(report)
            return True
        except Exception:
            return False

    async def wait_provider_reports(self, targets, request_id, timeout=3.0):
        wanted = {str(target) for target in targets}
        if not wanted:
            return
        loop = asyncio.get_event_loop()
        deadline = loop.time() + float(timeout)
        while loop.time() < deadline:
            have = set((self.provider_usage_reports.get(request_id) or {}).keys())
            if wanted.issubset(have):
                return
            await asyncio.sleep(0.05)

    async def _route_payment(self, target, voucher, ttl=MAX_PAYMENT_HOPS, trace=None):
        target = str(target or "")
        ttl = int(ttl)
        trace = list(trace or [])
        if not target or ttl < 0 or self.me in trace:
            return False
        trace.append(self.me)
        if target == self.me:
            return await self._receive_payment(voucher)
        message = {
            "type": "open", "service": PAYMENT_SERVICE, "target": target,
            "voucher": voucher, "ttl": ttl, "trace": trace,
        }
        return await self._route_control_message(target, message)

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
        service = msg.get("service")
        if service == "rpc":
            await self._worker_rpc(msg, via)
            return
        if service == PAYMENT_SERVICE:
            target = str(msg.get("target") or "")
            voucher = msg.get("voucher") or {}
            ttl = int(msg.get("ttl", MAX_PAYMENT_HOPS))
            trace = list(msg.get("trace") or [])
            if target == self.me:
                await self._receive_payment(voucher)
                return
            if ttl > 0:
                await self._route_payment(target, voucher, ttl - 1, trace)
            return
        if service == USAGE_SERVICE:
            target = str(msg.get("target") or "")
            report = msg.get("report") or {}
            ttl = int(msg.get("ttl", MAX_PAYMENT_HOPS))
            trace = list(msg.get("trace") or [])
            if target == self.me:
                self._receive_usage_report(report)
                return
            if ttl > 0:
                await self._route_usage_report(target, report, ttl - 1, trace)
            return
        await super(AutoPayChainMesh, self).handle_open_request(msg, via=via)


async def run_metered_request(mesh, server_bin, text):
    request_id = secrets.token_hex(16)
    mesh.current_request_id = request_id
    mesh.provider_usage_reports.pop(request_id, None)
    mesh.begin_usage()
    try:
        await wqpu.ask(mesh, server_bin, text)
    finally:
        snapshot = mesh.end_usage()
        await mesh.wait_provider_reports(snapshot.keys(), request_id, 3.0)
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
        mesh.current_request_id = None


def install_extension():
    runtime.ChainMesh = AutoPayChainMesh
    runtime.run_metered_request = run_metered_request


def main():
    install_extension()
    return runtime.main()


if __name__ == "__main__":
    raise SystemExit(main())
