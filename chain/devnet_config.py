#!/usr/bin/env python3
"""Deterministic config patching for the local sovereign WQPU devnet.

This file intentionally uses only the Python standard library so the devnet
bootstrap does not need another package manager before the chain even starts.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

NATIVE_PRECOMPILE = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"


def _set_if_present(root: dict[str, Any], path: list[str], value: Any) -> None:
    current: Any = root
    for part in path[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict) and path[-1] in current:
        current[path[-1]] = value


def patch_genesis(data: dict[str, Any], base: str, display: str, exponent: int) -> dict[str, Any]:
    if not base or not display:
        raise ValueError("denominations must be non-empty")
    if exponent <= 0 or exponent > 30:
        raise ValueError("display exponent out of range")

    app = data.setdefault("app_state", {})

    _set_if_present(app, ["staking", "params", "bond_denom"], base)
    _set_if_present(app, ["mint", "params", "mint_denom"], base)
    _set_if_present(app, ["evm", "params", "evm_denom"], base)

    gov = app.get("gov")
    if isinstance(gov, dict):
        for section in ("deposit_params", "params"):
            params = gov.get(section)
            if not isinstance(params, dict):
                continue
            for field in ("min_deposit", "expedited_min_deposit"):
                deposits = params.get(field)
                if isinstance(deposits, list):
                    for coin in deposits:
                        if isinstance(coin, dict) and "denom" in coin:
                            coin["denom"] = base

    bank = app.get("bank")
    if isinstance(bank, dict):
        bank["denom_metadata"] = [
            {
                "description": "Native coin of the WQPU compute network.",
                "denom_units": [
                    {"denom": base, "exponent": 0, "aliases": []},
                    {"denom": display, "exponent": exponent, "aliases": []},
                ],
                "base": base,
                "display": display,
                "name": display,
                "symbol": display,
                "uri": "",
                "uri_hash": "",
            }
        ]

    erc20 = app.get("erc20")
    if isinstance(erc20, dict):
        erc20["native_precompiles"] = [NATIVE_PRECOMPILE]
        erc20["token_pairs"] = [
            {
                "contract_owner": 1,
                "erc20_address": NATIVE_PRECOMPILE,
                "denom": base,
                "enabled": True,
            }
        ]

    consensus = data.get("consensus")
    if isinstance(consensus, dict):
        params = consensus.get("params")
        if isinstance(params, dict):
            block = params.get("block")
            if isinstance(block, dict):
                block["max_gas"] = "10000000"

    return data


def patch_app_toml(text: str, evm_chain_id: int) -> str:
    if evm_chain_id <= 0:
        raise ValueError("EVM chain id must be positive")

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
        replacement = None

        if section == "evm" and key == "evm-chain-id":
            replacement = f"evm-chain-id = {evm_chain_id}"
        elif section == "json-rpc":
            if key == "enable":
                replacement = "enable = true"
            elif key == "address":
                replacement = 'address = "127.0.0.1:8545"'
            elif key == "ws-address":
                replacement = 'ws-address = "127.0.0.1:8546"'
            elif key == "api":
                # Keep the dev RPC minimal. No personal/debug namespace by default.
                replacement = 'api = "eth,net,web3"'
            elif key == "allow-insecure-unlock":
                replacement = "allow-insecure-unlock = false"
            elif key == "allow-unprotected-txs":
                replacement = "allow-unprotected-txs = false"

        out.append(replacement if replacement is not None else raw)

    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def patch_genesis_file(path: Path, base: str, display: str, exponent: int) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    patched = patch_genesis(data, base, display, exponent)
    path.write_text(json.dumps(patched, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def patch_app_file(path: Path, evm_chain_id: int) -> None:
    path.write_text(patch_app_toml(path.read_text(encoding="utf-8"), evm_chain_id), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    genesis = sub.add_parser("genesis")
    genesis.add_argument("path", type=Path)
    genesis.add_argument("--base", required=True)
    genesis.add_argument("--display", required=True)
    genesis.add_argument("--exponent", required=True, type=int)

    app = sub.add_parser("app-toml")
    app.add_argument("path", type=Path)
    app.add_argument("--evm-chain-id", required=True, type=int)

    args = parser.parse_args()
    if args.command == "genesis":
        patch_genesis_file(args.path, args.base, args.display, args.exponent)
    else:
        patch_app_file(args.path, args.evm_chain_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
