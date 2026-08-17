#!/usr/bin/env python3
"""Read-only health/security diagnostics for a running WQPU testnet operator."""
from __future__ import print_function

import hashlib
import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import wqpu_chain  # noqa: E402
import wqpu_public_config  # noqa: E402

STACK = ROOT / ".wqpu-testnet"
STATE = STACK / "state.json"
CONFIG = STACK / "network-config.json"
OPERATOR = STACK / "operator.json"
DEPLOYMENT = STACK / "deployment.json"


def load(path):
    return json.loads(Path(path).read_text())


def pid_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def rpc(url, method, params):
    raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=raw, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as response:
        body = json.load(response)
    if body.get("error"):
        raise RuntimeError("RPC {} failed: {}".format(method, body["error"]))
    return body.get("result")


def http_health(url, insecure=False):
    context = ssl._create_unverified_context() if insecure and str(url).startswith("https://") else None
    with urllib.request.urlopen(url, timeout=5, context=context) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError("HTTP {} returned {}".format(url, response.status))
        return json.load(response)


def relay_fingerprint(host, port):
    context = ssl._create_unverified_context()
    with socket.create_connection((host, int(port)), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        raise RuntimeError("transport relay presented no TLS certificate")
    return hashlib.sha256(der).hexdigest()


def operator_address(private_key):
    proc = subprocess.run(
        ["cast", "wallet", "address", "--private-key", str(private_key)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("could not derive operator address: {}".format(proc.stdout.strip()))
    for token in proc.stdout.split():
        if token.startswith("0x") and len(token) == 42:
            return token.lower()
    raise RuntimeError("cast returned no operator address")


def check():
    state = load(STATE)
    raw_config = load(CONFIG)
    public = wqpu_public_config.normalize_public(
        wqpu_chain, raw_config, raw_config.get("public") or {}
    )
    # Strict validator still rejects explicit conflicting UID/runtime values.
    public = wqpu_chain.validate_network_config(raw_config, public)
    operator = load(OPERATOR)
    deployment = load(DEPLOYMENT)

    rows = {}
    failures = []

    def record(name, ok, detail=None):
        rows[name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            failures.append(name)

    pids = state.get("pids") or {}
    for name in ("anvil", "rpc_gateway", "relayer", "transport_relay"):
        pid = pids.get(name)
        record("process_{}".format(name), bool(pid and pid_alive(pid)), pid)

    internal_rpc = str(state.get("internal_rpc") or "")
    try:
        chain_id = str(rpc(internal_rpc, "eth_chainId", [])).lower()
        expected_chain = wqpu_chain.normalize_chain_id(public.get("chain_id"))
        record("chain_id", chain_id == expected_chain, chain_id)
    except Exception as exc:
        record("chain_id", False, str(exc))

    for label in ("token", "registry", "market"):
        address = str(public.get(label) or "").lower()
        try:
            code = str(rpc(internal_rpc, "eth_getCode", [address, "latest"]) or "")
            record("{}_bytecode".format(label), code not in ("", "0x", "0x0"), address)
        except Exception as exc:
            record("{}_bytecode".format(label), False, str(exc))

    try:
        derived = operator_address(operator.get("private_key"))
        expected = str(operator.get("address") or "").lower()
        same = (
            derived == expected
            and expected == str(state.get("operator") or "").lower()
            and expected == str(deployment.get("operator") or "").lower()
        )
        record("operator_identity", same, derived)
    except Exception as exc:
        record("operator_identity", False, str(exc))

    ports = state.get("ports") or {}
    scheme = str(state.get("public_scheme") or "http")
    insecure = bool(state.get("tls_enabled"))
    try:
        record(
            "rpc_gateway_health", True,
            http_health("{}://127.0.0.1:{}/health".format(scheme, int(ports["rpc"])), insecure),
        )
    except Exception as exc:
        record("rpc_gateway_health", False, str(exc))
    try:
        record(
            "relayer_health", True,
            http_health("{}://127.0.0.1:{}/health".format(scheme, int(ports["relayer"])), insecure),
        )
    except Exception as exc:
        record("relayer_health", False, str(exc))

    try:
        actual_fp = relay_fingerprint("127.0.0.1", int(ports["relay"]))
        expected_fp = str(state.get("relay_fingerprint") or "").lower().replace("0x", "")
        record("transport_tls_fingerprint", actual_fp == expected_fp, actual_fp)
    except Exception as exc:
        record("transport_tls_fingerprint", False, str(exc))

    expected_uid = wqpu_chain.compute_network_uid(
        public.get("chain_id"), public.get("token"), public.get("registry"), public.get("market")
    )
    record("network_uid", str(public.get("network_uid") or "").lower() == expected_uid, expected_uid)

    result = {
        "ok": not failures,
        "network_uid": expected_uid,
        "checks": rows,
        "failures": failures,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not failures else 1


def main():
    try:
        return check()
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
