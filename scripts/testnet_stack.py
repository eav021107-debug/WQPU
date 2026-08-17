#!/usr/bin/env python3
"""One-command operator stack for the WQPU EVM testnet prototype.

Topology:
  public wallet/client -> restricted RPC gateway -> loopback-only Anvil
  public wallet/client -> gas/faucet relayer -> loopback-only Anvil
  WQPU peers -> TLS transport relay

The operator stack is persistent across normal stop/start cycles. It is still a TESTNET
prototype, not the final sovereign WQPU consensus network and not a place for real funds.
"""
from __future__ import print_function

import argparse
import json
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import devnet  # noqa: E402

STACK_DIR = ROOT / ".wqpu-testnet"
STATE_FILE = STACK_DIR / "state.json"
CONFIG_FILE = STACK_DIR / "network-config.json"
OPERATOR_FILE = STACK_DIR / "operator.json"
DEPLOYMENT_FILE = STACK_DIR / "deployment.json"
CHAIN_STATE_FILE = STACK_DIR / "anvil-state.json"
JOIN_SH_FILE = STACK_DIR / "join.sh"
JOIN_PS1_FILE = STACK_DIR / "join.ps1"
LOG_DIR = STACK_DIR / "logs"
RELAY_HOME = STACK_DIR / "relay-home"
SECP256K1_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
DEFAULT_INTERNAL_RPC_PORT = 28545
DEFAULT_PUBLIC_RPC_PORT = 8545
DEFAULT_RELAYER_PORT = 8787
DEFAULT_RELAY_PORT = 7443
DEFAULT_CHAIN_ID = 31337
DEFAULT_PRICE = 10 ** 18
DEFAULT_SUPPLY = 1_000_000_000


def secure_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value)
    try:
        path.chmod(0o600)
    except Exception:
        pass


def load_json(path, default=None):
    try:
        value = json.loads(path.read_text())
        return value
    except Exception:
        return {} if default is None else default


def pid_alive(pid):
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def terminate_pid(pid):
    if not pid_alive(pid):
        return
    pid = int(pid)
    try:
        if os.name == "nt":
            os.kill(pid, signal.SIGTERM)
        else:
            os.killpg(pid, signal.SIGTERM)
    except Exception:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            return
    deadline = time.time() + 7
    while time.time() < deadline and pid_alive(pid):
        time.sleep(0.1)
    if pid_alive(pid):
        try:
            if os.name == "nt":
                os.kill(pid, signal.SIGTERM)
            else:
                os.killpg(pid, signal.SIGKILL)
        except Exception:
            pass


def require_tools():
    devnet.require_tools()
    missing = [name for name in ("openssl",) if not shutil.which(name)]
    if missing:
        raise RuntimeError("Missing required tool(s): {}".format(", ".join(missing)))


def generate_private_key():
    while True:
        value = secrets.randbelow(SECP256K1_N - 1) + 1
        if 0 < value < SECP256K1_N:
            return "0x{:064x}".format(value)


def run(cmd, env=None):
    proc = subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("command failed:\n{}\n{}".format(" ".join(cmd), proc.stdout))
    return proc.stdout


def private_key_address(private_key):
    out = run(["cast", "wallet", "address", "--private-key", private_key])
    matches = re.findall(r"0x[0-9a-fA-F]{40}", out)
    if not matches:
        raise RuntimeError("could not derive operator address")
    return devnet.validate_address(matches[-1]).lower()


def load_or_create_operator():
    saved = load_json(OPERATOR_FILE, {})
    if saved:
        key = str(saved.get("private_key") or "").lower()
        address = str(saved.get("address") or "").lower()
        if not re.match(r"^0x[0-9a-f]{64}$", key):
            raise RuntimeError("persisted testnet operator key is invalid; use `reset --yes` for a new testnet")
        derived = private_key_address(key)
        if address != derived:
            raise RuntimeError("persisted testnet operator address does not match its key; use `reset --yes`")
        return key, derived, True
    if CHAIN_STATE_FILE.exists() or DEPLOYMENT_FILE.exists():
        raise RuntimeError("persisted chain/deployment exists but operator key is missing; use `reset --yes`")
    key = generate_private_key()
    address = private_key_address(key)
    secure_write(OPERATOR_FILE, json.dumps({
        "warning": "TESTNET operator key. Do not use for real funds.",
        "address": address,
        "private_key": key,
    }, indent=2) + "\n")
    return key, address, False


