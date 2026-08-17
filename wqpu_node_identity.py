#!/usr/bin/env python3
"""Cryptographic public-node authentication for WQPU transport handshakes.

A public WQPU node signs each control/dial/accept hello with the private key belonging to
its local TLS certificate. The transport relay verifies that certificate's SHA-256
fingerprint is the one currently registered on-chain for the claimed wallet. This turns
`network_uid` from a self-asserted string into a registry-bound node identity proof.
"""
from __future__ import print_function

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from wqpu_chain import ChainError, RegistryClient, normalize_address

PROOF_KIND = "wqpu-node-identity-v1"
DEFAULT_MAX_AGE = 300
DEFAULT_FUTURE_SKEW = 60
DEFAULT_REGISTRY_CACHE_TTL = 15
NONCE_RE = re.compile(r"^[0-9a-f]{32}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
ROLES = frozenset(("control", "dial", "accept"))


class IdentityError(RuntimeError):
    pass


def _canonical_payload(proof):
    payload = {
        "kind": PROOF_KIND,
        "network_uid": str(proof.get("network_uid") or "").strip().lower(),
        "node_id": str(proof.get("node_id") or ""),
        "wallet": normalize_address(proof.get("wallet")),
        "role": str(proof.get("role") or ""),
        "issued_at": int(proof.get("issued_at")),
        "nonce": str(proof.get("nonce") or "").strip().lower(),
    }
    if not payload["network_uid"].startswith("wqpu-"):
        raise IdentityError("invalid WQPU network UID in identity proof")
    if not payload["node_id"] or len(payload["node_id"]) > 128:
        raise IdentityError("invalid node id in identity proof")
    if payload["role"] not in ROLES:
        raise IdentityError("invalid role in identity proof")
    if not NONCE_RE.match(payload["nonce"]):
        raise IdentityError("invalid nonce in identity proof")
    return payload


def canonical_bytes(proof):
    payload = _canonical_payload(proof)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def certificate_der(cert_path):
    if not shutil.which("openssl"):
        raise IdentityError("OpenSSL is required for WQPU public node identity")
    try:
        return subprocess.check_output([
            "openssl", "x509", "-in", str(cert_path), "-outform", "DER"
        ], stderr=subprocess.STDOUT)
    except Exception as exc:
        raise IdentityError("could not read WQPU TLS certificate: {}".format(exc))


def certificate_fingerprint_from_der(der):
    return hashlib.sha256(der).hexdigest()


def _sign_bytes(data, key_path):
    if not shutil.which("openssl"):
        raise IdentityError("OpenSSL is required for WQPU public node identity")
    proc = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(key_path)],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise IdentityError("could not sign WQPU node identity: {}".format(proc.stderr.decode("utf-8", "replace").strip()))
    return proc.stdout


def build_identity_proof(network_uid, node_id, wallet, role, cert_path=None, key_path=None,
                         issued_at=None, nonce=None):
    if cert_path is None or key_path is None:
        import wqpu
        wqpu.ensure_cert()
        cert_path = cert_path or wqpu.CERT
        key_path = key_path or wqpu.KEY
    proof = {
        "kind": PROOF_KIND,
        "network_uid": str(network_uid or "").strip().lower(),
        "node_id": str(node_id or ""),
        "wallet": normalize_address(wallet),
        "role": str(role or ""),
        "issued_at": int(time.time() if issued_at is None else issued_at),
        "nonce": str(nonce or secrets.token_hex(16)).lower(),
    }
    payload = canonical_bytes(proof)
    der = certificate_der(cert_path)
    signature = _sign_bytes(payload, key_path)
    proof["certificate_der"] = base64.b64encode(der).decode("ascii")
    proof["signature"] = base64.b64encode(signature).decode("ascii")
    proof["certificate_fingerprint"] = certificate_fingerprint_from_der(der)
    return proof


