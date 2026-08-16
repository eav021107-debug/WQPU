#!/usr/bin/env python3
"""WQPU: one llama.cpp model across several trusted computers."""
from __future__ import annotations

import argparse
import ctypes
import ipaddress
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

VERSION = "0.2.0"
HOME = Path(os.environ.get("WQPU_HOME", str(Path.home() / ".wqpu"))).expanduser()
CONFIG_FILE = HOME / "config.json"
NODE_FILE = HOME / "node-id"
STATUS_FILE = HOME / "status.json"
STOP_FILE = HOME / "stop"
RUNTIME_DIR = HOME / "runtime"
LOG_DIR = HOME / "logs"
DISCOVERY_PORT = 51111
RPC_PORT = 50052
API_PORT = 8080
PEER_TTL = 8.0
DISCOVERY_EVERY = 1.5
DEFAULTS = {
    "cluster": "home",
    "model": "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M",
    "cpu_fraction": 0.50,
    "ram_reserve_fraction": 0.30,
    "min_ram_reserve_mb": 512,
    "min_coordinator_ram_mb": 3072,
    "context": 4096,
    "discovery_port": DISCOVERY_PORT,
    "rpc_port": RPC_PORT,
    "api_port": API_PORT,
}


def ensure_home() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    ensure_home()
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception as exc:
            print(f"WQPU: config warning: {exc}", file=sys.stderr)
    if os.environ.get("WQPU_CPU_FRACTION"):
        cfg["cpu_fraction"] = float(os.environ["WQPU_CPU_FRACTION"])
    if os.environ.get("WQPU_RAM_RESERVE_FRACTION"):
        cfg["ram_reserve_fraction"] = float(os.environ["WQPU_RAM_RESERVE_FRACTION"])
    if os.environ.get("WQPU_MODEL"):
        cfg["model"] = os.environ["WQPU_MODEL"]
    if os.environ.get("WQPU_CLUSTER"):
        cfg["cluster"] = os.environ["WQPU_CLUSTER"]
    return cfg


def save_config(cfg: dict) -> None:
    ensure_home()
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def node_id() -> str:
    ensure_home()
    if NODE_FILE.exists():
        value = NODE_FILE.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = uuid.uuid4().hex
    NODE_FILE.write_text(value + "\n", encoding="utf-8")
    return value


def total_ram_mb() -> int:
    system = platform.system()
    try:
        if system == "Windows":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            state = MEMORYSTATUSEX()
            state.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state))
            return int(state.ullTotalPhys // (1024 * 1024))
        if system == "Linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
        if system == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(out) // (1024 * 1024)
    except Exception:
        pass
    return 0


def threads_for(cfg: dict) -> int:
    fraction = max(0.10, min(float(cfg.get("cpu_fraction", 0.5)), 0.90))
    return max(1, int((os.cpu_count() or 2) * fraction))


def reserve_mb(cfg: dict, ram_mb: int | None = None) -> int:
    ram_mb = ram_mb if ram_mb is not None else total_ram_mb()
    if not ram_mb:
        return int(cfg.get("min_ram_reserve_mb", 512))
    minimum = max(256, int(cfg.get("min_ram_reserve_mb", 512)))
    fraction = max(0.10, min(float(cfg.get("ram_reserve_fraction", 0.3)), 0.8))
    wanted = max(minimum, int(ram_mb * fraction))
    cap = max(256, ram_mb - 512)
    return min(wanted, cap)


def api_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": f"WQPU/{VERSION}"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": f"WQPU/{VERSION}"})
    with urllib.request.urlopen(req, timeout=120) as response, dest.open("wb") as out:
        shutil.copyfileobj(response, out, length=1024 * 1024)


