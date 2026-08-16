#!/usr/bin/env python3
"""WQPU equal-peer P2P node.

Every node runs the same code:
- contributes a localhost llama.cpp RPC worker;
- accepts peer connections when reachable;
- connects outbound to known peers;
- exchanges peer tables;
- can temporarily relay another peer's RPC stream;
- coordinates only the requests that originate on itself.

No permanent coordinator or dedicated relay role exists.
"""

import argparse
import asyncio
import base64
import ctypes
import hashlib
import json
import os
import platform
import secrets
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

VERSION = "0.5.1"
PORT = int(os.environ.get("WQPU_PORT", "7443"))
RPC_PORT = 50052
DEFAULT_MODEL = "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M"
HOME = Path(os.environ.get("WQPU_HOME", str(Path.home() / ".wqpu"))).expanduser()
CFG = HOME / "network.json"
NODE_ID_FILE = HOME / "node-id"
PEERS_FILE = HOME / "peers.json"
RUNTIME = HOME / "runtime"
LOGS = HOME / "logs"
CERT = HOME / "cert.pem"
KEY = HOME / "key.pem"


def ensure_home():
    for p in (HOME, RUNTIME, LOGS):
        p.mkdir(parents=True, exist_ok=True)


def node_id():
    ensure_home()
    if NODE_ID_FILE.exists():
        v = NODE_ID_FILE.read_text().strip()
        if v:
            return v
    v = uuid.uuid4().hex
    NODE_ID_FILE.write_text(v + "\n")
    return v


def total_ram_mb():
    try:
        s = platform.system()
        if s == "Linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
        if s == "Darwin":
            out = subprocess.check_output(
                ["sysctl", "-n", "hw.memsize"], universal_newlines=True
            ).strip()
            return int(out) // 1048576
        if s == "Windows":
            class M(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            m = M()
            m.dwLength = ctypes.sizeof(M)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullTotalPhys // 1048576)
    except Exception:
        pass
    return 0


def threads_for():
    frac = max(.1, min(float(os.environ.get("WQPU_CPU_FRACTION", "0.5")), .9))
    return max(1, int((os.cpu_count() or 2) * frac))


def model_name():
    return os.environ.get("WQPU_MODEL", DEFAULT_MODEL)


def reserve_mb():
    ram = total_ram_mb()
    return max(128, min(1024, int(ram * .12) if ram else 256))


def proc_kwargs():
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000)}
    return {"preexec_fn": lambda: os.nice(7)}


def start_proc(cmd, logname):
    ensure_home()
    log = (LOGS / logname).open("a", encoding="utf-8")
    return subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, **proc_kwargs())


def stop_proc(p):
    if not p or p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(5)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


async def close_writer(writer):
    if not writer:
        return
    try:
        writer.close()
    except Exception:
        return
    wait_closed = getattr(writer, "wait_closed", None)
    if wait_closed:
        try:
            await wait_closed()
        except Exception:
            pass


async def close_server(server):
    if not server:
        return
    try:
        server.close()
    except Exception:
        return
    wait_closed = getattr(server, "wait_closed", None)
    if wait_closed:
        try:
            await wait_closed()
        except Exception:
            pass


async def to_thread(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args))


def ensure_cert():
    ensure_home()
    if CERT.exists() and KEY.exists():
        return
    if not shutil.which("openssl"):
        raise RuntimeError("openssl is required to create the local WQPU TLS certificate")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
        "-days", "3650", "-subj", "/CN=WQPU Peer", "-keyout", str(KEY), "-out", str(CERT)
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        KEY.chmod(0o600)
    except Exception:
        pass


def cert_fingerprint():
    ensure_cert()
    der = subprocess.check_output(["openssl", "x509", "-in", str(CERT), "-outform", "DER"])
    return hashlib.sha256(der).hexdigest()


def server_ssl():
    ensure_cert()
    proto = getattr(ssl, "PROTOCOL_TLS_SERVER", ssl.PROTOCOL_TLS)
    ctx = ssl.SSLContext(proto)
    ctx.load_cert_chain(str(CERT), str(KEY))
    return ctx


