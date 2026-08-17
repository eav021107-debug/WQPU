#!/usr/bin/env python3
"""Derive a CometBFT node ID from config/node_key.json using stdlib only."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def node_id_from_file(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    key = data.get("priv_key")
    if not isinstance(key, dict):
        raise ValueError("node_key.json is missing priv_key")
    key_type = key.get("type")
    encoded = key.get("value")
    if not isinstance(key_type, str) or "PrivKeyEd25519" not in key_type:
        raise ValueError("unsupported CometBFT node key type")
    if not isinstance(encoded, str):
        raise ValueError("invalid CometBFT node key value")
    try:
        private_key = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("invalid base64 CometBFT node key") from exc
    # CometBFT's Ed25519 private key encoding is seed(32) || public_key(32).
    if len(private_key) != 64:
        raise ValueError("unexpected CometBFT Ed25519 private key length")
    public_key = private_key[32:]
    return hashlib.sha256(public_key).digest()[:20].hex()


def seed_address(node_id: str, external_address: str) -> str:
    parsed = urlsplit(external_address)
    if parsed.scheme != "tcp" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("external address must be tcp://HOST:PORT")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid external address port") from exc
    if port is None or port < 1 or port > 65535:
        raise ValueError("invalid external address port")
    host = parsed.hostname
    rendered = f"[{host}]" if ":" in host else host
    return f"{node_id}@{rendered}:{port}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("node_key", type=Path)
    parser.add_argument("--external-address", default="")
    args = parser.parse_args()
    node_id = node_id_from_file(args.node_key)
    print(seed_address(node_id, args.external_address) if args.external_address else node_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