def load_deployment():
    saved = load_json(DEPLOYMENT_FILE, {})
    if not saved:
        if CHAIN_STATE_FILE.exists():
            raise RuntimeError("persisted Anvil state exists but deployment metadata is missing; use `reset --yes`")
        return None
    if not CHAIN_STATE_FILE.exists():
        raise RuntimeError("deployment metadata exists but persisted Anvil state is missing; use `reset --yes`")
    if str(saved.get("chain_id") or "").lower() != "0x{:x}".format(DEFAULT_CHAIN_ID):
        raise RuntimeError("persisted deployment has unexpected chain id; use `reset --yes`")
    for key in ("token", "registry", "market"):
        try:
            saved[key] = devnet.validate_address(saved.get(key)).lower()
        except Exception:
            raise RuntimeError("persisted deployment has invalid {}; use `reset --yes`".format(key))
    return saved


def save_deployment(operator, token, registry, market, price, supply):
    data = {
        "version": 1,
        "chain_id": "0x{:x}".format(DEFAULT_CHAIN_ID),
        "operator": operator,
        "token": token,
        "registry": registry,
        "market": market,
        "price": int(price),
        "supply": int(supply),
        "created_at": int(time.time()),
    }
    secure_write(DEPLOYMENT_FILE, json.dumps(data, indent=2) + "\n")
    return data


def build_anvil_command(port, state_path=CHAIN_STATE_FILE):
    state_path = Path(state_path)
    cmd = [
        "anvil", "--host", "127.0.0.1", "--port", str(int(port)),
        "--chain-id", str(DEFAULT_CHAIN_ID), "--accounts", "1", "--balance", "0",
    ]
    if state_path.exists():
        cmd += ["--load-state", str(state_path)]
    cmd += ["--dump-state", str(state_path), "--state-interval", "1"]
    return cmd


def contract_exists(rpc_url, address):
    try:
        code = str(devnet.rpc(rpc_url, "eth_getCode", [address, "latest"]) or "").lower()
        return code not in ("", "0x", "0x0")
    except Exception:
        return False


def validate_loaded_deployment(rpc_url, deployment):
    missing = [key for key in ("token", "registry", "market") if not contract_exists(rpc_url, deployment[key])]
    if missing:
        raise RuntimeError(
            "persisted chain does not contain expected {} contract(s); use `reset --yes`".format(", ".join(missing))
        )


def resolve_public_tls(args, old):
    cert = str(args.tls_cert or old.get("tls_cert") or "").strip()
    key = str(args.tls_key or old.get("tls_key") or "").strip()
    if bool(cert) != bool(key):
        raise RuntimeError("both --tls-cert and --tls-key are required")
    if not cert:
        return "", ""
    cert_path = Path(cert).expanduser().resolve()
    key_path = Path(key).expanduser().resolve()
    if not cert_path.is_file():
        raise RuntimeError("TLS certificate not found: {}".format(cert_path))
    if not key_path.is_file():
        raise RuntimeError("TLS private key not found: {}".format(key_path))
    return str(cert_path), str(key_path)


def spawn(name, cmd, env=None):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = (LOG_DIR / (name + ".log")).open("a", encoding="utf-8")
    kwargs = {
        "cwd": str(ROOT),
        "env": env or os.environ.copy(),
        "stdout": log,
        "stderr": subprocess.STDOUT,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    return proc.pid


def wait_http(url, timeout=20, insecure=False):
    deadline = time.time() + timeout
    context = ssl._create_unverified_context() if insecure and str(url).startswith("https://") else None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2, context=context) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("service did not become ready: {}".format(url))


def wait_tcp(host, port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            sock = socket.create_connection((host, int(port)), timeout=1)
            sock.close()
            return True
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("TCP service did not become ready on {}:{}".format(host, port))


def guess_public_host():
    override = os.environ.get("WQPU_TESTNET_PUBLIC_HOST", "").strip()
    if override:
        return override
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("1.1.1.1", 80))
        value = sock.getsockname()[0]
        if value:
            return value
    except Exception:
        pass
    finally:
        sock.close()
    return "127.0.0.1"