def client_ssl():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def save_cfg(cfg):
    ensure_home()
    CFG.write_text(json.dumps(cfg, indent=2) + "\n")


def load_cfg():
    if not CFG.exists():
        return None
    return json.loads(CFG.read_text())


def encode_join(secret, peers):
    raw = json.dumps({"secret": secret, "peers": peers}, separators=(",", ":")).encode()
    return "WQPU1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_join(token):
    if not token.startswith("WQPU1."):
        raise ValueError("bad WQPU join code")
    raw = token.split(".", 1)[1]
    raw += "=" * ((4 - len(raw) % 4) % 4)
    d = json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    if not d.get("secret"):
        raise ValueError("join code has no network secret")
    d["peers"] = list(d.get("peers") or [])
    return d


def make_network(join_token=None):
    ensure_cert()
    if join_token:
        cfg = decode_join(join_token)
    else:
        cfg = {"secret": secrets.token_urlsafe(32), "peers": []}
    save_cfg(cfg)
    return cfg


def peer_key(host, port):
    return "{}:{}".format(host, int(port))


def load_peer_cache():
    try:
        return json.loads(PEERS_FILE.read_text())
    except Exception:
        return {}


def save_peer_cache(peers):
    ensure_home()
    PEERS_FILE.write_text(json.dumps(peers, indent=2) + "\n")


def download(url, dest):
    req = urllib.request.Request(url, headers={"User-Agent": "WQPU/{}".format(VERSION)})
    with urllib.request.urlopen(req, timeout=120) as r, dest.open("wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)


def api_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "WQPU/{}".format(VERSION)})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def asset_suffix():
    s = platform.system()
    m = platform.machine().lower()
    x = m in {"x86_64", "amd64", "x64"}
    a = m in {"arm64", "aarch64"}
    if s == "Windows" and x:
        return "-bin-win-cpu-x64.zip"
    if s == "Windows" and a:
        return "-bin-win-cpu-arm64.zip"
    if s == "Linux" and x:
        return "-bin-ubuntu-x64.tar.gz"
    if s == "Linux" and a:
        return "-bin-ubuntu-arm64.tar.gz"
    if s == "Darwin" and a:
        return "-bin-macos-arm64.tar.gz"
    if s == "Darwin" and x:
        return "-bin-macos-x64.tar.gz"
    raise RuntimeError("unsupported platform: {} {}".format(s, m))


def find_binary(root, stem):
    for p in list(root.rglob(stem)) + list(root.rglob(stem + ".exe")):
        if p.is_file():
            if os.name != "nt":
                p.chmod(p.stat().st_mode | 0o111)
            return p
    raise FileNotFoundError(stem)


def ensure_runtime():
    ensure_home()
    meta = RUNTIME / "current.json"
    if meta.exists():
        try:
            d = json.loads(meta.read_text())
            s, r = Path(d["server"]), Path(d["rpc"])
            if s.exists() and r.exists():
                return s, r, d.get("tag", "cached")
        except Exception:
            pass
    print("WQPU: downloading llama.cpp...")
    rel = api_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest")
    tag = rel["tag_name"]
    suf = asset_suffix()
    asset = next((x for x in rel["assets"] if x["name"].endswith(suf)), None)
    if not asset:
        raise RuntimeError("no llama.cpp asset for {}".format(suf))
    target = RUNTIME / tag
    if target.exists():
        shutil.rmtree(str(target))
    target.mkdir(parents=True)
    arc = RUNTIME / asset["name"]
    download(asset["browser_download_url"], arc)
    if arc.suffix == ".zip":
        with zipfile.ZipFile(str(arc)) as z:
            z.extractall(str(target))
    else:
        with tarfile.open(str(arc), "r:gz") as t:
            t.extractall(str(target))
    try:
        arc.unlink()
    except OSError:
        pass
    server = find_binary(target, "llama-server")
    rpc = find_binary(target, "ggml-rpc-server")
    meta.write_text(json.dumps(
        {"tag": tag, "server": str(server), "rpc": str(rpc)}, indent=2
    ) + "\n")
    return server, rpc, tag


