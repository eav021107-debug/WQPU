#!/usr/bin/env python3
"""Hardened WQPU CometBFT peer-exchange configuration.

Seed nodes are only a first-contact mechanism. CometBFT PEX learns more peers
and its normal addrbook.json persists those addresses across restarts.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

MAX_SEEDS = 64
NODE_ID_RE = re.compile(r"^[0-9a-fA-F]{40}$")
DNS_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def _valid_host(host: str) -> bool:
    if not host or len(host) > 253 or any(ch.isspace() for ch in host):
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if host.endswith("."):
        host = host[:-1]
    labels = host.split(".")
    return bool(labels) and all(DNS_LABEL_RE.fullmatch(label) for label in labels)


def normalize_seed(raw: str) -> str:
    value = raw.strip()
    if value.count("@") != 1:
        raise ValueError("seed must be NODE_ID@HOST:PORT")
    node_id, address = value.split("@", 1)
    if not NODE_ID_RE.fullmatch(node_id):
        raise ValueError("CometBFT seed node id must be exactly 40 hex characters")

    parsed = urlsplit("//" + address)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("seed address must not contain credentials")
    if parsed.path or parsed.query or parsed.fragment or not parsed.hostname:
        raise ValueError("seed address must be HOST:PORT")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid seed port") from exc
    if port is None or port < 1 or port > 65535:
        raise ValueError("invalid seed port")
    if not _valid_host(parsed.hostname):
        raise ValueError("invalid seed host")

    host = parsed.hostname
    try:
        is_v6 = isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        is_v6 = False
    rendered_host = f"[{host}]" if is_v6 else host.lower()
    return f"{node_id.lower()}@{rendered_host}:{port}"


def load_seeds(path: Path) -> list[str]:
    if not path.exists():
        raise ValueError(f"seed manifest does not exist: {path}")
    seeds: list[str] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = raw.split("#", 1)[0].strip()
        if not value:
            continue
        try:
            seed = normalize_seed(value)
        except ValueError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        if seed in seen:
            continue
        seen.add(seed)
        seeds.append(seed)
        if len(seeds) > MAX_SEEDS:
            raise ValueError(f"seed manifest exceeds safety limit of {MAX_SEEDS}")
    return seeds


def _toml_string(value: str) -> str:
    if any(ch in value for ch in ('"', "\\", "\n", "\r", "\x00")):
        raise ValueError("unsafe TOML string")
    return f'"{value}"'


def patch_p2p_config(text: str, seeds: list[str], seed_mode: bool, external_address: str = "") -> str:
    if len(seeds) > MAX_SEEDS:
        raise ValueError("too many seeds")
    if external_address:
        # CometBFT accepts a URL-style external address. Restrict it to TCP.
        parsed = urlsplit(external_address)
        if parsed.scheme != "tcp" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("external address must be tcp://HOST:PORT")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("invalid external address port") from exc
        if port is None or port < 1 or port > 65535 or not _valid_host(parsed.hostname):
            raise ValueError("invalid external address")

    replacements = {
        "seeds": _toml_string(",".join(seeds)),
        "pex": "true",
        "seed_mode": "true" if seed_mode else "false",
        "addr_book_strict": "true",
        "external_address": _toml_string(external_address),
    }
    counts = {key: 0 for key in replacements}
    section = ""
    out: list[str] = []

    for raw in text.splitlines():
        stripped = raw.strip()
        match = re.fullmatch(r"\[([^]]+)\]", stripped)
        if match:
            section = match.group(1)
            out.append(raw)
            continue
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if section == "p2p" and key in replacements:
            out.append(f"{key} = {replacements[key]}")
            counts[key] += 1
        else:
            out.append(raw)

    bad = {key: count for key, count in counts.items() if count != 1}
    if bad:
        detail = ", ".join(f"{key}={count}" for key, count in sorted(bad.items()))
        raise ValueError(f"unexpected CometBFT [p2p] config shape: {detail}")
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--seeds-file", type=Path, required=True)
    parser.add_argument("--seed-mode", choices=("0", "1"), default="0")
    parser.add_argument("--external-address", default="")
    args = parser.parse_args()

    seeds = load_seeds(args.seeds_file)
    original = args.config.read_text(encoding="utf-8")
    patched = patch_p2p_config(
        original,
        seeds=seeds,
        seed_mode=args.seed_mode == "1",
        external_address=args.external_address,
    )
    tmp = args.config.with_suffix(args.config.suffix + ".tmp")
    tmp.write_text(patched, encoding="utf-8")
    tmp.replace(args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