def asset_suffix() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    x64 = machine in {"x86_64", "amd64", "x64"}
    arm64 = machine in {"arm64", "aarch64"}
    if system == "Windows" and x64:
        return "-bin-win-cpu-x64.zip"
    if system == "Windows" and arm64:
        return "-bin-win-cpu-arm64.zip"
    if system == "Linux" and x64:
        return "-bin-ubuntu-x64.tar.gz"
    if system == "Linux" and arm64:
        return "-bin-ubuntu-arm64.tar.gz"
    if system == "Darwin" and arm64:
        return "-bin-macos-arm64.tar.gz"
    if system == "Darwin" and x64:
        return "-bin-macos-x64.tar.gz"
    raise RuntimeError(f"unsupported platform: {system} {machine}")


def find_binary(root: Path, stem: str) -> Path:
    candidates = []
    for name in ([stem + ".exe", stem] if os.name == "nt" else [stem, stem + ".exe"]):
        candidates.extend(root.rglob(name))
    for path in candidates:
        if path.is_file():
            if os.name != "nt":
                path.chmod(path.stat().st_mode | 0o111)
            return path
    raise FileNotFoundError(f"{stem} not found in {root}")


def ensure_runtime(force: bool = False) -> tuple[Path, Path, str]:
    ensure_home()
    meta_file = RUNTIME_DIR / "current.json"
    if meta_file.exists() and not force:
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            server = Path(meta["server"])
            rpc = Path(meta["rpc"])
            if server.exists() and rpc.exists():
                return server, rpc, meta.get("tag", "cached")
        except Exception:
            pass
    print("WQPU: downloading an official llama.cpp build...")
    release = api_get_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest")
    tag = release["tag_name"]
    suffix = asset_suffix()
    asset = next((a for a in release.get("assets", []) if a.get("name", "").endswith(suffix)), None)
    if not asset:
        raise RuntimeError(f"llama.cpp release {tag} has no asset matching {suffix}")
    target = RUNTIME_DIR / tag
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    archive = RUNTIME_DIR / asset["name"]
    download(asset["browser_download_url"], archive)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            try:
                tf.extractall(target, filter="data")
            except TypeError:
                tf.extractall(target)
    try:
        archive.unlink()
    except OSError:
        pass
    server = find_binary(target, "llama-server")
    rpc = find_binary(target, "ggml-rpc-server")
    meta = {"tag": tag, "server": str(server), "rpc": str(rpc)}
    meta_file.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return server, rpc, tag


def low_priority_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)}
    return {}


def start_process(cmd: list[str], log_name: str) -> subprocess.Popen:
    ensure_home()
    log = (LOG_DIR / log_name).open("a", encoding="utf-8")
    kwargs = low_priority_kwargs()
    if os.name != "nt":
        kwargs["preexec_fn"] = lambda: os.nice(7)
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, **kwargs)


