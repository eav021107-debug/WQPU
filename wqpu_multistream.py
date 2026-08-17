#!/usr/bin/env python3
"""Aggregate verified worker meters across llama.cpp's multiple RPC sockets.

A logical llama.cpp request may open several TCP RPC streams to one worker. Requester-side
UsageBook already meters all of those streams cumulatively per provider. Historically the
worker signed one report per stream and the requester kept only the latest report, making
real inference fail closed even when every individual report was valid.

This extension keeps the security boundary intact:
- every stream is independently metered and TLS-signed by the worker;
- the existing AutoPay receiver verifies wallet, Registry fingerprint and signature first;
- only verified reports are aggregated;
- a signed stream_id prevents replay/double-counting;
- requester waits for the verified aggregate to exactly equal its own final meter;
- timeout/mismatch still means no voucher.
"""
from __future__ import print_function

import asyncio
import hashlib
import json

import wqpu
import wqpu_accounting
from wqpu_attestation import sign_report
from wqpu_meter import RPCRequestMeter


INVARIANT_FIELDS = ("meter_version", "llama_rpc_op_count")
ADDITIVE_FIELDS = tuple(
    field for field in wqpu_accounting.MATCH_FIELDS if field not in INVARIANT_FIELDS
)


class MultiStreamError(RuntimeError):
    pass


def _int(stats, field):
    return int((stats or {}).get(field) or 0)


def merge_rpc_stats(previous, current):
    """Combine two complete per-socket snapshots without weakening eligibility checks."""
    left = dict(previous or {})
    right = dict(current or {})
    if not left:
        return right
    if not right:
        return left

    for field in INVARIANT_FIELDS:
        if _int(left, field) != _int(right, field):
            raise MultiStreamError("worker meter invariant mismatch: {}".format(field))

    merged = dict(left)
    for field in INVARIANT_FIELDS:
        merged[field] = _int(left, field)
    for field in ADDITIVE_FIELDS:
        merged[field] = _int(left, field) + _int(right, field)

    merged["protocol_seen"] = bool(left.get("protocol_seen")) or bool(right.get("protocol_seen"))
    merged["active_seconds"] = float(left.get("active_seconds") or 0.0) + float(right.get("active_seconds") or 0.0)
    merged["last_graph_nodes"] = int(right.get("last_graph_nodes") or left.get("last_graph_nodes") or 0)
    merged["tracked_devices"] = max(
        int(left.get("tracked_devices") or 0), int(right.get("tracked_devices") or 0)
    )
    return merged


def _legacy_stream_id(report):
    # Compatibility only. Patched workers always sign an explicit stream_id. Hashing the
    # verified signature gives old one-stream implementations deterministic replay safety.
    signature = str((report or {}).get("signature") or "")
    if not signature:
        return ""
    return "legacy-" + hashlib.sha256(signature.encode("ascii", "ignore")).hexdigest()


