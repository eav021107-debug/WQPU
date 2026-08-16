#!/usr/bin/env python3
from __future__ import annotations
import argparse, asyncio, json, secrets, ssl, time
from dataclasses import dataclass, field
from pathlib import Path

def jline(obj): return (json.dumps(obj, separators=(",", ":")) + "\n").encode()

@dataclass
class Control:
    node_id: str
    info: dict
    writer: asyncio.StreamWriter
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

@dataclass
class Pair:
    dial_reader: asyncio.StreamReader | None = None
    dial_writer: asyncio.StreamWriter | None = None
    accept_reader: asyncio.StreamReader | None = None
    accept_writer: asyncio.StreamWriter | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    started: bool = False

class Relay:
    def __init__(self, secret: str):
        self.secret = secret
        self.controls: dict[str, Control] = {}
        self.pairs: dict[str, Pair] = {}

    async def send(self, node_id: str, msg: dict):
        c = self.controls.get(node_id)
        if not c: raise RuntimeError("target offline")
        async with c.lock:
            c.writer.write(jline(msg)); await c.writer.drain()

    def snapshot(self):
        nodes=[]
        for nid,c in self.controls.items():
            info=dict(c.info); info["node_id"]=nid; nodes.append(info)
        nodes.sort(key=lambda n:n["node_id"])
        return {"type":"nodes","nodes":nodes}

    async def broadcast(self):
        msg=self.snapshot()
        for nid in list(self.controls):
            try: await self.send(nid,msg)
            except Exception: pass

    async def pipe(self, reader, writer):
        try:
            while True:
                data=await reader.read(65536)
                if not data: break
                writer.write(data); await writer.drain()
        except Exception: pass
        try: writer.close(); await writer.wait_closed()
        except Exception: pass

    async def maybe_start_pair(self,sid):
        p=self.pairs.get(sid)
        if not p or p.started or not (p.dial_reader and p.accept_reader): return
        p.started=True
        p.dial_writer.write(b"WQPU-READY\n"); p.accept_writer.write(b"WQPU-READY\n")
        await p.dial_writer.drain(); await p.accept_writer.drain()
        async def run():
            await asyncio.gather(self.pipe(p.dial_reader,p.accept_writer),self.pipe(p.accept_reader,p.dial_writer))
            p.done.set(); self.pairs.pop(sid,None)
        asyncio.create_task(run())

    async def handle(self,reader,writer):
        try:
            raw=await asyncio.wait_for(reader.readline(),10); hello=json.loads(raw.decode())
            if not secrets.compare_digest(str(hello.get("secret","")),self.secret):
                writer.close(); await writer.wait_closed(); return
            role=hello.get("role"); node=str(hello.get("node_id",""))
            if not node: raise ValueError("missing node_id")
            if role=="control":
                ctrl=Control(node,dict(hello.get("info") or {}),writer); old=self.controls.get(node)
                if old:
                    try: old.writer.close()
                    except Exception: pass
                self.controls[node]=ctrl; await self.broadcast()
                try:
                    while True:
                        line=await reader.readline()
                        if not line: break
                        msg=json.loads(line.decode())
                        if msg.get("type")=="ping":
                            async with ctrl.lock:
                                writer.write(jline({"type":"pong","t":time.time()})); await writer.drain()
                        elif msg.get("type")=="info":
                            ctrl.info.update(msg.get("info") or {}); await self.broadcast()
                finally:
                    if self.controls.get(node) is ctrl:
                        self.controls.pop(node,None); await self.broadcast()
                return
            if role=="dial":
                target=str(hello.get("target","")); service=str(hello.get("service",""))
                if target not in self.controls or node not in self.controls: raise RuntimeError("source or target offline")
                sid=secrets.token_hex(12); p=Pair(dial_reader=reader,dial_writer=writer); self.pairs[sid]=p
                await self.send(target,{"type":"open","stream":sid,"service":service})
                try: await asyncio.wait_for(p.done.wait(),timeout=3600)
                except asyncio.TimeoutError: pass
                return
            if role=="accept":
                sid=str(hello.get("stream","")); p=self.pairs.get(sid)
                if not p: raise RuntimeError("unknown stream")
                p.accept_reader,p.accept_writer=reader,writer; await self.maybe_start_pair(sid); await p.done.wait(); return
            raise ValueError("unknown role")
        except Exception as exc:
            try: writer.write(jline({"type":"error","error":str(exc)})); await writer.drain()
            except Exception: pass
            try: writer.close(); await writer.wait_closed()
            except Exception: pass

async def main_async(args):
    secret=Path(args.secret_file).read_text().strip(); ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(args.cert,args.key)
    relay=Relay(secret); server=await asyncio.start_server(relay.handle,args.host,args.port,ssl=ctx)
    print("WQPU peer relay listening on "+", ".join(str(s.getsockname()) for s in server.sockets or []),flush=True)
    async with server: await server.serve_forever()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--host",default="0.0.0.0"); ap.add_argument("--port",type=int,default=7443); ap.add_argument("--cert",required=True); ap.add_argument("--key",required=True); ap.add_argument("--secret-file",required=True)
    asyncio.run(main_async(ap.parse_args()))

if __name__=="__main__": main()