def url_host(host):
    host = str(host).strip()
    if ":" in host and not host.startswith("["):
        return "[{}]".format(host)
    return host


def current_client_ref():
    explicit = os.environ.get("WQPU_TESTNET_CLIENT_REF", "").strip()
    if explicit:
        return explicit
    try:
        out = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
        if out and out != "HEAD":
            return out
    except Exception:
        pass
    return "main"


def build_network_config(public_host, rpc_port, relayer_port, relay_port, relay_fp,
                         token, registry, market, payments_enabled=False, faucet_enabled=True,
                         scheme="http"):
    if scheme not in ("http", "https"):
        raise ValueError("unsupported public URL scheme")
    h = url_host(public_host)
    relayer_base = "{}://{}:{}".format(scheme, h, int(relayer_port))
    return {
        "version": 3,
        "public": {
            "enabled": True,
            "testnet": True,
            "chain_id": "0x{:x}".format(DEFAULT_CHAIN_ID),
            "chain_name": "WQPU Testnet",
            "native_symbol": "ETH",
            "rpc_url": "{}://{}:{}".format(scheme, h, int(rpc_port)),
            "token": token,
            "registry": registry,
            "market": market,
            "relayer_url": relayer_base + "/relay",
            "faucet_url": relayer_base + "/faucet" if faucet_enabled else None,
            "relays": [{
                "host": public_host,
                "port": int(relay_port),
                "fingerprint": relay_fp,
            }],
            "payments_enabled": bool(payments_enabled),
            "auto_claim": False,
            "payment_enforcement": False,
            "llama_cpp_tag": "b10456",
            "llama_rpc_protocol_major": 5,
            "llama_rpc_op_count": 101,
        },
    }


def write_join_files(public_host, relayer_port, scheme="http"):
    h = url_host(public_host)
    config_url = "{}://{}:{}/network-config.json".format(scheme, h, int(relayer_port))
    ref = current_client_ref()
    raw = "https://raw.githubusercontent.com/eav021107-debug/WQPU/{}".format(ref)
    sh = """#!/usr/bin/env sh
set -eu
export WQPU_RAW_BASE='{raw}'
export WQPU_NO_START=1
curl -fsSL \"$WQPU_RAW_BASE/install.sh\" | sh
ROOT=\"$HOME/.local/share/wqpu\"
curl -fsSL '{config_url}' -o \"$ROOT/network-config.json\"
unset WQPU_NO_START
exec \"$HOME/.local/bin/wqpu\"
""".format(raw=raw, config_url=config_url)
    ps1 = """$ErrorActionPreference = 'Stop'
$env:WQPU_RAW_BASE = '{raw}'
$env:WQPU_NO_START = '1'
irm \"$env:WQPU_RAW_BASE/install.ps1\" | iex
$root = Join-Path $env:LOCALAPPDATA 'WQPU'
Invoke-WebRequest -UseBasicParsing '{config_url}' -OutFile (Join-Path $root 'network-config.json')
Remove-Item Env:WQPU_NO_START -ErrorAction SilentlyContinue
& (Join-Path $root 'bin\\wqpu.cmd')
""".format(raw=raw, config_url=config_url)
    JOIN_SH_FILE.write_text(sh)
    JOIN_PS1_FILE.write_text(ps1)
    try:
        JOIN_SH_FILE.chmod(0o755)
    except Exception:
        pass
    return config_url


def relay_fingerprint():
    env = os.environ.copy()
    env["WQPU_HOME"] = str(RELAY_HOME)
    out = run([
        sys.executable,
        "-c",
        "import wqpu; print(wqpu.cert_fingerprint())",
    ], env=env)
    value = out.strip().splitlines()[-1].lower().replace("0x", "")
    if len(value) != 64:
        raise RuntimeError("invalid generated relay TLS fingerprint")
    int(value, 16)
    return value


