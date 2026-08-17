#!/usr/bin/env python3
"""Start an Anvil WQPU devnet and deploy the prototype contracts.

This script uses Anvil's public, well-known development mnemonic/private key.
Never use this key, mnemonic, or generated state with real funds.
"""

from __future__ import print_function

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".wqpu-devnet.json"
ENV_FILE = ROOT / ".wqpu-devnet.env"
LOG = ROOT / ".wqpu-devnet-anvil.log"
PID_FILE = ROOT / ".wqpu-devnet-anvil.pid"

DEFAULT_RPC = "http://127.0.0.1:8545"
DEFAULT_CHAIN_ID = 31337
DEFAULT_MNEMONIC = "test test test test test test test test test test test junk"
DEFAULT_PRIVATE_KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
DEFAULT_SUPPLY = 1_000_000_000
DEFAULT_PRICE = 10 ** 18  # 1 WQPU per 1,000,000 compute units.


def require_tools():
    missing = [name for name in ("anvil", "forge", "cast") if not shutil.which(name)]
    if missing:
        raise RuntimeError(
            "Missing Foundry tools: {}. Install Foundry first: https://getfoundry.sh/".format(
                ", ".join(missing)
            )
        )


def rpc(url, method, params=None, timeout=2):
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params or [],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = json.load(response)
    if body.get("error"):
        raise RuntimeError("RPC error: {}".format(body["error"]))
    return body.get("result")


def chain_id(url):
    return int(rpc(url, "eth_chainId"), 16)


def wait_rpc(url, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            return chain_id(url)
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("Anvil did not become ready at {}".format(url))


def start_anvil(rpc_url):
    if rpc_url != DEFAULT_RPC:
        raise RuntimeError("Automatic Anvil start supports only {}".format(DEFAULT_RPC))

    log = LOG.open("a", encoding="utf-8")
    cmd = [
        "anvil",
        "--host", "0.0.0.0",
        "--port", "8545",
        "--chain-id", str(DEFAULT_CHAIN_ID),
        "--mnemonic", DEFAULT_MNEMONIC,
    ]
    kwargs = {"stdout": log, "stderr": subprocess.STDOUT, "cwd": str(ROOT)}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    PID_FILE.write_text(str(proc.pid) + "\n")
    actual = wait_rpc(rpc_url)
    if actual != DEFAULT_CHAIN_ID:
        raise RuntimeError("unexpected devnet chain id {}".format(actual))
    return proc.pid


def run(cmd):
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("command failed:\n{}\n{}".format(" ".join(cmd), proc.stdout))
    return proc.stdout


def validate_address(value):
    value = str(value or "").strip()
    if not re.match(r"^0x[0-9a-fA-F]{40}$", value):
        raise ValueError("invalid EVM address: {}".format(value))
    return value


def deploy(contract, constructor_args, rpc_url, private_key):
    cmd = [
        "forge", "create", contract,
        "--rpc-url", rpc_url,
        "--private-key", private_key,
        "--broadcast",
    ]
    if constructor_args:
        cmd += ["--constructor-args"] + [str(x) for x in constructor_args]
    output = run(cmd)
    matches = re.findall(r"Deployed to:\s*(0x[0-9a-fA-F]{40})", output)
    if not matches:
        raise RuntimeError("could not parse deployment address:\n{}".format(output))
    return matches[-1]


def fund_wallet(wallet, token, rpc_url, private_key):
    wallet = validate_address(wallet)
    run([
        "cast", "send", wallet,
        "--value", "100ether",
        "--rpc-url", rpc_url,
        "--private-key", private_key,
    ])
    run([
        "cast", "send", token,
        "transfer(address,uint256)", wallet, str(1000 * 10 ** 18),
        "--rpc-url", rpc_url,
        "--private-key", private_key,
    ])


def main():
    ap = argparse.ArgumentParser(description="Start/deploy the local WQPU Anvil devnet")
    ap.add_argument("wallet", nargs="?", help="optional existing wallet to fund with dev ETH + WQPU")
    ap.add_argument("--rpc-url", default=DEFAULT_RPC)
    ap.add_argument("--price", type=int, default=DEFAULT_PRICE, help="token wei per 1,000,000 compute units")
    ap.add_argument("--supply", type=int, default=DEFAULT_SUPPLY, help="whole WQPU tokens")
    args = ap.parse_args()

    require_tools()
    started_pid = None
    try:
        actual_chain = chain_id(args.rpc_url)
    except Exception:
        print("WQPU devnet: starting Anvil...")
        started_pid = start_anvil(args.rpc_url)
        actual_chain = chain_id(args.rpc_url)

    if actual_chain != DEFAULT_CHAIN_ID:
        raise RuntimeError(
            "refusing dev deployment: expected chain {}, got {}".format(DEFAULT_CHAIN_ID, actual_chain)
        )

    private_key = os.environ.get("WQPU_DEV_PRIVATE_KEY", DEFAULT_PRIVATE_KEY)
    deployer = run(["cast", "wallet", "address", "--private-key", private_key]).strip().splitlines()[-1]
    deployer = validate_address(deployer)

    print("WQPU devnet: compiling contracts...")
    run(["forge", "build"])

    print("WQPU devnet: deploying token...")
    token = deploy(
        "contracts/WQPUToken.sol:WQPUToken",
        [args.supply, deployer],
        args.rpc_url,
        private_key,
    )
    print("WQPU devnet: deploying registry...")
    registry = deploy(
        "contracts/WQPURegistry.sol:WQPURegistry",
        [args.price],
        args.rpc_url,
        private_key,
    )
    print("WQPU devnet: deploying compute market...")
    market = deploy(
        "contracts/WQPUComputeMarket.sol:WQPUComputeMarket",
        [token, registry],
        args.rpc_url,
        private_key,
    )

    if args.wallet:
        print("WQPU devnet: funding {}...".format(args.wallet))
        fund_wallet(args.wallet, token, args.rpc_url, private_key)

    state = {
        "rpc_url": args.rpc_url,
        "chain_id": "0x{:x}".format(actual_chain),
        "deployer": deployer,
        "token": token,
        "registry": registry,
        "market": market,
        "price_per_million_units": args.price,
        "anvil_pid": started_pid,
        "dev_only": True,
    }
    STATE.write_text(json.dumps(state, indent=2) + "\n")
    ENV_FILE.write_text(
        "export WQPU_RPC_URL='{}'\n"
        "export WQPU_REGISTRY='{}'\n"
        "export WQPU_TOKEN='{}'\n"
        "export WQPU_MARKET='{}'\n".format(args.rpc_url, registry, token, market)
    )

    print("\nWQPU devnet ready.")
    print("RPC:      {}".format(args.rpc_url))
    print("Token:    {}".format(token))
    print("Registry: {}".format(registry))
    print("Market:   {}".format(market))
    print("\nmacOS/Linux: source .wqpu-devnet.env && wqpu")
    print("Windows: set WQPU_RPC_URL and WQPU_REGISTRY to the values above, then run wqpu")
    print("This is a local test chain. The Anvil mnemonic/private key is public and unsafe for real funds.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print("WQPU devnet error: {}".format(exc), file=sys.stderr)
        raise SystemExit(1)
