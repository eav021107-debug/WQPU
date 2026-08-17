#!/usr/bin/env python3
"""Provider-side durable inbox for cumulative WQPU vouchers."""

from __future__ import print_function

import json
import os
import time
from pathlib import Path

from wqpu_claim import ClaimError, normalize_package


HOME = Path(os.environ.get("WQPU_HOME", str(Path.home() / ".wqpu"))).expanduser()
INBOX_FILE = HOME / "voucher-inbox.json"


class VoucherInboxError(RuntimeError):
    pass


def _load():
    try:
        data = json.loads(INBOX_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    HOME.mkdir(parents=True, exist_ok=True)
    INBOX_FILE.write_text(json.dumps(data, indent=2) + "\n")
    try:
        INBOX_FILE.chmod(0o600)
    except Exception:
        pass


def _key(package):
    return "{}:{}".format(package["requester"], package["session_id"])


def accept(local_wallet, package):
    local_wallet = str(local_wallet or "").lower()
    try:
        p = normalize_package(package)
    except ClaimError as exc:
        raise VoucherInboxError(str(exc))
    if p["provider"] != local_wallet:
        raise VoucherInboxError("voucher belongs to another provider")

    root = _load()
    root.setdefault("vouchers", {})
    key = _key(p)
    old = root["vouchers"].get(key) or {}
    old_amount = int(old.get("cumulative_amount") or 0)
    old_units = int(old.get("cumulative_units") or 0)
    if p["cumulative_amount"] < old_amount or p["cumulative_units"] < old_units:
        raise VoucherInboxError("voucher moved backwards")
    if p["cumulative_amount"] == old_amount and p["cumulative_units"] == old_units:
        return False

    p["received_at"] = int(time.time())
    p["claimed_tx"] = old.get("claimed_tx")
    root["vouchers"][key] = p
    _save(root)
    return True


def pending(local_wallet=None):
    wallet = str(local_wallet or "").lower()
    rows = []
    for value in (_load().get("vouchers") or {}).values():
        if wallet and str(value.get("provider") or "").lower() != wallet:
            continue
        if value.get("claimed_tx"):
            continue
        rows.append(dict(value))
    rows.sort(key=lambda x: (int(x.get("received_at") or 0), int(x.get("cumulative_amount") or 0)))
    return rows


def mark_claimed(package, tx_hash):
    p = normalize_package(package)
    tx_hash = str(tx_hash or "")
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        raise VoucherInboxError("invalid claim transaction hash")
    root = _load()
    key = _key(p)
    row = (root.get("vouchers") or {}).get(key)
    if not row:
        raise VoucherInboxError("voucher is not in inbox")
    if int(row.get("cumulative_amount") or 0) != p["cumulative_amount"]:
        raise VoucherInboxError("newer voucher is already stored")
    row["claimed_tx"] = tx_hash
    row["claimed_at"] = int(time.time())
    _save(root)


def summary(local_wallet=None):
    rows = pending(local_wallet)
    return {
        "pending": len(rows),
        "cumulative_amount": sum(int(row.get("cumulative_amount") or 0) for row in rows),
    }