def start(args):
    require_tools()
    old = load_json(STATE_FILE, {})
    running = [name for name, pid in (old.get("pids") or {}).items() if pid_alive(pid)]
    if running:
        raise RuntimeError("testnet stack already running: {}".format(", ".join(sorted(running))))

    STACK_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    public_host = args.public_host or old.get("public_host") or guess_public_host()
    tls_cert, tls_key = resolve_public_tls(args, old)
    scheme = "https" if tls_cert else "http"
    internal_rpc = "http://127.0.0.1:{}".format(args.internal_rpc_port)
    operator_key, operator, reused_operator = load_or_create_operator()
    deployment = load_deployment()
    resumed = deployment is not None

    pids = {}
    try:
        print("WQPU testnet: starting loopback Anvil{}...".format(" from persisted state" if resumed else ""))
        pids["anvil"] = spawn("anvil", build_anvil_command(args.internal_rpc_port))
        actual = devnet.wait_rpc(internal_rpc, timeout=20)
        if actual != DEFAULT_CHAIN_ID:
            raise RuntimeError("unexpected chain id {}".format(actual))

        if deployment:
            validate_loaded_deployment(internal_rpc, deployment)
            if str(deployment.get("operator") or "").lower() != operator:
                raise RuntimeError("persisted deployment operator differs from operator key; use `reset --yes`")
            token = deployment["token"]
            registry = deployment["registry"]
            market = deployment["market"]
            print("WQPU testnet: reusing persisted contracts.")
        else:
            devnet.rpc(internal_rpc, "anvil_setBalance", [operator, hex(100000 * 10 ** 18)])
            print("WQPU testnet: compiling + deploying contracts...")
            run(["forge", "build"])
            token = devnet.deploy(
                "contracts/WQPUToken.sol:WQPUToken",
                [args.supply, operator], internal_rpc, operator_key,
            ).lower()
            registry = devnet.deploy(
                "contracts/WQPURegistry.sol:WQPURegistry",
                [args.price], internal_rpc, operator_key,
            ).lower()
            market = devnet.deploy(
                "contracts/WQPUComputeMarket.sol:WQPUComputeMarket",
                [token, registry], internal_rpc, operator_key,
            ).lower()
            deployment = save_deployment(operator, token, registry, market, args.price, args.supply)
            deadline = time.time() + 4
            while time.time() < deadline and not CHAIN_STATE_FILE.exists():
                time.sleep(0.1)

        fp = relay_fingerprint()
        config = build_network_config(
            public_host, args.rpc_port, args.relayer_port, args.relay_port, fp,
            token, registry, market, args.payments, not args.no_faucet, scheme,
        )
        CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
        write_join_files(public_host, args.relayer_port, scheme)

        env_gateway = os.environ.copy()
        env_gateway.update({
            "WQPU_ANVIL_RPC": internal_rpc,
            "WQPU_GATEWAY_QUIET": "1",
        })
        gateway_cmd = [
            sys.executable, str(ROOT / "wqpu_rpc_gateway.py"),
            "--host", args.bind_host, "--port", str(args.rpc_port),
            "--upstream", internal_rpc,
        ]
        if tls_cert:
            gateway_cmd += ["--tls-cert", tls_cert, "--tls-key", tls_key]
        print("WQPU testnet: starting restricted public {} RPC gateway...".format(scheme.upper()))
        pids["rpc_gateway"] = spawn("rpc-gateway", gateway_cmd, env=env_gateway)

        env_relayer = os.environ.copy()
        env_relayer.update({
            "WQPU_RPC_URL": internal_rpc,
            "WQPU_CHAIN_ID": "0x{:x}".format(DEFAULT_CHAIN_ID),
            "WQPU_REGISTRY": registry,
            "WQPU_MARKET": market,
            "WQPU_TOKEN": token,
            "WQPU_RELAYER_PRIVATE_KEY": operator_key,
            "WQPU_NETWORK_CONFIG": str(CONFIG_FILE),
            "WQPU_JOIN_SH": str(JOIN_SH_FILE),
            "WQPU_JOIN_PS1": str(JOIN_PS1_FILE),
            "WQPU_TESTNET_FAUCET": "0" if args.no_faucet else "1",
            "WQPU_RELAYER_QUIET": "1",
        })
        relayer_cmd = [
            sys.executable, str(ROOT / "wqpu_relayer.py"),
            "--host", args.bind_host, "--port", str(args.relayer_port),
        ]
        if tls_cert:
            relayer_cmd += ["--tls-cert", tls_cert, "--tls-key", tls_key]
        print("WQPU testnet: starting {} gas relayer{}...".format(scheme.upper(), " + faucet" if not args.no_faucet else ""))
        pids["relayer"] = spawn("relayer", relayer_cmd, env=env_relayer)

        env_transport = os.environ.copy()
        env_transport.update({
            "WQPU_HOME": str(RELAY_HOME),
            "WQPU_CHAIN_ID": "0x{:x}".format(DEFAULT_CHAIN_ID),
            "WQPU_REGISTRY": registry,
        })
        print("WQPU testnet: starting TLS transport relay...")
        pids["transport_relay"] = spawn("transport-relay", [
            sys.executable, str(ROOT / "wqpu_transport_relay.py"),
            "--host", args.bind_host, "--port", str(args.relay_port),
        ], env=env_transport)

        local_scheme = scheme
        wait_http("{}://127.0.0.1:{}/health".format(local_scheme, args.rpc_port), 20, insecure=bool(tls_cert))
        wait_http("{}://127.0.0.1:{}/health".format(local_scheme, args.relayer_port), 20, insecure=bool(tls_cert))
        wait_tcp("127.0.0.1", args.relay_port, 20)

        state = {
            "version": 3,
            "started_at": int(time.time()),
            "public_host": public_host,
            "bind_host": args.bind_host,
            "public_scheme": scheme,
            "tls_enabled": bool(tls_cert),
            "tls_cert": tls_cert or None,
            "tls_key": tls_key or None,
            "internal_rpc": internal_rpc,
            "chain_id": "0x{:x}".format(DEFAULT_CHAIN_ID),
            "persistent": True,
            "resumed": bool(resumed),
            "operator": operator,
            "operator_reused": bool(reused_operator),
            "token": token,
            "registry": registry,
            "market": market,
            "relay_fingerprint": fp,
            "config": str(CONFIG_FILE),
            "chain_state": str(CHAIN_STATE_FILE),
            "deployment": str(DEPLOYMENT_FILE),
            "pids": pids,
            "ports": {
                "rpc": args.rpc_port,
                "relayer": args.relayer_port,
                "relay": args.relay_port,
                "internal_rpc": args.internal_rpc_port,
            },
        }
        secure_write(STATE_FILE, json.dumps(state, indent=2) + "\n")
        print_ready(state)
        return 0
    except Exception:
        for pid in reversed(list(pids.values())):
            terminate_pid(pid)
        raise


