#!/usr/bin/env python3
"""Signed worker-side usage attestations for WQPU dual metering.

The worker signs its independently measured RPC usage with the same local RSA key used
by its TLS certificate. The report carries that certificate; the requester hashes it
and accepts the signature only when its SHA-256 fingerprint matches the worker wallet's
on-chain WQPURegistry entry.
"""

from __future__ import print_function

import base64
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import wqpu


class AttestationError(RuntimeError):
    pass


# Normal WQPU runs one node per process and therefore use wqpu.CERT/KEY. This optional
# registry exists for integration tests and future multi-instance daemons where several
# isolated node identities intentionally share one Python process.
_IDENTITIES = {}


def register_identity(node_id, cert_path, key_path):
    node_id = str(node_id or "")
    if not node_id:
        raise AttestationError("node id is required for attestation identity")
    _IDENTITIES[node_id] = (Path(cert_path), Path(key_path))


def unregister_identity(node_id):
    _IDENTITIES.pop(str(node_id or ""), None)


def _identity_for_report(report):
    node_id = str((report or {}).get("provider_node_id") or "")
    found = _IDENTITIES.get(node_id)
    if found:
        return found
    wqpu.ensure_cert()
    return Path(wqpu.CERT), Path(wqpu.KEY)


def _canonical(report):
    body = dict(report or {})
    body.pop("signature", None)
    body.pop("certificate", None)
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _openssl():
    value = shutil.which("openssl")
    if not value:
        raise AttestationError("OpenSSL is required for WQPU usage attestations")
    return value


def _certificate_der_from_pem(pem):
    openssl = _openssl()
    with tempfile.TemporaryDirectory() as tmp:
        cert = Path(tmp) / "cert.pem"
        cert.write_text(str(pem))
        proc = subprocess.run(
            [openssl, "x509", "-in", str(cert), "-outform", "DER"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if proc.returncode != 0:
            raise AttestationError("invalid worker certificate")
        return proc.stdout


def certificate_fingerprint(pem):
    return hashlib.sha256(_certificate_der_from_pem(pem)).hexdigest()


def sign_report(report):
    openssl = _openssl()
    cert_path, key_path = _identity_for_report(report)
    payload = _canonical(report)
    proc = subprocess.run(
        [openssl, "dgst", "-sha256", "-sign", str(key_path)],
        input=payload,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise AttestationError("could not sign worker usage report")
    signed = dict(report)
    signed["certificate"] = cert_path.read_text()
    signed["signature"] = base64.b64encode(proc.stdout).decode("ascii")
    return signed


def verify_report(report, expected_fingerprint):
    if not isinstance(report, dict):
        raise AttestationError("usage report must be an object")
    expected = str(expected_fingerprint or "").lower().replace("0x", "")
    if len(expected) != 64:
        raise AttestationError("invalid expected TLS fingerprint")
    try:
        int(expected, 16)
    except ValueError:
        raise AttestationError("invalid expected TLS fingerprint")

    certificate = str(report.get("certificate") or "")
    actual = certificate_fingerprint(certificate)
    if actual.lower() != expected:
        raise AttestationError("worker usage certificate fingerprint mismatch")

    try:
        signature = base64.b64decode(str(report.get("signature") or ""), validate=True)
    except Exception:
        raise AttestationError("invalid usage report signature encoding")
    if not signature:
        raise AttestationError("empty usage report signature")

    openssl = _openssl()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cert = root / "cert.pem"
        public = root / "public.pem"
        sig = root / "signature.bin"
        payload = root / "report.bin"
        cert.write_text(certificate)
        sig.write_bytes(signature)
        payload.write_bytes(_canonical(report))

        pub = subprocess.run(
            [openssl, "x509", "-in", str(cert), "-pubkey", "-noout", "-out", str(public)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if pub.returncode != 0:
            raise AttestationError("could not extract worker public key")
        verified = subprocess.run(
            [openssl, "dgst", "-sha256", "-verify", str(public), "-signature", str(sig), str(payload)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if verified.returncode != 0:
            raise AttestationError("worker usage report signature mismatch")
    return True