def _verify_signature(payload, der, signature):
    if not shutil.which("openssl"):
        raise IdentityError("OpenSSL is required for WQPU public node identity")
    with tempfile.TemporaryDirectory(prefix="wqpu-node-id-") as tmp:
        cert = Path(tmp) / "cert.der"
        pub = Path(tmp) / "pub.pem"
        sig = Path(tmp) / "sig.bin"
        data = Path(tmp) / "payload.bin"
        cert.write_bytes(der)
        sig.write_bytes(signature)
        data.write_bytes(payload)
        pub_proc = subprocess.run([
            "openssl", "x509", "-inform", "DER", "-in", str(cert), "-pubkey", "-noout",
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if pub_proc.returncode != 0:
            raise IdentityError("invalid certificate in WQPU node proof")
        pub.write_bytes(pub_proc.stdout)
        verify = subprocess.run([
            "openssl", "dgst", "-sha256", "-verify", str(pub),
            "-signature", str(sig), str(data),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if verify.returncode != 0:
            raise IdentityError("invalid WQPU node identity signature")


def verify_identity_proof(proof, expected_network_uid, expected_node_id, expected_wallet,
                          expected_fingerprint, expected_role, now=None,
                          max_age=DEFAULT_MAX_AGE, future_skew=DEFAULT_FUTURE_SKEW):
    if not isinstance(proof, dict):
        raise IdentityError("missing WQPU node identity proof")
    payload = _canonical_payload(proof)
    expected_wallet = normalize_address(expected_wallet)
    expected_uid = str(expected_network_uid or "").strip().lower()
    expected_fp = str(expected_fingerprint or "").lower().replace("0x", "")
    if payload["network_uid"] != expected_uid:
        raise IdentityError("WQPU node proof belongs to a different network")
    if payload["node_id"] != str(expected_node_id or ""):
        raise IdentityError("WQPU node proof has a different node id")
    if payload["wallet"] != expected_wallet:
        raise IdentityError("WQPU node proof has a different wallet")
    if payload["role"] != str(expected_role or ""):
        raise IdentityError("WQPU node proof has a different role")
    if not FINGERPRINT_RE.match(expected_fp):
        raise IdentityError("invalid expected WQPU TLS fingerprint")
    now = int(time.time() if now is None else now)
    issued = int(payload["issued_at"])
    if issued < now - int(max_age):
        raise IdentityError("stale WQPU node identity proof")
    if issued > now + int(future_skew):
        raise IdentityError("future-dated WQPU node identity proof")
    try:
        der = base64.b64decode(str(proof.get("certificate_der") or ""), validate=True)
        signature = base64.b64decode(str(proof.get("signature") or ""), validate=True)
    except Exception:
        raise IdentityError("invalid base64 in WQPU node identity proof")
    if not der or not signature:
        raise IdentityError("incomplete WQPU node identity proof")
    actual_fp = certificate_fingerprint_from_der(der)
    claimed_fp = str(proof.get("certificate_fingerprint") or "").lower().replace("0x", "")
    if claimed_fp and claimed_fp != actual_fp:
        raise IdentityError("WQPU node proof certificate fingerprint is inconsistent")
    if actual_fp != expected_fp:
        raise IdentityError("WQPU node certificate is not registered for this wallet")
    _verify_signature(canonical_bytes(payload), der, signature)
    return {
        "wallet": expected_wallet,
        "node_id": payload["node_id"],
        "network_uid": expected_uid,
        "role": payload["role"],
        "nonce": payload["nonce"],
        "fingerprint": actual_fp,
        "issued_at": issued,
    }


def hello_wallet(hello):
    if not isinstance(hello, dict):
        return ""
    direct = str(hello.get("wallet") or "").strip().lower()
    if direct:
        return direct
    info = hello.get("info")
    if isinstance(info, dict):
        return str(info.get("wallet") or "").strip().lower()
    return ""


class RegistryIdentityVerifier(object):
    def __init__(self, network_uid, client=None, cache_ttl=DEFAULT_REGISTRY_CACHE_TTL):
        self.network_uid = str(network_uid or "").strip().lower()
        if not self.network_uid:
            raise IdentityError("network UID is required")
        self.client = client or RegistryClient()
        if not self.client.configured:
            raise IdentityError("WQPU chain is not configured for transport identity verification")
        self.cache_ttl = max(0, int(cache_ttl))
        self._wallet_cache = {}
        self._nonces = {}

    def _node(self, wallet, now):
        wallet = normalize_address(wallet)
        row = self._wallet_cache.get(wallet)
        if row and now - row[0] <= self.cache_ttl:
            return row[1]
        try:
            node = self.client.find_wallet(wallet, 512)
        except ChainError as exc:
            raise IdentityError("could not read WQPU registry identity: {}".format(exc))
        if not node or not node.get("active"):
            raise IdentityError("wallet is not an active WQPU registry node")
        self._wallet_cache[wallet] = (now, node)
        return node

    def _remember_nonce(self, wallet, nonce, now):
        cutoff = now - (DEFAULT_MAX_AGE + DEFAULT_FUTURE_SKEW)
        for key, seen_at in list(self._nonces.items()):
            if seen_at < cutoff:
                self._nonces.pop(key, None)
        key = "{}|{}".format(wallet, nonce)
        if key in self._nonces:
            raise IdentityError("replayed WQPU node identity proof")
        self._nonces[key] = now

    def verify_hello(self, hello, now=None):
        if not isinstance(hello, dict):
            raise IdentityError("invalid WQPU transport hello")
        role = str(hello.get("role") or "")
        if role not in ROLES:
            raise IdentityError("unsupported WQPU transport role")
        node_id = str(hello.get("node_id") or "")
        wallet = hello_wallet(hello)
        if not node_id or not wallet:
            raise IdentityError("WQPU transport hello is missing node identity")
        now = int(time.time() if now is None else now)
        node = self._node(wallet, now)
        result = verify_identity_proof(
            hello.get("identity_proof"),
            self.network_uid,
            node_id,
            wallet,
            node.get("fingerprint"),
            role,
            now=now,
        )
        self._remember_nonce(result["wallet"], result["nonce"], now)
        return result
