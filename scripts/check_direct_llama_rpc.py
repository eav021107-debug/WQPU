#!/usr/bin/env python3
"""Baseline: real pinned llama.cpp RPC inference with no WQPU transport.

This separates upstream/runtime limitations from WQPU relay behavior. It uses the exact
runtime, model and CPU RPC flags used by the WQPU heavy E2E, but connects llama-server
directly to ggml-rpc-server on loopback.
"""
from __future__ import print_function

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(str(ROOT))
sys.path.insert(0, str(ROOT))

import wqpu
import wqpu_runtime_pin

HOME = ROOT / ".wqpu-testnet" / "direct-llama-rpc"
MODEL = os.environ.get("WQPU_REAL_MODEL", "ggml-org/gemma-3-1b-it-GGUF:Q4_K_M")


def start(command, name):
    HOME.mkdir(parents=True, exist_ok=True)
    handle = (HOME / name).open("a", encoding="utf-8")
    env = os.environ.copy()
    env["GGML_RPC_DEBUG"] = "1"
    env["GGML_RDMA_DEV"] = "__wqpu_tcp_tunnel_only__"
    return subprocess.Popen(
        [str(x) for x in command], stdout=handle, stderr=subprocess.STDOUT,
        cwd=str(ROOT), env=env,
    )


def stop(proc):
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait(5)


def tail(name, lines=80):
    try:
        return "\n".join((HOME / name).read_text(errors="replace").splitlines()[-lines:])
    except Exception:
        return ""


def wait_tcp(port, proc, timeout=60):
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("direct rpc server exited:\n{}".format(tail("rpc.log")))
        try:
            sock = socket.create_connection(("127.0.0.1", int(port)), timeout=1)
            sock.close(); return
        except Exception:
            time.sleep(0.2)
    raise RuntimeError("direct rpc server did not listen")


def wait_http(port, proc, timeout=900):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("direct llama-server exited:\n{}\n--- rpc ---\n{}".format(
                tail("llama.log"), tail("rpc.log")
            ))
        try:
            with urllib.request.urlopen("http://127.0.0.1:{}/health".format(port), timeout=2) as r:
                body = json.load(r)
            if body.get("status") in ("ok", "no slot available") or body.get("ok") is True:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("direct llama-server model load timed out")


def chat(port):
    raw = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": "Reply briefly. What is two plus two?"}],
        "temperature": 0,
        "max_tokens": 24,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:{}/v1/chat/completions".format(port), data=raw,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as response:
        return json.load(response)


def main():
    import shutil
    if HOME.exists():
        shutil.rmtree(str(HOME))
    os.environ["WQPU_LLAMA_TAG"] = "b10456"
    wqpu.ensure_runtime = wqpu_runtime_pin.ensure_runtime
    server_bin, rpc_bin, tag = wqpu_runtime_pin.ensure_runtime()
    if tag != "b10456":
        raise RuntimeError("direct baseline did not use pinned b10456")

    rpc_port = wqpu.free_port()
    api_port = wqpu.free_port()
    rpc_proc = start([
        rpc_bin, "--host", "127.0.0.1", "--port", str(rpc_port),
        "--threads", "2", "--device", "CPU", "--cache",
    ], "rpc.log")
    llama_proc = None
    try:
        wait_tcp(rpc_port, rpc_proc)
        llama_proc = start([
            server_bin,
            "--hf-repo", MODEL,
            "--threads", "2", "--threads-batch", "2",
            "--ctx-size", "512", "--host", "127.0.0.1", "--port", str(api_port),
            "--parallel", "1", "--rpc", "127.0.0.1:{}".format(rpc_port),
        ], "llama.log")
        wait_http(api_port, llama_proc)
        result = chat(api_port)
        content = ""
        choices = result.get("choices") or []
        if choices:
            content = str(((choices[0].get("message") or {}).get("content") or "")).strip()
        if not content:
            raise RuntimeError("direct llama RPC returned no assistant content")
        print("DIRECT LLAMA RPC BASELINE OK")
        print("model={} llama_tag={} assistant={}".format(
            MODEL, tag, content.replace("\n", " ")[:160]
        ))
        return 0
    finally:
        stop(llama_proc)
        stop(rpc_proc)


if __name__ == "__main__":
    raise SystemExit(main())