def print_ready(state):
    host = state["public_host"]
    h = url_host(host)
    ports = state["ports"]
    scheme = state.get("public_scheme") or "http"
    config_url = "{}://{}:{}/network-config.json".format(scheme, h, ports["relayer"])
    print("\nWQPU TESTNET READY{}".format(" (RESUMED)" if state.get("resumed") else ""))
    print("Public RPC:    {}://{}:{}".format(scheme, h, ports["rpc"]))
    print("Gas relayer:   {}://{}:{}/relay".format(scheme, h, ports["relayer"]))
    print("Faucet/config: {}://{}:{}".format(scheme, h, ports["relayer"]))
    print("Transport:     {}:{}".format(host, ports["relay"]))
    print("Registry:      {}".format(state["registry"]))
    print("Market:        {}".format(state["market"]))
    print("Config:        {}".format(config_url))
    print("Persistent:    {}".format(CHAIN_STATE_FILE))
    if state.get("tls_enabled"):
        print("Public TLS:    enabled (certificate: {})".format(state.get("tls_cert")))
    else:
        print("Public TLS:    disabled; use --tls-cert/--tls-key for Internet-facing wallet endpoints")
    print("\nLinux/macOS client (one command):")
    print("curl -fsSL {}://{}:{}/join.sh | sh".format(scheme, h, ports["relayer"]))
    print("\nWindows PowerShell client (one command):")
    print("irm {}://{}:{}/join.ps1 | iex".format(scheme, h, ports["relayer"]))
    print("\nNormal stop/start keeps this same testnet. Use `reset --yes` only to create a new one.")
    print("Operator key is testnet-only and stored with restricted permissions in {}.".format(OPERATOR_FILE))


def stop(_args):
    state = load_json(STATE_FILE, {})
    pids = state.get("pids") or {}
    for name in ("transport_relay", "relayer", "rpc_gateway", "anvil"):
        pid = pids.get(name)
        if pid:
            print("Stopping {} ({})...".format(name, pid))
            terminate_pid(pid)
    if state:
        state["pids"] = {}
        state["stopped_at"] = int(time.time())
        state["resumable"] = CHAIN_STATE_FILE.exists() and DEPLOYMENT_FILE.exists() and OPERATOR_FILE.exists()
        secure_write(STATE_FILE, json.dumps(state, indent=2) + "\n")
    print("WQPU testnet stopped. Persistent chain state was kept.")
    return 0


