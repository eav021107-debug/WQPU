#!/usr/bin/env python3
"""WQPU dual-meter usage receipts + fail-closed prototype voucher policy."""

from __future__ import print_function

import json
import os
import secrets
import time

import wqpu
from wqpu_meter import GGML_OP_COUNT
from wqpu_payments import PaymentSession


USAGE_DIR = wqpu.HOME / "usage"

# Dual metering protects payment for useful compute, not incidental RPC housekeeping.
# Real llama.cpp can issue an extra 4-byte device/alignment/memory probe while RPC sockets
# are closing or being reserved. Such a 13-byte frame changes `requests/request_bytes` but
# cannot change graph execution or the WQPU charge. Require exact agreement on every
# integrity and billable-work field instead. Tensor upload volume remains matched because
# it is part of the exact remote work stream even though v2 currently prices scalar ops.
BILLING_MATCH_FIELDS = (
    "meter_version",
    "llama_rpc_op_count",
    "graph_compute_calls",
    "graph_recompute_calls",
    "graph_payload_bytes",
    "tensor_upload_bytes",
    "node_executions",
    "estimated_scalar_ops",
    "matmul_scalar_ops",
    "attention_scalar_ops",
    "generic_scalar_ops",
    "metadata_node_executions",
    "invalid_frames",
    "trailing_bytes",
)
# Compatibility name used by early tests/integration code.
MATCH_FIELDS = BILLING_MATCH_FIELDS


def _flag(env_name, network, config_name, default=False):
    raw = os.environ.get(env_name)
    if raw is not None:
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return bool((network or {}).get(config_name, default))


def meter_is_eligible(stats):
    """Fail closed: malformed/partial/unknown RPC work must never become a voucher."""
    try:
        return (
            int(stats.get("meter_version") or 0) == 2
            and int(stats.get("llama_rpc_op_count") or 0) == GGML_OP_COUNT
            and int(stats.get("invalid_frames") or 0) == 0
            and int(stats.get("trailing_bytes") or 0) == 0
            and int(stats.get("estimated_scalar_ops") or 0) > 0
            and (int(stats.get("graph_compute_calls") or 0) + int(stats.get("graph_recompute_calls") or 0)) > 0
        )
    except Exception:
        return False


# Compatibility alias for early v2-meter integration code. Keep the canonical predicate
# above; both names preserve the same fail-closed semantics.
meter_eligible = meter_is_eligible


def meters_match(requester_stats, provider_stats):
    """Return true only when independently observed *billable work* is identical.

    `requests` and `request_bytes` remain diagnostics in receipts, but are deliberately
    excluded because non-billable llama.cpp device probes can occur at different socket
    lifecycle boundaries. Any malformed/trailing stream or any difference in graph work,
    tensor upload, operation categories or estimated units still fails closed.
    """
    if not meter_is_eligible(requester_stats) or not meter_is_eligible(provider_stats):
        return False
    try:
        return all(
            int(requester_stats.get(field) or 0) == int(provider_stats.get(field) or 0)
            for field in BILLING_MATCH_FIELDS
        )
    except Exception:
        return False


def _provider_report(mesh, request_id, node_id):
    reports = getattr(mesh, "provider_usage_reports", {}) or {}
    return dict(((reports.get(request_id) or {}).get(node_id)) or {})


def save_usage_receipt(mesh, snapshot):
    network = getattr(getattr(mesh, "chain", None), "network", {}) or {}
    auto_vouchers = _flag("WQPU_AUTO_VOUCHERS", network, "payments_enabled", False)
    request_id = str(getattr(mesh, "current_request_id", "") or "")
    receipt = {
        "version": 3,
        "kind": "wqpu-rpc-usage-receipt",
        "request_id": request_id or None,
        "created_at": int(time.time()),
        "model": wqpu.model_name(),
        "meter": "llama.cpp-rpc-dual-estimated-scalar-ops-v2",
        "prototype_accounting": True,
        "automatic_real_value_payments_default": bool(network.get("payments_enabled", False)),
        "auto_vouchers_requested": auto_vouchers,
        "workers": [],
    }
    payments = None
    if auto_vouchers:
        try:
            payments = PaymentSession(mesh.chain)
            payments.validate()
        except Exception as exc:
            receipt["payment_error"] = str(exc)

    for node_id, stats in snapshot.items():
        units = int(stats.get("estimated_scalar_ops") or 0)
        if units <= 0 and int(stats.get("node_executions") or 0) <= 0:
            continue
        info = mesh.peer_info.get(node_id) or {}
        wallet = str(info.get("wallet") or "").lower()
        requester_eligible = meter_is_eligible(stats)
        provider_report = _provider_report(mesh, request_id, node_id) if request_id else {}
        provider_stats = provider_report.get("rpc") if isinstance(provider_report.get("rpc"), dict) else {}
        dual_match = meters_match(stats, provider_stats) if provider_stats else False

        worker = {
            "node_id": node_id,
            "wallet": wallet or None,
            "hostname": info.get("hostname"),
            "prototype_compute_units": units,
            "requester_meter_eligible": requester_eligible,
            "provider_attestation_received": bool(provider_report),
            "dual_meter_match": dual_match,
            "rpc": stats,
        }
        if provider_report:
            worker["provider_rpc"] = provider_stats
            worker["provider_attestation"] = {
                "provider_node_id": provider_report.get("provider_node_id"),
                "provider_wallet": provider_report.get("provider_wallet"),
                "signature": provider_report.get("signature"),
            }

        if not requester_eligible:
            worker["voucher_error"] = "requester meter stream incomplete, malformed, or unsupported"
        elif not provider_report:
            worker["voucher_error"] = "signed worker meter report not received"
        elif not dual_match:
            worker["voucher_error"] = "requester and worker billable-compute meters disagree"
        elif payments and wallet:
            try:
                worker["voucher"] = payments.issue(wallet, units)
            except Exception as exc:
                worker["voucher_error"] = str(exc)
        receipt["workers"].append(worker)

    if not receipt["workers"]:
        return receipt, None

    USAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = USAGE_DIR / "request-{}-{}.json".format(int(time.time()), secrets.token_hex(4))
    path.write_text(json.dumps(receipt, indent=2) + "\n")
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return receipt, path
