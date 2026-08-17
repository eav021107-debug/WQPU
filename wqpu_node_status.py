#!/usr/bin/env python3
"""TLS-signed off-chain WQPU node status for load-aware scheduling.

Registry stays the source of membership/wallet/TLS fingerprint. Dynamic load changes much
faster than an on-chain wallet transaction should, so nodes sign short-lived status
snapshots with the same TLS key whose fingerprint is registered on-chain.
"""
from __future__ import print_function

import base64
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

from wqpu_chain import BPS, normalize_address
from wqpu_node_identity import (
    IdentityError,
    _sign_bytes,
    _verify_signature,
    certificate_der,
    certificate_fingerprint_from_der,
)

STATUS_KIND = "wqpu-node-status-v1"
DEFAULT_MAX_AGE = 20
DEFAULT_FUTURE_SKEW = 10
FP_RE = re.compile(r"^[0-9a-f]{64}$")


class StatusError(RuntimeError):
    pass


def canonical_status(info, issued_at=None):
    if not isinstance(info, dict):
        raise StatusError("node status info must be an object")
    try:
        capacity = int(info.get("capacity") or info.get("ram_mb") or 0)
        load_bps = int(info.get("load_bps") or 0)
    except Exception:
        raise StatusError("invalid node capacity/load")
    if capacity <= 0:
        raise StatusError("node capacity must be positive")
    if load_bps < 0 or load_bps > BPS:
        raise StatusError("node load is outside 0..10000 bps")
    network_uid = str(info.get("network_uid") or "").strip().lower()
    node_id = str(info.get("node_id") or "")
    wallet = normalize_address(info.get("wallet"))
    if not network_uid.startswith("wqpu-"):
        raise StatusError("invalid WQPU network UID")
    if not node_id or len(node_id) > 128:
        raise StatusError("invalid WQPU node id")
    return {
        "kind": STATUS_KIND,
        "network_uid": network_uid,
        "node_id": node_id,
        "wallet": wallet,
        "capacity": capacity,
        "load_bps": load_bps,
        "model": str(info.get("model") or ""),
        "version": str(info.get("version") or ""),
        "issued_at": int(time.time() if issued_at is None else issued_at),
    }


def canonical_bytes(status):
    return json.dumps(status, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_status_attestation(info, cert_path=None, key_path=None, issued_at=None):
    if cert_path is None or key_path is None:
        import wqpu
        wqpu.ensure_cert()
        cert_path = cert_path or wqpu.CERT
        key_path = key_path or wqpu.KEY
    status = canonical_status(info, issued_at=issued_at)
    der = certificate_der(cert_path)
    signature = _sign_bytes(canonical_bytes(status), key_path)
    return {
        "kind": STATUS_KIND,
        "status": status,
        "certificate_der": base64.b64encode(der).decode("ascii"),
        "certificate_fingerprint": certificate_fingerprint_from_der(der),
        "signature": base64.b64encode(signature).decode("ascii"),
    }


def _same_public_status(info, status):
    try:
        expected = canonical_status(info, issued_at=status.get("issued_at"))
    except Exception:
        return False
    return expected == status


def verify_status_attestation(info, expected_fingerprint, expected_network_uid=None,
                              now=None, max_age=DEFAULT_MAX_AGE,
                              future_skew=DEFAULT_FUTURE_SKEW):
    if not isinstance(info, dict):
        raise StatusError("invalid node info")
    att = info.get("status_attestation")
    if not isinstance(att, dict) or att.get("kind") != STATUS_KIND:
        raise StatusError("missing signed WQPU node status")
    status = att.get("status")
    if not isinstance(status, dict) or status.get("kind") != STATUS_KIND:
        raise StatusError("invalid signed WQPU node status payload")
    if not _same_public_status(info, status):
        raise StatusError("signed node status does not match advertised load/capacity")
    expected_uid = str(expected_network_uid or info.get("network_uid") or "").strip().lower()
    if str(status.get("network_uid") or "").strip().lower() != expected_uid:
        raise StatusError("signed node status belongs to another WQPU network")
    now = int(time.time() if now is None else now)
    try:
        issued = int(status.get("issued_at"))
    except Exception:
        raise StatusError("invalid signed node status timestamp")
    if issued < now - int(max_age):
        raise StatusError("stale signed WQPU node status")
    if issued > now + int(future_skew):
        raise StatusError("future-dated signed WQPU node status")
    expected_fp = str(expected_fingerprint or "").lower().replace("0x", "")
    if not FP_RE.match(expected_fp):
        raise StatusError("invalid expected WQPU TLS fingerprint")
    try:
        der = base64.b64decode(str(att.get("certificate_der") or ""), validate=True)
        signature = base64.b64decode(str(att.get("signature") or ""), validate=True)
    except Exception:
        raise StatusError("invalid base64 in signed node status")
    if not der or not signature:
        raise StatusError("incomplete signed node status")
    actual_fp = certificate_fingerprint_from_der(der)
    claimed_fp = str(att.get("certificate_fingerprint") or "").lower().replace("0x", "")
    if claimed_fp and claimed_fp != actual_fp:
        raise StatusError("node status certificate fingerprint is inconsistent")
    if actual_fp != expected_fp:
        raise StatusError("node status certificate is not registered for this wallet")
    try:
        _verify_signature(canonical_bytes(status), der, signature)
    except IdentityError as exc:
        raise StatusError(str(exc))
    return status
