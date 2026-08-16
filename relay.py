#!/usr/bin/env python3
"""WQPU encrypted rendezvous/relay.

The relay does not run the model and never chooses a leader. It only keeps the
list of online peers and forwards authenticated RPC streams between them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path


def jline(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode()


@dataclass
class Control:
    node_id: str
    info: dict
    writer: asyncio.StreamWriter
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


@dataclass
class Pair:
    source: str
    target: str
    service: str
    dial_reader: asyncio.StreamReader
    dial_writer: asyncio.StreamWriter
    accept_reader: asyncio.StreamReader | None = None
    accept_writer: asyncio.StreamWriter | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    started: bool = False


class Relay:
    def __init__(self, secret: str):
        self.secret = secret
        self.controls: dict[str, Control] = {}
        self.pairs: dict[str, Pair] = {}

    async def send(self, node_id: str, message: dict) -> None:
        control = self.controls.get(node_id)
        if not control:
            raise RuntimeError("target offline")
        async with control.lock:
            control.writer.write(jline(message))
            await control.writer.drain()

    def snapshot(self) -> dict:
        nodes = []
        for node_id, control in self.controls.items():
            info = dict(control.info)
            info["node_id"] = node_id
            nodes.append(info)
        nodes.sort(key=lambda item: item["node_id"])
        return {"type": "nodes", "nodes": nodes}

    async def broadcast(self) -> None:
        message = self.snapshot()
        for node_id in list(self.controls):
            try:
                await self.send(node_id, message)
            except Exception:
                pass

    async def pipe(self, reader, writer) -> None:
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

    async def maybe_start_pair(self, stream_id: str) -> None:
        pair = self.pairs.get(stream_id)
        if not pair or pair.started or not pair.accept_reader or not pair.accept_writer:
            return
        pair.started = True
        pair.dial_writer.write(b"WQPU-READY\n")
        pair.accept_writer.write(b"WQPU-READY\n")
        await pair.dial_writer.drain()
        await pair.accept_writer.drain()

        async def run() -> None:
            try:
                await asyncio.gather(
                    self.pipe(pair.dial_reader, pair.accept_writer),
                    self.pipe(pair.accept_reader, pair.dial_writer),
                )
            finally:
                pair.done.set()
                self.pairs.pop(stream_id, None)

        asyncio.create_task(run())

    async def handle(self, reader, writer) -> None:
        try:
            raw = await asyncio.wait_for(reader.readline(), 10)
            hello = json.loads(raw.decode())
            if not secrets.compare_digest(str(hello.get("secret", "")), self.secret):
                writer.close()
                await writer.wait_closed()
                return

            role = hello.get("role")
            node = str(hello.get("node_id", ""))
            if not node:
                raise ValueError("missing node_id")

            if role == "control":
                control = Control(node, dict(hello.get("info") or {}), writer)
                old = self.controls.get(node)
                if old:
                    try:
                        old.writer.close()
                    except Exception:
                        pass
                self.controls[node] = control
                await self.broadcast()
                try:
                    while True:
                        line = await reader.readline()
                        if not line:
                            break
                        message = json.loads(line.decode())
                        if message.get("type") == "ping":
                            async with control.lock:
                                writer.write(jline({"type": "pong", "t": time.time()}))
                                await writer.drain()
                        elif message.get("type") == "info":
                            control.info.update(message.get("info") or {})
                            await self.broadcast()
                finally:
                    if self.controls.get(node) is control:
                        self.controls.pop(node, None)
                        await self.broadcast()
                return

            if role == "dial":
                target = str(hello.get("target", ""))
                service = str(hello.get("service", ""))
                if service != "rpc":
                    raise RuntimeError("unsupported service")
                if node not in self.controls or target not in self.controls:
                    raise RuntimeError("source or target offline")

                stream_id = secrets.token_hex(16)
                pair = Pair(
                    source=node,
                    target=target,
                    service=service,
                    dial_reader=reader,
                    dial_writer=writer,
                )
                self.pairs[stream_id] = pair
                try:
                    await self.send(target, {"type": "open", "stream": stream_id, "service": service})
                    await asyncio.wait_for(pair.done.wait(), timeout=3600)
                finally:
                    if not pair.started:
                        self.pairs.pop(stream_id, None)
                        try:
                            writer.close()
                            await writer.wait_closed()
                        except Exception:
                            pass
                return

            if role == "accept":
                stream_id = str(hello.get("stream", ""))
                pair = self.pairs.get(stream_id)
                if not pair:
                    raise RuntimeError("unknown stream")
                if node != pair.target:
                    raise RuntimeError("wrong stream target")
                if pair.accept_reader is not None:
                    raise RuntimeError("stream already accepted")
                pair.accept_reader = reader
                pair.accept_writer = writer
                await self.maybe_start_pair(stream_id)
                await pair.done.wait()
                return

            raise ValueError("unknown role")
        except Exception as exc:
            try:
                writer.write(jline({"type": "error", "error": str(exc)}))
                await writer.drain()
            except Exception:
                pass
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


async def main_async(args) -> None:
    secret = Path(args.secret_file).read_text(encoding="utf-8").strip()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(args.cert, args.key)
    relay = Relay(secret)
    server = await asyncio.start_server(relay.handle, args.host, args.port, ssl=context)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    print(f"WQPU peer relay listening on {addresses}", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7443)
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--secret-file", required=True)
    asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
