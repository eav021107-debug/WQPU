#!/usr/bin/env python3
"""Aggregate verified worker meters across llama.cpp's multiple RPC sockets.

A logical llama.cpp request may open several TCP RPC streams to one worker. Requester-side
UsageBook meters each physical socket independently and aggregates the completed snapshots
per provider. The worker signs one report per physical stream; this extension verifies each
report first and then applies the exact same merge semantics on the requester.

This extension is requester-side only. It never replaces or wraps the worker RPC transport:
- every physical stream keeps using the proven byte-transparent AutoPay worker path;
- every resulting report is independently TLS-signed by the worker;
- the existing receiver verifies wallet, Registry fingerprint and signature first;
- only verified reports are aggregated;
- a report ID derived from the verified signature makes relay replay idempotent;
- requester waits for the verified aggregate to exactly equal its own final meter;
- timeout/mismatch still means no voucher.
"""
from __future__ import print_function

import asyncio
import hashlib

import wqpu_accounting
from wqpu_meter import MeterError, merge_meter_snapshots


class MultiStreamError(RuntimeError):
    pass


def merge_rpc_stats(previous, current):
    """Use the same physical-stream merge rules as requester-side UsageBook."""
    try:
        return merge_meter_snapshots(previous, current)
    except MeterError as exc:
        raise MultiStreamError(str(exc))


def _report_id(report):
    """Use signed payload identity for replay safety without touching RPC forwarding.

    Newer reports may carry an explicit signed stream_id. Existing reports are uniquely
    identified by their verified TLS signature. A truly identical legitimate report can
    only be under-counted, never double-paid, so the failure mode remains fail-closed.
    """
    stream_id = str((report or {}).get("stream_id") or "")
    if stream_id:
        return "stream-" + stream_id
    signature = str((report or {}).get("signature") or "")
    if not signature:
        return ""
    return "sig-" + hashlib.sha256(signature.encode("ascii", "ignore")).hexdigest()


def install(cls):
    if getattr(cls, "_wqpu_multistream_installed", False):
        return cls

    original_receive = cls._receive_usage_report
    original_end_usage = cls.end_usage

    def receive_usage_report(self, report):
        """Verify one signed stream with the old receiver, then aggregate exactly once."""
        if not isinstance(report, dict):
            return False
        request_id = str(report.get("request_id") or "")
        provider_node = str(report.get("provider_node_id") or "")
        if not request_id or not provider_node:
            return False

        old_report = dict(((self.provider_usage_reports.get(request_id) or {}).get(provider_node)) or {})
        report_id = _report_id(report)
        if not report_id:
            return False

        seen_root = getattr(self, "_verified_usage_report_ids", None)
        if seen_root is None:
            seen_root = {}
            self._verified_usage_report_ids = seen_root
        seen = seen_root.setdefault((request_id, provider_node), set())
        if report_id in seen:
            return True

        # Critical trust boundary: original receiver verifies report signature against the
        # provider wallet's active Registry TLS fingerprint before we touch the aggregate.
        if not original_receive(self, report):
            return False

        current_saved = dict(((self.provider_usage_reports.get(request_id) or {}).get(provider_node)) or {})
        try:
            aggregate_rpc = merge_rpc_stats(old_report.get("rpc") or {}, current_saved.get("rpc") or {})
        except Exception:
            bucket = self.provider_usage_reports.setdefault(request_id, {})
            if old_report:
                bucket[provider_node] = old_report
            else:
                bucket.pop(provider_node, None)
            return False

        seen.add(report_id)
        aggregate = dict(current_saved)
        aggregate["rpc"] = aggregate_rpc
        aggregate["verified_stream_count"] = len(seen)
        aggregate["report_id"] = report_id
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

    cls._receive_usage_report = receive_usage_report
    cls.end_usage = end_usage
    cls.wait_provider_reports = wait_provider_reports
    cls._wqpu_multistream_installed = True
    return cls
