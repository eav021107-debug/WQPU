#!/usr/bin/env python3
"""E2E check: on-chain wallet -> TLS fingerprint -> signed WQPU transport hello."""
from __future__ import print_function

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from wqpu_chain import RegistryClient  # noqa: E402
from wqpu_node_identity import (  # noqa: E402
    IdentityError,
    RegistryIdentityVerifier,
    build_identity_proof,
    certificate_der,
    certificate_fingerprint_from_der,
)

STACK = ROOT / ".wqpu-testnet"


def run(cmd):
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    if proc.returncode != 0:
        raise RuntimeError("command failed: {}\n{}".format(" ".join(cmd), proc.stdout))
    return proc.stdout


def main():
    if not shutil.which("openssl") or not shutil.which("cast"):
        raise RuntimeError("OpenSSL and cast are required")
    operator = json.loads((STACK / "operator.json").read_text())
    config = json.loads((STACK / "network-config.json").read_text())["public"]
    state = json.loads((STACK / "state.json").read_text())
    wallet = str(operator["address"]).lower()
    private_key = str(operator["private_key"])
    registry = str(config["registry"]).lower()
    uid = str(config["network_uid"]).lower()
    rpc = str(state["internal_rpc"])

    with tempfile.TemporaryDirectory(prefix="wqpu-registry-id-") as tmp:
        tmp = Path(tmp)
        key = tmp / "key.pem"
        cert = tmp / "cert.pem"
        run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-days", "1", "-subj", "/CN=WQPU Registry Identity E2E",
            "-keyout", str(key), "-out", str(cert),
        ])
        fingerprint = certificate_fingerprint_from_der(certificate_der(cert))
        if not re.match(r"^[0-9a-f]{64}$", fingerprint):
            raise RuntimeError("bad generated TLS fingerprint")

        run([
            "cast", "send", registry,
            "announce(string,bytes32,uint64,uint16)",
            "127.0.0.1:7443", "0x" + fingerprint, "4096", "0",
            "--rpc-url", rpc, "--private-key", private_key,
        ])

        client = RegistryClient(rpc_url=rpc, registry=registry)
        client.expected_chain_id = str(config["chain_id"]).lower()
        node = client.find_wallet(wallet, 512)
        if not node or not node.get("active"):
            raise RuntimeError("operator wallet did not appear as an active Registry node")
        if str(node.get("fingerprint") or "").lower().replace("0x", "") != fingerprint:
            raise RuntimeError("Registry returned a different TLS fingerprint")

        proof = build_identity_proof(
            uid, "registry-e2e-node", wallet, "control",
            cert_path=cert, key_path=key, issued_at=int(time.time()), nonce="55" * 16,
        )
        hello = {
            "role": "control",
            "node_id": "registry-e2e-node",
            "wallet": wallet,
            "network_uid": uid,
            "info": {
                "wallet": wallet,
                "network": "wqpu-public-v1",
                "network_uid": uid,
                "fingerprint": fingerprint,
            },
            "identity_proof": proof,
        }
        verifier = RegistryIdentityVerifier(uid, client=client, cache_ttl=0)
        verified = verifier.verify_hello(hello)
        if verified["fingerprint"] != fingerprint or verified["wallet"] != wallet:
            raise RuntimeError("verified WQPU identity did not match Registry")
        try:
            verifier.verify_hello(hello)
            raise RuntimeError("replayed WQPU identity proof was accepted")
        except IdentityError:
            pass

        run([
            "cast", "send", registry, "setOffline()",
            "--rpc-url", rpc, "--private-key", private_key,
        ])

    print("WQPU Registry -> TLS node identity proof E2E OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
