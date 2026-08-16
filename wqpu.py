#!/usr/bin/env python3
"""WQPU: a tiny LAN launcher for one llama.cpp model across several computers."""
from __future__ import annotations

import argparse
import ctypes
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

VERSION = "0.1.0"
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
PEER_TTL = 6.0
BROADCAST_EVERY = 1.0
DEFAULTS = {
    "cluster": "home",
    "model": "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M",
    "cpu_fraction": 0.50,
    "ram_reserve_fraction": 0.30,
    "min_ram_reserve_mb": 4096,
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
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
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
    minimum = int(cfg.get("min_ram_reserve_mb", 4096))
    fraction = max(0.10, min(float(cfg.get("ram_reserve_fraction", 0.3)), 0.8))
    return max(minimum, int(ram_mb * fraction)) if ram_mb else minimum


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
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, **kwargs)
    return proc


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

    def payload(self) -> bytes:
        data = {
            "magic": "WQPU1",
            "version": VERSION,
            "cluster": self.cfg["cluster"],
            "node": self.node,
            "hostname": self.hostname,
            "ram_mb": self.ram,
            "threads": self.threads,
            "model": self.cfg["model"],
            "rpc_port": int(self.cfg["rpc_port"]),
            "api_port": int(self.cfg["api_port"]),
        }
        return json.dumps(data, separators=(",", ":")).encode("utf-8")

    def run(self) -> None:
        port = int(self.cfg["discovery_port"])
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind(("", port))
        sock.settimeout(0.4)
        self.sock = sock
        last_send = 0.0
        while not self.stop_event.is_set():
            now = time.time()
            if now - last_send >= BROADCAST_EVERY:
                try:
                    sock.sendto(self.payload(), ("255.255.255.255", port))
                except OSError:
                    pass
                last_send = now
            try:
                raw, addr = sock.recvfrom(8192)
                data = json.loads(raw.decode("utf-8"))
                if data.get("magic") != "WQPU1" or data.get("cluster") != self.cfg["cluster"]:
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


def write_status(cfg: dict, role: str, peers: list[dict], coordinator: str, child: subprocess.Popen | None) -> None:
    self_info = {
        "node": node_id(),
        "hostname": socket.gethostname(),
        "ip": local_ip(),
        "ram_mb": total_ram_mb(),
        "threads": threads_for(cfg),
        "model": cfg["model"],
    }
    data = {
        "version": VERSION,
        "updated": time.time(),
        "role": role,
        "coordinator": coordinator,
        "self": self_info,
        "peers": peers,
        "api_url": f"http://{local_ip()}:{cfg['api_port']}" if role == "coordinator" else None,
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
    thread = threading.Thread(target=disc.run, daemon=True)
    thread.start()
    print(f"WQPU {VERSION} | llama.cpp {tag}")
    print(f"Node: {socket.gethostname()} | RAM {total_ram_mb()} MiB | CPU threads reserved for WQPU: {threads_for(cfg)}/{os.cpu_count() or '?'}")
    print("Searching for WQPU nodes on the local network...")

    child: subprocess.Popen | None = None
    signature = None
    stopping = False

    def on_signal(_sig, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)

    time.sleep(2.5)
    try:
        while not stopping and not STOP_FILE.exists():
            peers = disc.snapshot()
            all_nodes = [disc.node] + [p["node"] for p in peers]
            coordinator = min(all_nodes)
            role = "coordinator" if coordinator == disc.node else "worker"
            peer_endpoints = sorted(f"{p['ip']}:{p.get('rpc_port', cfg['rpc_port'])}" for p in peers if p["node"] != coordinator)
            if role == "coordinator":
                peer_endpoints = sorted(f"{p['ip']}:{p.get('rpc_port', cfg['rpc_port'])}" for p in peers)
            model_mismatch = [p for p in peers if p.get("model") != cfg["model"]]
            current = (role, tuple(peer_endpoints), cfg["model"])
            dead = child is not None and child.poll() is not None

            if current != signature or child is None or dead:
                terminate(child)
                child = None
                signature = current
                time.sleep(1.2)
                if role == "worker":
                    cmd = [
                        str(rpc_bin), "--host", "0.0.0.0", "--port", str(cfg["rpc_port"]),
                        "--threads", str(threads_for(cfg)), "--device", "CPU", "--cache",
                    ]
                    child = start_process(cmd, "rpc.log")
                    print(f"Role: WORKER | coordinator node {coordinator[:8]} | RPC :{cfg['rpc_port']}")
                else:
                    cmd = [
                        str(server_bin), "--hf-repo", str(cfg["model"]),
                        "--threads", str(threads_for(cfg)), "--threads-batch", str(threads_for(cfg)),
                        "--ctx-size", str(cfg["context"]), "--host", "0.0.0.0", "--port", str(cfg["api_port"]),
                        "--prio", "-1", "--poll", "0", "--fit", "on", "--fit-target", str(reserve_mb(cfg)),
                    ]
                    if peer_endpoints:
                        cmd += ["--rpc", ",".join(peer_endpoints)]
                    child = start_process(cmd, "server.log")
                    print(f"Role: COORDINATOR | {len(peers) + 1} node(s) | UI/API: http://{local_ip()}:{cfg['api_port']}")
                    if peer_endpoints:
                        print("RPC workers: " + ", ".join(peer_endpoints))
                if model_mismatch:
                    print("Warning: some nodes have a different model setting; coordinator setting wins.")

            write_status(cfg, role, peers, coordinator, child)
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
    print(f"Self: {me.get('hostname')} {me.get('ip')} | RAM {me.get('ram_mb')} MiB | threads {me.get('threads')}")
    for p in data.get("peers", []):
        print(f"Peer: {p.get('hostname')} {p.get('ip')} | RAM {p.get('ram_mb')} MiB | threads {p.get('threads')}")
    if data.get("api_url"):
        print("UI/API: " + data["api_url"])
    return 0 if live else 1


def cmd_doctor(_args) -> int:
    cfg = load_config()
    print(f"WQPU {VERSION}")
    print(f"OS: {platform.system()} {platform.release()} | arch={platform.machine()}")
    print(f"CPU logical threads: {os.cpu_count()} | WQPU target: {threads_for(cfg)}")
    ram = total_ram_mb()
    print(f"RAM: {ram} MiB | planned reserve: {reserve_mb(cfg, ram)} MiB")
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


def cmd_model(args) -> int:
    cfg = load_config()
    if args.spec:
        cfg["model"] = args.spec
        save_config(cfg)
        print(f"Model set to: {args.spec}")
        print("Restart WQPU on the coordinator if it is currently running.")
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
    parser = argparse.ArgumentParser(prog="wqpu", description="Run one llama.cpp model across several computers on a trusted LAN.")
    parser.add_argument("--version", action="version", version=f"WQPU {VERSION}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("start", help="join the LAN cluster in the foreground")
    sub.add_parser("status", help="show the last local cluster status")
    sub.add_parser("doctor", help="show hardware/runtime information")
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