def install(cls):
    if getattr(cls, "_wqpu_multistream_installed", False):
        return cls

    original_receive = cls._receive_usage_report
    original_end_usage = cls.end_usage

    async def worker_rpc(self, msg, via):
        """Worker side: meter and sign each physical RPC stream with its relay stream id."""
        sid = str(msg.get("stream") or "")
        if not sid or not via:
            return
        rr = rw = lr = lw = None
        try:
            rr, rw = await self.open_accept(via, sid)
            line = await asyncio.wait_for(rr.readline(), 5)
            if not line.startswith(self.METER_PRELUDE if hasattr(self, "METER_PRELUDE") else b"WQPU-METER2 ") or len(line) > 2048:
                # AutoPay defines the constant at module level, not on the class. Keep the
                # exact compatibility behavior without billing an untagged stream.
                import wqpu_autopay
                lr, lw = await asyncio.open_connection("127.0.0.1", wqpu.RPC_PORT)
                lw.write(line)
                await lw.drain()
                await wqpu.bridge(rr, rw, lr, lw)
                return

            import wqpu_autopay
            meta = json.loads(line[len(wqpu_autopay.METER_PRELUDE):].decode("utf-8"))
            request_id = str(meta.get("request_id") or "")
            requester = str(meta.get("requester_node_id") or "")
            if len(request_id) != 32 or not requester:
                raise RuntimeError("bad WQPU meter prelude")
            int(request_id, 16)

            local_port = int(getattr(self, "local_rpc_port", wqpu.RPC_PORT))
            lr, lw = await asyncio.open_connection("127.0.0.1", local_port)
            meter = RPCRequestMeter()
            await asyncio.gather(
                self._copy_worker_metered(rr, lw, meter),
                wqpu.copy_stream(lr, rw),
            )
            report = sign_report({
                "version": 2,
                "kind": "wqpu-worker-usage-attestation",
                "request_id": request_id,
                "stream_id": sid,
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

    def receive_usage_report(self, report):
        """Verify one signed stream with the old receiver, then aggregate exactly once."""
        if not isinstance(report, dict):
            return False
        request_id = str(report.get("request_id") or "")
        provider_node = str(report.get("provider_node_id") or "")
        if not request_id or not provider_node:
            return False

        old_report = dict(((self.provider_usage_reports.get(request_id) or {}).get(provider_node)) or {})
        stream_id = str(report.get("stream_id") or "") or _legacy_stream_id(report)
        if not stream_id:
            return False

        seen_root = getattr(self, "_verified_usage_stream_ids", None)
        if seen_root is None:
            seen_root = {}
            self._verified_usage_stream_ids = seen_root
        seen = seen_root.setdefault((request_id, provider_node), set())
        if stream_id in seen:
            # Idempotent delivery of an already-verified physical stream.
            return True

        # Critical trust boundary: original receiver verifies report signature against the
        # provider wallet's active Registry TLS fingerprint before we touch the aggregate.
        if not original_receive(self, report):
            return False

        current_saved = dict(((self.provider_usage_reports.get(request_id) or {}).get(provider_node)) or {})
        try:
            aggregate_rpc = merge_rpc_stats(old_report.get("rpc") or {}, current_saved.get("rpc") or {})
        except Exception:
            # Never retain a partial replacement if invariants disagree.
            bucket = self.provider_usage_reports.setdefault(request_id, {})
            if old_report:
                bucket[provider_node] = old_report
            else:
                bucket.pop(provider_node, None)
            return False

        seen.add(stream_id)
        aggregate = dict(current_saved)
        aggregate["rpc"] = aggregate_rpc
        aggregate["verified_stream_count"] = len(seen)
        aggregate["stream_id"] = stream_id
        self.provider_usage_reports.setdefault(request_id, {})[provider_node] = aggregate
        return True

    def end_usage(self):
        snapshot = original_end_usage(self)
        self._last_requester_usage_snapshot = dict(snapshot or {})
        return snapshot

    async def wait_provider_reports(self, targets, request_id, timeout=3.0):
        wanted = {str(target) for target in targets}
        if not wanted:
            return True
        loop = asyncio.get_event_loop()
        deadline = loop.time() + float(timeout)
        while loop.time() < deadline:
            reports = self.provider_usage_reports.get(request_id) or {}
            requester = getattr(self, "_last_requester_usage_snapshot", {}) or {}
            complete = True
            for target in wanted:
                report = reports.get(target) or {}
                provider_stats = report.get("rpc") if isinstance(report.get("rpc"), dict) else {}
                requester_stats = requester.get(target) if isinstance(requester.get(target), dict) else {}
                if not report:
                    complete = False
                    break
                if requester_stats and not wqpu_accounting.meters_match(requester_stats, provider_stats):
                    complete = False
                    break
            if complete:
                return True
            await asyncio.sleep(0.05)
        return False

    cls._worker_rpc = worker_rpc
    cls._receive_usage_report = receive_usage_report
    cls.end_usage = end_usage
    cls.wait_provider_reports = wait_provider_reports
    cls._wqpu_multistream_installed = True
    return cls
