#!/usr/bin/env python3
"""WQPU usage receipt + prototype voucher policy for meter v2."""

from __future__ import print_function

import json
import os
import secrets
import time

import wqpu
from wqpu_meter import GGML_OP_COUNT
from wqpu_payments import PaymentSession


USAGE_DIR = wqpu.HOME / "usage"


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


def save_usage_receipt(mesh, snapshot):
    network = getattr(getattr(mesh, "chain", None), "network", {}) or {}
    auto_vouchers = _flag("WQPU_AUTO_VOUCHERS", network, "payments_enabled", False)
    receipt = {
        "version": 2,
        "kind": "wqpu-rpc-usage-receipt",
        "created_at": int(time.time()),
        "model": wqpu.model_name(),
        "meter": "llama.cpp-rpc-estimated-scalar-ops-v2",
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
        eligible = meter_is_eligible(stats)
        worker = {
            "node_id": node_id,
            "wallet": wallet or None,
            "hostname": info.get("hostname"),
            "prototype_compute_units": units,
            "meter_eligible_for_prototype_voucher": eligible,
            "rpc": stats,
        }
        if not eligible:
            worker["voucher_error"] = "meter stream incomplete, malformed, or unsupported"
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