def reset(args):
    if not args.yes:
        raise RuntimeError("reset destroys the current testnet identity/state; rerun with `reset --yes`")
    state = load_json(STATE_FILE, {})
    if any(pid_alive(pid) for pid in (state.get("pids") or {}).values()):
        stop(None)
    for path in (STATE_FILE, CONFIG_FILE, OPERATOR_FILE, DEPLOYMENT_FILE, CHAIN_STATE_FILE, JOIN_SH_FILE, JOIN_PS1_FILE):
        try:
            path.unlink()
        except OSError:
            pass
    for path in (RELAY_HOME, LOG_DIR):
        try:
            shutil.rmtree(str(path))
        except OSError:
            pass
    print("WQPU testnet reset complete. The next start will create a new chain/operator/contracts.")
    return 0


def status(_args):
    state = load_json(STATE_FILE, {})
    rows = {}
    for name, pid in (state.get("pids") or {}).items():
        rows[name] = {"pid": pid, "running": pid_alive(pid)}
    running = bool(rows) and all(v["running"] for v in rows.values())
    resumable = CHAIN_STATE_FILE.exists() and DEPLOYMENT_FILE.exists() and OPERATOR_FILE.exists()
    result = {
        "running": running,
        "resumable": resumable,
        "persistent": True,
        "public_scheme": state.get("public_scheme") or "http",
        "tls_enabled": bool(state.get("tls_enabled")),
        "services": rows,
        "public_host": state.get("public_host"),
        "operator": state.get("operator"),
        "token": state.get("token"),
        "registry": state.get("registry"),
        "market": state.get("market"),
        "config": state.get("config"),
        "chain_state": str(CHAIN_STATE_FILE) if CHAIN_STATE_FILE.exists() else None,
    }
    print(json.dumps(result, indent=2))
    return 0 if running else 1


def print_config(_args):
    if not CONFIG_FILE.exists():
        raise RuntimeError("testnet config not found; run start first")
    print(CONFIG_FILE.read_text(), end="")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="wqpu-testnet", description="WQPU one-command persistent EVM testnet operator stack")
    sub = ap.add_subparsers(dest="command")
    start_p = sub.add_parser("start")
    start_p.add_argument("--public-host", default=None, help="advertised IP/DNS; defaults to previous host or this machine's LAN address")
    start_p.add_argument("--bind-host", default="0.0.0.0")
    start_p.add_argument("--internal-rpc-port", type=int, default=DEFAULT_INTERNAL_RPC_PORT)
    start_p.add_argument("--rpc-port", type=int, default=DEFAULT_PUBLIC_RPC_PORT)
    start_p.add_argument("--relayer-port", type=int, default=DEFAULT_RELAYER_PORT)
    start_p.add_argument("--relay-port", type=int, default=DEFAULT_RELAY_PORT)
    start_p.add_argument("--price", type=int, default=DEFAULT_PRICE)
    start_p.add_argument("--supply", type=int, default=DEFAULT_SUPPLY)
    start_p.add_argument("--payments", action="store_true", help="enable automatic prototype vouchers in generated config")
    start_p.add_argument("--no-faucet", action="store_true")
    start_p.add_argument("--tls-cert", default=None, help="trusted PEM certificate for public RPC/relayer HTTPS")
    start_p.add_argument("--tls-key", default=None, help="PEM private key matching --tls-cert")
    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("config")
    reset_p = sub.add_parser("reset", help="destroy persisted TESTNET state and create a new network on next start")
    reset_p.add_argument("--yes", action="store_true", help="confirm destructive testnet reset")
    args = ap.parse_args()
    command = args.command or "status"
    if command == "start":
        return start(args)
    if command == "stop":
        return stop(args)
    if command == "status":
        return status(args)
    if command == "config":
        return print_config(args)
    if command == "reset":
        return reset(args)
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print("WQPU testnet error: {}".format(exc), file=sys.stderr)
        raise SystemExit(1)