async def copy_stream(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    await close_writer(writer)


async def bridge(ar, aw, br, bw):
    await asyncio.gather(copy_stream(ar, bw), copy_stream(br, aw))


class Control(object):
    def __init__(self, node, info, reader, writer):
        self.node = node
        self.info = info
        self.reader = reader
        self.writer = writer
        self.lock = asyncio.Lock()


class Pair(object):
    def __init__(self, dial_r=None, dial_w=None, accept_r=None, accept_w=None):
        self.dial_r = dial_r
        self.dial_w = dial_w
        self.accept_r = accept_r
        self.accept_w = accept_w
        self.done = asyncio.Event()
        self.started = False


class Mesh(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.secret = cfg["secret"]
        self.me = node_id()
        self.fp = cert_fingerprint()
        self.controls = {}
        self.outbound = {}
        self.routes = {}
        self.peer_info = {}
        self.pairs = {}
        self.stop = asyncio.Event()
        self.server = None

    def my_info(self):
        return {
            "node_id": self.me,
            "hostname": socket.gethostname(),
            "ram_mb": total_ram_mb(),
            "threads": threads_for(),
            "port": PORT,
            "fingerprint": self.fp,
            "model": model_name(),
            "version": VERSION,
        }

    def public_snapshot(self, observed_ip=None):
        info = self.my_info()
        if observed_ip:
            info["host"] = observed_ip
        return info

    async def send(self, ctrl, obj):
        async with ctrl.lock:
            ctrl.writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())
            await ctrl.writer.drain()

    async def broadcast_nodes(self):
        nodes = [self.public_snapshot()]
        for c in self.controls.values():
            x = dict(c.info)
            x["node_id"] = c.node
            nodes.append(x)
        msg = {"type": "nodes", "nodes": nodes}
        for c in list(self.controls.values()):
            try:
                await self.send(c, msg)
            except Exception:
                pass

    async def maybe_start_pair(self, sid):
        p = self.pairs.get(sid)
        if not p or p.started or not (p.dial_r and p.accept_r):
            return
        p.started = True
        p.dial_w.write(b"WQPU-READY\n")
        p.accept_w.write(b"WQPU-READY\n")
        await p.dial_w.drain()
        await p.accept_w.drain()

        async def run_pair():
            await bridge(p.dial_r, p.dial_w, p.accept_r, p.accept_w)
            p.done.set()
            self.pairs.pop(sid, None)

        asyncio.ensure_future(run_pair())

    async def handle_inbound(self, reader, writer):
        peername = writer.get_extra_info("peername")
        try:
            raw = await asyncio.wait_for(reader.readline(), 10)
            hello = json.loads(raw.decode())
            if not secrets.compare_digest(str(hello.get("secret", "")), self.secret):
                raise RuntimeError("authentication failed")
            role = hello.get("role")
            src = str(hello.get("node_id", ""))
            if not src:
                raise RuntimeError("missing node id")

            if role == "control":
                info = dict(hello.get("info") or {})
                if peername:
                    info["host"] = peername[0]
                ctrl = Control(src, info, reader, writer)
                old = self.controls.get(src)
                if old:
                    await close_writer(old.writer)
                self.controls[src] = ctrl
                self.peer_info[src] = info
                await self.broadcast_nodes()
                try:
                    while not self.stop.is_set():
                        line = await reader.readline()
                        if not line:
                            break
                        msg = json.loads(line.decode())
                        if msg.get("type") == "ping":
                            await self.send(ctrl, {"type": "pong", "t": time.time()})
                        elif msg.get("type") == "open":
                            await self.handle_open_request(msg)
                finally:
                    if self.controls.get(src) is ctrl:
                        self.controls.pop(src, None)
                        await self.broadcast_nodes()
                return

            if role == "dial":
                target = str(hello.get("target", ""))
                if target == self.me:
                    writer.write(b"WQPU-READY\n")
                    await writer.drain()
                    lr, lw = await asyncio.open_connection("127.0.0.1", RPC_PORT)
                    await bridge(reader, writer, lr, lw)
                    return
                target_ctrl = self.controls.get(target)
                if not target_ctrl:
                    raise RuntimeError("target not connected to this peer")
                sid = secrets.token_hex(12)
                p = Pair(dial_r=reader, dial_w=writer)
                self.pairs[sid] = p
                await self.send(target_ctrl, {"type": "open", "stream": sid, "service": "rpc"})
                try:
                    await asyncio.wait_for(p.done.wait(), 3600)
                except asyncio.TimeoutError:
                    pass
                finally:
                    self.pairs.pop(sid, None)
                return

            if role == "accept":
                sid = str(hello.get("stream", ""))
                p = self.pairs.get(sid)
                if not p:
                    raise RuntimeError("unknown stream")
                p.accept_r, p.accept_w = reader, writer
                await self.maybe_start_pair(sid)
                await p.done.wait()
                return

            raise RuntimeError("unknown role")
        except Exception as exc:
            try:
                writer.write((json.dumps({"type": "error", "error": str(exc)}) + "\n").encode())
                await writer.drain()
            except Exception:
                pass
            await close_writer(writer)

    async def start_listener(self):
        self.server = await asyncio.start_server(
            self.handle_inbound, "0.0.0.0", PORT, ssl=server_ssl()
        )

    async def connect_control(self, peer):
        host, port = peer["host"], int(peer.get("port", PORT))
        key = peer_key(host, port)
        if key in self.outbound:
            return
        r, w = await asyncio.open_connection(
            host, port, ssl=client_ssl(), server_hostname=host
        )
        sslobj = w.get_extra_info("ssl_object")
        cert = sslobj.getpeercert(binary_form=True) if sslobj else b""
        fp = hashlib.sha256(cert).hexdigest()
        expected = str(peer.get("fingerprint") or "").lower()
        if expected and fp.lower() != expected:
            await close_writer(w)
            raise RuntimeError("fingerprint mismatch for {}".format(key))
        hello = {
            "role": "control", "secret": self.secret, "node_id": self.me,
            "info": self.my_info()
        }
        w.write((json.dumps(hello, separators=(",", ":")) + "\n").encode())
        await w.drain()
        ctrl = Control(key, peer, r, w)
        self.outbound[key] = ctrl

        async def read_loop():
            try:
                while not self.stop.is_set():
                    line = await r.readline()
                    if not line:
                        break
                    msg = json.loads(line.decode())
                    if msg.get("type") == "nodes":
                        self.merge_nodes(key, msg.get("nodes") or [])
                    elif msg.get("type") == "open":
                        await self.handle_open_request(msg, via=peer)
                    elif msg.get("type") == "pong":
                        pass
            finally:
                self.outbound.pop(key, None)
                for routes in self.routes.values():
                    routes.discard(key)
                await close_writer(w)

        asyncio.ensure_future(read_loop())

    def merge_nodes(self, route_key, nodes):
        cache = load_peer_cache()
        for n in nodes:
            nid = n.get("node_id")
            if not nid or nid == self.me:
                continue
            self.peer_info[nid] = dict(n)
            self.routes.setdefault(nid, set()).add(route_key)
            host = n.get("host")
            port = int(n.get("port", PORT))
            fp = n.get("fingerprint")
            if host and fp:
                cache[peer_key(host, port)] = {
                    "host": host, "port": port, "fingerprint": fp
                }
        save_peer_cache(cache)

    async def connector_loop(self):
        while not self.stop.is_set():
            candidates = {}
            for p in self.cfg.get("peers") or []:
                if p.get("host"):
                    candidates[peer_key(p["host"], p.get("port", PORT))] = p
            candidates.update(load_peer_cache())
            for key, p in list(candidates.items()):
                if key in self.outbound:
                    continue
                try:
                    await asyncio.wait_for(self.connect_control(p), 5)
                except Exception:
                    pass
            for c in list(self.outbound.values()):
                try:
                    await self.send(c, {"type": "ping"})
                except Exception:
                    pass
            await asyncio.sleep(8)

    async def handle_open_request(self, msg, via=None):
        if msg.get("service") != "rpc":
            return
        sid = msg.get("stream")
        if not sid:
            return
        try:
            if via:
                rr, rw = await self.open_accept(via, sid)
            else:
                return
            lr, lw = await asyncio.open_connection("127.0.0.1", RPC_PORT)
            await bridge(rr, rw, lr, lw)
        except Exception:
            pass

    async def open_accept(self, hub, sid):
        r, w = await asyncio.open_connection(
            hub["host"], int(hub.get("port", PORT)),
            ssl=client_ssl(), server_hostname=hub["host"]
        )
        hello = {
            "role": "accept", "secret": self.secret,
            "node_id": self.me, "stream": sid
        }
        w.write((json.dumps(hello, separators=(",", ":")) + "\n").encode())
        await w.drain()
        line = await asyncio.wait_for(r.readline(), 15)
        if line != b"WQPU-READY\n":
            await close_writer(w)
            raise RuntimeError("relay accept failed")
        return r, w

    async def open_rpc(self, target):
        routes = list(self.routes.get(target) or [])
        for route_key in routes:
            hub = None
            all_peers = list(self.cfg.get("peers") or []) + list(load_peer_cache().values())
            for p in all_peers:
                if peer_key(p["host"], p.get("port", PORT)) == route_key:
                    hub = p
                    break
            if not hub:
                continue
            try:
                r, w = await asyncio.open_connection(
                    hub["host"], int(hub.get("port", PORT)),
                    ssl=client_ssl(), server_hostname=hub["host"]
                )
                hello = {
                    "role": "dial", "secret": self.secret,
                    "node_id": self.me, "target": target
                }
                w.write((json.dumps(hello, separators=(",", ":")) + "\n").encode())
                await w.drain()
                line = await asyncio.wait_for(r.readline(), 15)
                if line == b"WQPU-READY\n":
                    return r, w
                await close_writer(w)
            except Exception:
                pass
        raise RuntimeError("no route to peer")

    async def proxy_handler(self, target, cr, cw):
        try:
            rr, rw = await self.open_rpc(target)
            await bridge(cr, cw, rr, rw)
        except Exception:
            await close_writer(cw)

    def peers(self):
        out = []
        for nid, info in self.peer_info.items():
            if nid == self.me:
                continue
            if self.routes.get(nid):
                out.append((nid, info))
        return out


async def wait_http(port, proc):
    for _ in range(240):
        if proc.poll() is not None:
            raise RuntimeError("llama-server exited; see ~/.wqpu/logs/request.log")
        try:
            r, w = await asyncio.open_connection("127.0.0.1", port)
            await close_writer(w)
            return
        except Exception:
            await asyncio.sleep(.5)
    raise RuntimeError("llama-server did not become ready")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


async def ask(mesh, server_bin, text):
    peers = mesh.peers()
    proxy_servers = []
    endpoints = []
    try:
        for nid, _ in peers:
            async def h(r, w, target=nid):
                await mesh.proxy_handler(target, r, w)
            srv = await asyncio.start_server(h, "127.0.0.1", 0)
            proxy_servers.append(srv)
            endpoints.append("127.0.0.1:{}".format(
                srv.sockets[0].getsockname()[1]
            ))

        api_port = free_port()
        cmd = [
            str(server_bin), "--hf-repo", model_name(),
            "--threads", str(threads_for()), "--threads-batch", str(threads_for()),
            "--ctx-size", "4096", "--host", "127.0.0.1", "--port", str(api_port),
            "--prio", "-1", "--poll", "0", "--parallel", "1",
            "--fit", "on", "--fit-target", str(reserve_mb())
        ]
        if endpoints:
            cmd += ["--rpc", ",".join(endpoints)]
        proc = start_proc(cmd, "request.log")
        try:
            print("[using this computer + {} peer(s)]".format(len(peers)))
            await wait_http(api_port, proc)
            payload = json.dumps({
                "model": model_name(),
                "messages": [{"role": "user", "content": text}],
                "stream": False
            }).encode()

            def call():
                req = urllib.request.Request(
                    "http://127.0.0.1:{}/v1/chat/completions".format(api_port),
                    data=payload, headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=1200) as r:
                    return json.load(r)

            d = await to_thread(call)
            print(d["choices"][0]["message"]["content"])
        finally:
            stop_proc(proc)
    finally:
        for s in proxy_servers:
            await close_server(s)


def parse_hostport(text):
    if ":" not in text:
        return text, PORT
    host, p = text.rsplit(":", 1)
    return host, int(p)


async def interactive(mesh, server_bin):
    print("\nWQPU peer is online. Type a question.")
    print("Commands: /status  /peers  /invite HOST[:PORT]  /exit\n")
    while not mesh.stop.is_set():
        try:
            line = (await to_thread(input, "wqpu> ")).strip()
        except (EOFError, KeyboardInterrupt):
            line = "/exit"
        if not line:
            continue
        if line == "/exit":
            mesh.stop.set()
            break
        if line == "/status":
            print("WQPU {} | equal peer | reachable peers: {}".format(
                VERSION, len(mesh.peers())
            ))
            continue
        if line == "/peers":
            peers = mesh.peers()
            if not peers:
                print("No reachable peers yet.")
            for nid, info in peers:
                print("- {} | RAM {} MiB | CPU {} | {}".format(
                    info.get("hostname", "peer"),
                    info.get("ram_mb", "?"),
                    info.get("threads", "?"),
                    nid[:8],
                ))
            continue
        if line.startswith("/invite"):
            parts = line.split(maxsplit=1)
            if len(parts) != 2:
                print("Use: /invite PUBLIC_HOST[:PORT]")
                continue
            host, port = parse_hostport(parts[1])
            peer = {"host": host, "port": port, "fingerprint": mesh.fp}
            print(encode_join(mesh.secret, [peer]))
            continue
        try:
            await ask(mesh, server_bin, line)
        except Exception as exc:
            print("WQPU error: {}".format(exc))


async def run(join_token=None):
    cfg = load_cfg()
    if not cfg:
        cfg = make_network(join_token)
        if not join_token:
            print("Created a new WQPU network.")
            print("After startup use /invite PUBLIC_HOST[:PORT] to make a join code.")
    server_bin, rpc_bin, tag = ensure_runtime()
    mesh = Mesh(cfg)
    await mesh.start_listener()
    rpc = start_proc([
        str(rpc_bin), "--host", "127.0.0.1", "--port", str(RPC_PORT),
        "--threads", str(threads_for()), "--device", "CPU", "--cache"
    ], "rpc.log")
    print("WQPU {} | llama.cpp {}".format(VERSION, tag))
    print("Node {} | RAM {} MiB | contributes {}/{} CPU threads".format(
        socket.gethostname(), total_ram_mb(), threads_for(), os.cpu_count() or "?"
    ))
    print("P2P listen: TCP {}".format(PORT))
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, mesh.stop.set)
        except (NotImplementedError, RuntimeError, ValueError):
            pass
    connector = asyncio.ensure_future(mesh.connector_loop())
    try:
        await interactive(mesh, server_bin)
    finally:
        mesh.stop.set()
        connector.cancel()
        await close_server(mesh.server)
        stop_proc(rpc)


def run_async(coro):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        all_tasks = getattr(asyncio, "all_tasks", None)
        if all_tasks:
            try:
                pending = all_tasks(loop=loop)
            except TypeError:
                pending = all_tasks()
        else:
            pending = asyncio.Task.all_tasks(loop=loop)
        for task in pending:
            task.cancel()
        if pending:
            try:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        loop.close()


def main():
    ap = argparse.ArgumentParser(prog="wqpu")
    ap.add_argument("--version", action="version", version="WQPU {}".format(VERSION))
    ap.add_argument("--join", help="WQPU1 join code")
    args = ap.parse_args()
    try:
        run_async(run(args.join or os.environ.get("WQPU_JOIN")))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print("WQPU error: {}".format(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
