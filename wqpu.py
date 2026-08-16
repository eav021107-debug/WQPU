#!/usr/bin/env python3
"""WQPU equal-peer client.

Each online node contributes a localhost llama.cpp RPC worker. The node that
originates a question starts a temporary local llama-server for that request,
connects the other online workers through the encrypted relay, prints the
answer, then tears the temporary coordinator down.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import hashlib
import json
import os
import platform
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid
import zipfile
from pathlib import Path

VERSION = "0.5.0"
HOME = Path(os.environ.get("WQPU_HOME", str(Path.home() / ".wqpu"))).expanduser()
NET_FILE = HOME / "network.json"
NODE_FILE = HOME / "node-id"
STATUS_FILE = HOME / "status.json"
RUNTIME_DIR = HOME / "runtime"
LOG_DIR = HOME / "logs"
RPC_PORT = 50052
DEFAULT_MODEL = "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M"
STATUS_MAX_AGE = 30


def ensure_home() -> None:
    for path in (HOME, RUNTIME_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)


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
    try:
        system = platform.system()
        if system == "Linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
        if system == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
            return int(out) // 1048576
        if system == "Windows":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            state = MemoryStatus()
            state.dwLength = ctypes.sizeof(MemoryStatus)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state))
            return int(state.ullTotalPhys // 1048576)
    except Exception:
        pass
    return 0


def threads_for() -> int:
    fraction = max(0.10, min(float(os.environ.get("WQPU_CPU_FRACTION", "0.5")), 0.90))
    return max(1, int((os.cpu_count() or 2) * fraction))


def model_name() -> str:
    return os.environ.get("WQPU_MODEL", DEFAULT_MODEL)


def reserve_mb() -> int:
    ram = total_ram_mb()
    return max(128, min(1024, int(ram * 0.12) if ram else 256))


def download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": f"WQPU/{VERSION}"})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
        shutil.copyfileobj(response, out, 1024 * 1024)


def api_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": f"WQPU/{VERSION}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


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
    for path in list(root.rglob(stem)) + list(root.rglob(stem + ".exe")):
        if path.is_file():
            if os.name != "nt":
                path.chmod(path.stat().st_mode | 0o111)
            return path
    raise FileNotFoundError(stem)


def ensure_runtime() -> tuple[Path, Path, str]:
    ensure_home()
    meta_file = RUNTIME_DIR / "current.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            server = Path(meta["server"])
            rpc = Path(meta["rpc"])
            if server.exists() and rpc.exists():
                return server, rpc, meta.get("tag", "cached")
        except Exception:
            pass

    print("WQPU: downloading llama.cpp...")
    release = api_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest")
    tag = release["tag_name"]
    suffix = asset_suffix()
    asset = next((item for item in release.get("assets", []) if item.get("name", "").endswith(suffix)), None)
    if not asset:
        raise RuntimeError(f"no llama.cpp asset for {suffix}")

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
    meta_file.write_text(json.dumps({"tag": tag, "server": str(server), "rpc": str(rpc)}, indent=2) + "\n", encoding="utf-8")
    return server, rpc, tag


def parse_token(token: str) -> dict:
    if not token.startswith("WQPU1."):
        raise ValueError("bad WQPU join token")
    raw = token.split(".", 1)[1]
    raw += "=" * ((4 - len(raw) % 4) % 4)
    data = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    for key in ("host", "port", "secret", "fingerprint"):
        if key not in data:
            raise ValueError(f"join token missing {key}")
    data["port"] = int(data["port"])
    data["fingerprint"] = data["fingerprint"].lower().replace(":", "")
    return data


def save_network(token: str) -> dict:
    ensure_home()
    data = parse_token(token)
    data["token"] = token
    NET_FILE.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def load_network() -> dict:
    if not NET_FILE.exists():
        raise RuntimeError("not joined; run: wqpu join <TOKEN>")
    return json.loads(NET_FILE.read_text(encoding="utf-8"))


async def tls_connect(net: dict):
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    reader, writer = await asyncio.open_connection(
        net["host"], int(net["port"]), ssl=context, server_hostname=net["host"]
    )
    ssl_object = writer.get_extra_info("ssl_object")
    cert = ssl_object.getpeercert(binary_form=True) if ssl_object else None
    fingerprint = hashlib.sha256(cert or b"").hexdigest()
    if fingerprint.lower() != str(net["fingerprint"]).lower():
        writer.close()
        await writer.wait_closed()
        raise RuntimeError("relay certificate fingerprint mismatch")
    return reader, writer


def hello(net: dict, role: str, **extra) -> bytes:
    data = {"role": role, "secret": net["secret"], "node_id": node_id()}
    data.update(extra)
    return (json.dumps(data, separators=(",", ":")) + "\n").encode()


async def relay_stream(net: dict, role: str, **extra):
    reader, writer = await tls_connect(net)
    writer.write(hello(net, role, **extra))
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), 15)
    if line != b"WQPU-READY\n":
        try:
            error = json.loads(line.decode()).get("error", line.decode(errors="ignore"))
        except Exception:
            error = line.decode(errors="ignore")
        writer.close()
        await writer.wait_closed()
        raise RuntimeError(f"relay stream failed: {error}")
    return reader, writer


async def copy_stream(reader, writer) -> None:
    try:
        while True:
            block = await reader.read(65536)
            if not block:
                break
            writer.write(block)
            await writer.drain()
    except Exception:
        pass
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def bridge(a_reader, a_writer, b_reader, b_writer) -> None:
    await asyncio.gather(copy_stream(a_reader, b_writer), copy_stream(b_reader, a_writer))


def process_kwargs() -> dict:
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)}
    return {"preexec_fn": lambda: os.nice(7)}


def start_process(command: list[str], log_name: str) -> subprocess.Popen:
    ensure_home()
    log = (LOG_DIR / log_name).open("a", encoding="utf-8")
    return subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, **process_kwargs())


def stop_process(process: subprocess.Popen | None) -> None:
    if not process or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


class State:
    def __init__(self) -> None:
        self.nodes: list[dict] = []

    def update(self, message: dict) -> None:
        self.nodes = list(message.get("nodes") or [])
        write_status(self.nodes)

    def touch(self) -> None:
        write_status(self.nodes)


def write_status(nodes: list[dict]) -> None:
    ensure_home()
    STATUS_FILE.write_text(
        json.dumps(
            {
                "version": VERSION,
                "updated": time.time(),
                "node_id": node_id(),
                "nodes": nodes,
                "role": "peer",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


async def handle_open(net: dict, message: dict) -> None:
    if message.get("service") != "rpc":
        return
    try:
        relay_reader, relay_writer = await relay_stream(net, "accept", stream=message.get("stream"))
        local_reader, local_writer = await asyncio.open_connection("127.0.0.1", RPC_PORT)
        await bridge(relay_reader, relay_writer, local_reader, local_writer)
    except Exception:
        pass


async def control_loop(net: dict, state: State, stop: asyncio.Event) -> None:
    info = {
        "hostname": socket.gethostname(),
        "ram_mb": total_ram_mb(),
        "threads": threads_for(),
        "version": VERSION,
    }
    delay = 1
    while not stop.is_set():
        try:
            reader, writer = await tls_connect(net)
            writer.write(hello(net, "control", info=info))
            await writer.drain()
            delay = 1

            async def pinger() -> None:
                while not stop.is_set():
                    await asyncio.sleep(10)
                    writer.write(b'{"type":"ping"}\n')
                    await writer.drain()
                    state.touch()

            ping_task = asyncio.create_task(pinger())
            try:
                while not stop.is_set():
                    line = await reader.readline()
                    if not line:
                        break
                    message = json.loads(line.decode())
                    if message.get("type") == "nodes":
                        state.update(message)
                    elif message.get("type") == "pong":
                        state.touch()
                    elif message.get("type") == "open":
                        asyncio.create_task(handle_open(net, message))
            finally:
                ping_task.cancel()
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        except Exception as exc:
            print(f"WQPU relay reconnect: {exc}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 15)


async def dial_proxy(net: dict, target: str, client_reader, client_writer) -> None:
    try:
        relay_reader, relay_writer = await relay_stream(net, "dial", target=target, service="rpc")
        await bridge(client_reader, client_writer, relay_reader, relay_writer)
    except Exception:
        try:
            client_writer.close()
            await client_writer.wait_closed()
        except Exception:
            pass


async def run_node() -> int:
    net = load_network()
    _, rpc_bin, tag = ensure_runtime()
    print(f"WQPU {VERSION} | equal peer | llama.cpp {tag}")
    print(
        f"Node: {socket.gethostname()} | RAM {total_ram_mb()} MiB | "
        f"contributes {threads_for()}/{os.cpu_count() or '?'} CPU threads"
    )
    rpc = start_process(
        [
            str(rpc_bin),
            "--host",
            "127.0.0.1",
            "--port",
            str(RPC_PORT),
            "--threads",
            str(threads_for()),
            "--device",
            "CPU",
            "--cache",
        ],
        "rpc.log",
    )

    stop = asyncio.Event()
    state = State()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    control = asyncio.create_task(control_loop(net, state, stop))
    write_status([])
    try:
        while not stop.is_set():
            if rpc.poll() is not None:
                raise RuntimeError("llama.cpp RPC worker exited; see ~/.wqpu/logs/rpc.log")
            await asyncio.sleep(1)
    finally:
        control.cancel()
        stop_process(rpc)
    return 0


def current_peers() -> list[dict]:
    if not STATUS_FILE.exists():
        raise RuntimeError("WQPU node is not running")
    status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    if time.time() - status.get("updated", 0) > STATUS_MAX_AGE:
        raise RuntimeError("WQPU node status is stale; start wqpu first")
    return [item for item in status.get("nodes", []) if item.get("node_id") != node_id()]


async def wait_http(port: int, process: subprocess.Popen) -> None:
    for _ in range(180):
        if process.poll() is not None:
            raise RuntimeError("local llama-server exited; see ~/.wqpu/logs/request.log")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError("local llama-server did not become ready")


async def request_once(text: str) -> int:
    net = load_network()
    server_bin, _, _ = ensure_runtime()
    peers = current_peers()
    proxy_servers = []
    endpoints = []
    process = None

    try:
        for target in [item.get("node_id") for item in peers if item.get("node_id")]:
            async def handler(reader, writer, peer_id=target):
                await dial_proxy(net, peer_id, reader, writer)

            server = await asyncio.start_server(handler, "127.0.0.1", 0)
            proxy_servers.append(server)
            port = server.sockets[0].getsockname()[1]
            endpoints.append(f"127.0.0.1:{port}")

        api_server = socket.socket()
        api_server.bind(("127.0.0.1", 0))
        api_port = api_server.getsockname()[1]
        api_server.close()

        command = [
            str(server_bin),
            "--hf-repo",
            model_name(),
            "--threads",
            str(threads_for()),
            "--threads-batch",
            str(threads_for()),
            "--ctx-size",
            "4096",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--prio",
            "-1",
            "--poll",
            "0",
            "--parallel",
            "1",
            "--fit",
            "on",
            "--fit-target",
            str(reserve_mb()),
        ]
        if endpoints:
            command += ["--rpc", ",".join(endpoints)]

        process = start_process(command, "request.log")
        print(f"Using this computer + {len(peers)} other online node(s)...")
        await wait_http(api_port, process)

        payload = json.dumps(
            {
                "model": model_name(),
                "messages": [{"role": "user", "content": text}],
                "stream": False,
            }
        ).encode()

        def call_api():
            request = urllib.request.Request(
                f"http://127.0.0.1:{api_port}/v1/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=1200) as response:
                return json.load(response)

        result = await asyncio.to_thread(call_api)
        print(result["choices"][0]["message"]["content"])
    finally:
        stop_process(process)
        for server in proxy_servers:
            server.close()
        for server in proxy_servers:
            try:
                await server.wait_closed()
            except Exception:
                pass
    return 0


def cmd_status() -> int:
    if not STATUS_FILE.exists():
        print("WQPU: no status yet")
        return 1
    status = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
    live = time.time() - status.get("updated", 0) < STATUS_MAX_AGE
    print(f"WQPU: {'RUNNING' if live else 'STALE'} | equal peer | nodes={len(status.get('nodes', []))}")
    for item in status.get("nodes", []):
        print(f"- {item.get('hostname')} | RAM {item.get('ram_mb')} MiB | threads {item.get('threads')}")
    print("The computer that asks a question coordinates only that request.")
    return 0 if live else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="wqpu")
    parser.add_argument("--version", action="version", version=f"WQPU {VERSION}")
    sub = parser.add_subparsers(dest="command")
    join = sub.add_parser("join")
    join.add_argument("token")
    sub.add_parser("start")
    sub.add_parser("status")
    ask = sub.add_parser("ask")
    ask.add_argument("text", nargs="+")
    args = parser.parse_args()

    try:
        if args.command == "join":
            data = save_network(args.token)
            print(f"Joined WQPU relay {data['host']}:{data['port']}")
            return 0
        if args.command == "status":
            return cmd_status()
        if args.command == "ask":
            return asyncio.run(request_once(" ".join(args.text)))
        return asyncio.run(run_node())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"WQPU error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