def terminate(proc: subprocess.Popen | None) -> None:
    if not proc or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def is_private_lan(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_loopback or addr.is_private or addr.is_link_local
    except ValueError:
        return False


def tailscale_cli() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if platform.system() == "Darwin":
        app_cli = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
        if app_cli.exists():
            return str(app_cli)
    return None


def tailscale_status() -> tuple[str | None, list[str]]:
    cli = tailscale_cli()
    if not cli:
        return None, []
    env = os.environ.copy()
    env["TAILSCALE_BE_CLI"] = "1"
    try:
        raw = subprocess.check_output([cli, "status", "--json"], text=True, stderr=subprocess.DEVNULL, env=env, timeout=8)
        data = json.loads(raw)
        if data.get("BackendState") != "Running":
            return None, []
        self_ips = data.get("Self", {}).get("TailscaleIPs", [])
        own = next((ip for ip in self_ips if ":" not in ip), None)
        peers: list[str] = []
        peer_map = data.get("Peer", {}) or {}
        values = peer_map.values() if isinstance(peer_map, dict) else peer_map
        for peer in values:
            for ip in peer.get("TailscaleIPs", []) or []:
                if ":" not in ip and ip != own:
                    peers.append(ip)
        return own, sorted(set(peers))
    except Exception:
        return None, []


def network_state() -> tuple[str, str, list[str]]:
    ts_ip, ts_peers = tailscale_status()
    if ts_ip:
        return "tailscale", ts_ip, ts_peers
    ip = local_ip()
    if is_private_lan(ip):
        return "lan", ip, []
    return "blocked", "127.0.0.1", []


class Discovery:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.node = node_id()
        self.hostname = socket.gethostname()
        self.ram = total_ram_mb()
        self.threads = threads_for(cfg)
        self.peers: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.sock: socket.socket | None = None
        self.mode, self.bind_ip, self.mesh_targets = network_state()

    def payload(self) -> bytes:
        data = {
            "magic": "WQPU2", "version": VERSION, "cluster": self.cfg["cluster"],
            "node": self.node, "hostname": self.hostname, "ram_mb": self.ram,
            "threads": self.threads, "model": self.cfg["model"],
            "rpc_port": int(self.cfg["rpc_port"]), "api_port": int(self.cfg["api_port"]),
            "network": self.mode,
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    def refresh_mesh(self) -> None:
        if self.mode != "tailscale":
            return
        own, peers = tailscale_status()
        if own:
            self.bind_ip = own
            self.mesh_targets = peers

    def run(self) -> None:
        if self.mode == "blocked":
            return
        port = int(self.cfg["discovery_port"])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if self.mode == "lan":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("" if self.mode == "lan" else self.bind_ip, port))
        sock.settimeout(0.4)
        self.sock = sock
        last_send = 0.0
        last_mesh_refresh = 0.0
        while not self.stop_event.is_set():
            now = time.time()
            if self.mode == "tailscale" and now - last_mesh_refresh >= 5.0:
                self.refresh_mesh()
                last_mesh_refresh = now
            if now - last_send >= DISCOVERY_EVERY:
                targets = ["255.255.255.255"] if self.mode == "lan" else list(self.mesh_targets)
                for target in targets:
                    try:
                        sock.sendto(self.payload(), (target, port))
                    except OSError:
                        pass
                last_send = now
            try:
                raw, addr = sock.recvfrom(8192)
                data = json.loads(raw.decode("utf-8"))
                if data.get("magic") != "WQPU2" or data.get("cluster") != self.cfg["cluster"]:
                    continue
                if data.get("node") == self.node:
                    continue
                data["ip"] = addr[0]
                data["last_seen"] = time.time()
                with self.lock:
                    self.peers[data["node"]] = data
            except socket.timeout:
                pass
            except (OSError, ValueError, UnicodeDecodeError):
                pass
            with self.lock:
                cutoff = time.time() - PEER_TTL
                self.peers = {k: v for k, v in self.peers.items() if v.get("last_seen", 0) >= cutoff}
        sock.close()

    def snapshot(self) -> list[dict]:
        with self.lock:
            return [dict(v) for v in self.peers.values()]

    def stop(self) -> None:
        self.stop_event.set()


def elect_coordinator(disc: Discovery, peers: list[dict]) -> str:
    candidates = [{"node": disc.node, "ram_mb": disc.ram, "threads": disc.threads}] + peers
    best = max(candidates, key=lambda x: (int(x.get("ram_mb", 0)), int(x.get("threads", 0)), str(x.get("node", ""))))
    return str(best["node"])


def write_status(cfg: dict, role: str, peers: list[dict], coordinator: str, child: subprocess.Popen | None, disc: Discovery) -> None:
    self_info = {
        "node": node_id(), "hostname": socket.gethostname(), "ip": disc.bind_ip,
        "ram_mb": total_ram_mb(), "threads": threads_for(cfg), "model": cfg["model"], "network": disc.mode,
    }
    data = {
        "version": VERSION, "updated": time.time(), "role": role, "coordinator": coordinator,
        "self": self_info, "peers": peers,
        "api_url": f"http://{disc.bind_ip}:{cfg['api_port']}" if role == "coordinator" else None,
        "child_pid": child.pid if child and child.poll() is None else None,
    }
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(STATUS_FILE)


def run_cluster(cfg: dict) -> int:
    if STOP_FILE.exists():
        STOP_FILE.unlink()
    server_bin, rpc_bin, tag = ensure_runtime()
    disc = Discovery(cfg)
    print(f"WQPU {VERSION} | llama.cpp {tag}")
    print(f"Node: {socket.gethostname()} | RAM {disc.ram} MiB | CPU threads for WQPU: {disc.threads}/{os.cpu_count() or '?'}")
    if disc.mode == "blocked":
        print("Network: PUBLIC address detected. WQPU will not expose RPC/API without Tailscale.")
        print("Install/connect Tailscale, then run WQPU again.")
    elif disc.mode == "tailscale":
        print(f"Network: TAILSCALE {disc.bind_ip} | secure remote discovery enabled")
    else:
        print(f"Network: LAN {disc.bind_ip} | local discovery enabled")
    thread = threading.Thread(target=disc.run, daemon=True)
    thread.start()
    child: subprocess.Popen | None = None
    signature = None
    stopping = False
    restart_after = 0.0

    def on_signal(_sig, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)
    time.sleep(2.0)
    try:
        while not stopping and not STOP_FILE.exists():
            peers = disc.snapshot()
            coordinator = elect_coordinator(disc, peers)
            if disc.mode == "blocked":
                role = "waiting"
            elif coordinator == disc.node:
                role = "coordinator"
            else:
                role = "worker"
            if role == "coordinator" and not peers and disc.ram < int(cfg.get("min_coordinator_ram_mb", 3072)):
                role = "waiting"
            peer_endpoints = sorted(f"{p['ip']}:{p.get('rpc_port', cfg['rpc_port'])}" for p in peers)
            model_mismatch = [p for p in peers if p.get("model") != cfg["model"]]
            current = (role, tuple(peer_endpoints), cfg["model"], disc.bind_ip)
            dead = child is not None and child.poll() is not None
            now = time.time()
            needs_child = role in {"worker", "coordinator"}
            should_change = current != signature or (needs_child and child is None)
            if dead and now >= restart_after:
                should_change = True
            if should_change:
                terminate(child)
                child = None
                signature = current
                if role == "worker":
                    cmd = [str(rpc_bin), "--host", disc.bind_ip, "--port", str(cfg["rpc_port"]),
                           "--threads", str(threads_for(cfg)), "--device", "CPU", "--cache"]
                    child = start_process(cmd, "rpc.log")
                    restart_after = now + 5.0
                    print(f"Role: WORKER | {disc.bind_ip}:{cfg['rpc_port']} | coordinator {coordinator[:8]}")
                elif role == "coordinator":
                    cmd = [str(server_bin), "--hf-repo", str(cfg["model"]),
                           "--threads", str(threads_for(cfg)), "--threads-batch", str(threads_for(cfg)),
                           "--ctx-size", str(cfg["context"]), "--host", disc.bind_ip, "--port", str(cfg["api_port"]),
                           "--prio", "-1", "--poll", "0", "--fit", "on", "--fit-target", str(reserve_mb(cfg))]
                    if peer_endpoints:
                        cmd += ["--rpc", ",".join(peer_endpoints)]
                    child = start_process(cmd, "server.log")
                    restart_after = now + 8.0
                    print(f"Role: COORDINATOR | {len(peers) + 1} node(s) | UI/API: http://{disc.bind_ip}:{cfg['api_port']}")
                    if peer_endpoints:
                        print("RPC workers: " + ", ".join(peer_endpoints))
                else:
                    print("Role: WAITING | this node is too small to coordinate alone, waiting for a stronger peer")
                if model_mismatch:
                    print("Warning: some nodes use a different model setting; coordinator setting wins.")
            write_status(cfg, role, peers, coordinator, child, disc)
            time.sleep(1.0)
    finally:
        disc.stop()
        terminate(child)
        if STOP_FILE.exists():
            try:
                STOP_FILE.unlink()
            except OSError:
                pass
        print("WQPU stopped.")
    return 0


def cmd_status(_args) -> int:
    if not STATUS_FILE.exists():
        print("WQPU is not running (no status file yet).")
        return 1
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Cannot read status: {exc}")
        return 1
    age = time.time() - data.get("updated", 0)
    live = age < 5
    print(f"WQPU: {'RUNNING' if live else 'STALE'} | role={data.get('role')} | nodes={1 + len(data.get('peers', []))}")
    me = data.get("self", {})
    print(f"Self: {me.get('hostname')} {me.get('ip')} | {me.get('network')} | RAM {me.get('ram_mb')} MiB | threads {me.get('threads')}")
    for p in data.get("peers", []):
        print(f"Peer: {p.get('hostname')} {p.get('ip')} | RAM {p.get('ram_mb')} MiB | threads {p.get('threads')}")
    if data.get("api_url"):
        print("UI/API: " + data["api_url"])
    return 0 if live else 1


def cmd_doctor(_args) -> int:
    cfg = load_config()
    mode, bind_ip, mesh = network_state()
    print(f"WQPU {VERSION}")
    print(f"OS: {platform.system()} {platform.release()} | arch={platform.machine()}")
    print(f"CPU logical threads: {os.cpu_count()} | WQPU target: {threads_for(cfg)}")
    ram = total_ram_mb()
    print(f"RAM: {ram} MiB | planned reserve: {reserve_mb(cfg, ram)} MiB")
    print(f"Network: {mode} | bind={bind_ip} | mesh peers visible={len(mesh)}")
    print(f"Model: {cfg['model']}")
    print(f"Cluster: {cfg['cluster']}")
    print(f"Home: {HOME}")
    try:
        server, rpc, tag = ensure_runtime()
        print(f"llama.cpp: {tag}")
        print(f"llama-server: {server}")
        print(f"ggml-rpc-server: {rpc}")
    except Exception as exc:
        print(f"Runtime: ERROR: {exc}")
        return 1
    return 0


def cmd_mesh(_args) -> int:
    cli = tailscale_cli()
    own, peers = tailscale_status()
    if not cli:
        print("Tailscale: not installed")
        return 1
    if not own:
        print(f"Tailscale CLI: {cli}")
        print("Tailscale: installed but not connected. Run: tailscale up")
        return 1
    print(f"Tailscale: connected | IP {own} | visible peers {len(peers)}")
    return 0


def cmd_model(args) -> int:
    cfg = load_config()
    if args.spec:
        cfg["model"] = args.spec
        save_config(cfg)
        print(f"Model set to: {args.spec}")
        print("Restart WQPU if it is currently running.")
    else:
        print(cfg["model"])
    return 0


def cmd_stop(_args) -> int:
    ensure_home()
    STOP_FILE.write_text("stop\n", encoding="utf-8")
    print("Stop signal written. Any WQPU process on this computer will exit.")
    return 0


def cmd_update(_args) -> int:
    server, rpc, tag = ensure_runtime(force=True)
    print(f"Updated llama.cpp runtime to {tag}")
    print(server)
    print(rpc)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="wqpu", description="Run one llama.cpp model across trusted LAN/Tailscale computers.")
    parser.add_argument("--version", action="version", version=f"WQPU {VERSION}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="join the cluster in the foreground")
    sub.add_parser("status", help="show the last local cluster status")
    sub.add_parser("doctor", help="show hardware/runtime/network information")
    sub.add_parser("mesh", help="check secure Tailscale remote networking")
    p_model = sub.add_parser("model", help="show or set Hugging Face GGUF repo[:quant]")
    p_model.add_argument("spec", nargs="?")
    sub.add_parser("stop", help="stop WQPU on this computer")
    sub.add_parser("update", help="download the latest llama.cpp runtime")
    args = parser.parse_args()
    try:
        if args.command in (None, "start"):
            return run_cluster(load_config())
        if args.command == "status":
            return cmd_status(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "mesh":
            return cmd_mesh(args)
        if args.command == "model":
            return cmd_model(args)
        if args.command == "stop":
            return cmd_stop(args)
        if args.command == "update":
            return cmd_update(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"WQPU error: {exc}", file=sys.stderr)
        print(f"Logs/config live in: {HOME}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
