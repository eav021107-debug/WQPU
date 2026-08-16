#!/usr/bin/env python3
from __future__ import annotations
import argparse, asyncio, base64, ctypes, hashlib, json, os, platform, shutil, signal, socket, ssl, subprocess, sys, tarfile, time, urllib.request, uuid, zipfile
from pathlib import Path

VERSION="0.3.0"
HOME=Path(os.environ.get("WQPU_HOME", str(Path.home()/".wqpu"))).expanduser()
NET_FILE=HOME/"network.json"
NODE_FILE=HOME/"node-id"
STATUS_FILE=HOME/"status.json"
RUNTIME_DIR=HOME/"runtime"
LOG_DIR=HOME/"logs"
RPC_PORT=50052
INTERNAL_API_PORT=18080
USER_API_PORT=8080
RPC_PROXY_BASE=55000
DEFAULT_MODEL="ggml-org/gemma-3-1b-it-GGUF:Q4_K_M"

def ensure_home():
    for p in (HOME,RUNTIME_DIR,LOG_DIR): p.mkdir(parents=True,exist_ok=True)

def node_id():
    ensure_home()
    if NODE_FILE.exists():
        v=NODE_FILE.read_text().strip()
        if v: return v
    v=uuid.uuid4().hex
    NODE_FILE.write_text(v+"\n")
    return v

def total_ram_mb():
    try:
        if platform.system()=="Linux":
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemTotal:"): return int(line.split()[1])//1024
        if platform.system()=="Darwin":
            return int(subprocess.check_output(["sysctl","-n","hw.memsize"],text=True).strip())//(1024*1024)
        if platform.system()=="Windows":
            class M(ctypes.Structure):
                _fields_=[("dwLength",ctypes.c_ulong),("dwMemoryLoad",ctypes.c_ulong),("ullTotalPhys",ctypes.c_ulonglong),("ullAvailPhys",ctypes.c_ulonglong),("ullTotalPageFile",ctypes.c_ulonglong),("ullAvailPageFile",ctypes.c_ulonglong),("ullTotalVirtual",ctypes.c_ulonglong),("ullAvailVirtual",ctypes.c_ulonglong),("ullAvailExtendedVirtual",ctypes.c_ulonglong)]
            m=M(); m.dwLength=ctypes.sizeof(M); ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullTotalPhys//(1024*1024))
    except Exception: pass
    return 0

def threads_for():
    frac=max(.1,min(float(os.environ.get("WQPU_CPU_FRACTION","0.5")),.9))
    return max(1,int((os.cpu_count() or 2)*frac))

def model_name(): return os.environ.get("WQPU_MODEL", DEFAULT_MODEL)

def reserve_mb():
    ram=total_ram_mb()
    return max(128, min(1024, int(ram*0.12) if ram else 256))

def download(url,dest):
    req=urllib.request.Request(url,headers={"User-Agent":f"WQPU/{VERSION}"})
    with urllib.request.urlopen(req,timeout=120) as r, dest.open("wb") as f: shutil.copyfileobj(r,f,1024*1024)

def api_json(url):
    req=urllib.request.Request(url,headers={"User-Agent":f"WQPU/{VERSION}"})
    with urllib.request.urlopen(req,timeout=30) as r: return json.load(r)

def asset_suffix():
    sysn=platform.system(); m=platform.machine().lower(); x64=m in {"x86_64","amd64","x64"}; arm=m in {"arm64","aarch64"}
    if sysn=="Windows" and x64: return "-bin-win-cpu-x64.zip"
    if sysn=="Windows" and arm: return "-bin-win-cpu-arm64.zip"
    if sysn=="Linux" and x64: return "-bin-ubuntu-x64.tar.gz"
    if sysn=="Linux" and arm: return "-bin-ubuntu-arm64.tar.gz"
    if sysn=="Darwin" and arm: return "-bin-macos-arm64.tar.gz"
    if sysn=="Darwin" and x64: return "-bin-macos-x64.tar.gz"
    raise RuntimeError(f"unsupported platform: {sysn} {m}")

def find_binary(root,stem):
    for p in list(root.rglob(stem))+list(root.rglob(stem+".exe")):
        if p.is_file():
            if os.name!="nt": p.chmod(p.stat().st_mode|0o111)
            return p
    raise FileNotFoundError(stem)

def ensure_runtime():
    ensure_home(); meta=RUNTIME_DIR/"current.json"
    if meta.exists():
        try:
            d=json.loads(meta.read_text()); s=Path(d["server"]); r=Path(d["rpc"])
            if s.exists() and r.exists(): return s,r,d.get("tag","cached")
        except Exception: pass
    print("WQPU: downloading llama.cpp...")
    rel=api_json("https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"); tag=rel["tag_name"]; suf=asset_suffix(); a=next((x for x in rel["assets"] if x["name"].endswith(suf)),None)
    if not a: raise RuntimeError(f"no llama.cpp asset for {suf}")
    target=RUNTIME_DIR/tag
    if target.exists(): shutil.rmtree(target)
    target.mkdir(parents=True); arc=RUNTIME_DIR/a["name"]; download(a["browser_download_url"],arc)
    if arc.suffix==".zip":
        with zipfile.ZipFile(arc) as z: z.extractall(target)
    else:
        with tarfile.open(arc,"r:gz") as t: t.extractall(target)
    try: arc.unlink()
    except OSError: pass
    s=find_binary(target,"llama-server"); r=find_binary(target,"ggml-rpc-server")
    meta.write_text(json.dumps({"tag":tag,"server":str(s),"rpc":str(r)},indent=2)+"\n"); return s,r,tag

def parse_token(token):
    if not token.startswith("WQPU1."): raise ValueError("bad WQPU join token")
    raw=token.split(".",1)[1]; raw += "="*((4-len(raw)%4)%4); d=json.loads(base64.urlsafe_b64decode(raw.encode()).decode())
    for k in ("host","port","secret","fingerprint"):
        if k not in d: raise ValueError(f"join token missing {k}")
    d["port"]=int(d["port"]); d["fingerprint"]=d["fingerprint"].lower().replace(":",""); return d

def save_network(token):
    ensure_home(); d=parse_token(token); d["token"]=token; NET_FILE.write_text(json.dumps(d,indent=2)+"\n"); return d

def load_network():
    if not NET_FILE.exists(): raise RuntimeError("not joined; run: wqpu join <TOKEN>")
    return json.loads(NET_FILE.read_text())

async def tls_connect(net):
    ctx=ssl.create_default_context(); ctx.check_hostname=False; ctx.verify_mode=ssl.CERT_NONE
    reader,writer=await asyncio.open_connection(net["host"],int(net["port"]),ssl=ctx,server_hostname=net["host"])
    obj=writer.get_extra_info("ssl_object"); cert=obj.getpeercert(binary_form=True) if obj else None; fp=hashlib.sha256(cert or b"").hexdigest()
    if fp.lower()!=str(net["fingerprint"]).lower():
        writer.close(); await writer.wait_closed(); raise RuntimeError("relay certificate fingerprint mismatch")
    return reader,writer

def hello(net,role,**extra):
    d={"role":role,"secret":net["secret"],"node_id":node_id()}; d.update(extra); return (json.dumps(d,separators=(",",":"))+"\n").encode()

async def relay_stream(net,role,**extra):
    r,w=await tls_connect(net); w.write(hello(net,role,**extra)); await w.drain(); line=await asyncio.wait_for(r.readline(),15)
    if line!=b"WQPU-READY\n":
        try: msg=json.loads(line.decode()); err=msg.get("error",line.decode(errors="ignore"))
        except Exception: err=line.decode(errors="ignore")
        w.close(); raise RuntimeError(f"relay stream failed: {err}")
    return r,w

async def copy_stream(reader,writer):
    try:
        while True:
            b=await reader.read(65536)
            if not b: break
            writer.write(b); await writer.drain()
    except Exception: pass
    try: writer.close(); await writer.wait_closed()
    except Exception: pass

async def bridge(a_r,a_w,b_r,b_w): await asyncio.gather(copy_stream(a_r,b_w),copy_stream(b_r,a_w))

class State:
    def __init__(self): self.nodes=[]; self.coordinator=None; self.changed=asyncio.Event()
    def update(self,msg):
        old=(self.coordinator,tuple(sorted(n.get("node_id","") for n in self.nodes))); self.nodes=list(msg.get("nodes") or []); self.coordinator=msg.get("coordinator"); new=(self.coordinator,tuple(sorted(n.get("node_id","") for n in self.nodes)))
        if new!=old: self.changed.set()
    def workers(self): return [n for n in self.nodes if n.get("node_id")!=self.coordinator]

def proc_kwargs():
    if os.name=="nt": return {"creationflags":getattr(subprocess,"BELOW_NORMAL_PRIORITY_CLASS",0x4000)}
    return {"preexec_fn":lambda: os.nice(7)}

def start_proc(cmd,logname):
    ensure_home(); log=(LOG_DIR/logname).open("a",encoding="utf-8"); return subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,**proc_kwargs())

def stop_proc(p):
    if not p or p.poll() is not None: return
    try: p.terminate(); p.wait(5)
    except Exception:
        try: p.kill()
        except Exception: pass

async def handle_open(net,msg):
    service=msg.get("service"); stream=msg.get("stream"); port=RPC_PORT if service=="rpc" else INTERNAL_API_PORT if service=="api" else None
    if not port: return
    try:
        rr,rw=await relay_stream(net,"accept",stream=stream); lr,lw=await asyncio.open_connection("127.0.0.1",port); await bridge(rr,rw,lr,lw)
    except Exception: pass

async def control_loop(net,state,stop):
    info={"hostname":socket.gethostname(),"ram_mb":total_ram_mb(),"threads":threads_for(),"model":model_name(),"version":VERSION}; delay=1
    while not stop.is_set():
        try:
            r,w=await tls_connect(net); w.write(hello(net,"control",info=info)); await w.drain(); delay=1
            async def pinger():
                while not stop.is_set(): await asyncio.sleep(10); w.write(b'{"type":"ping"}\n'); await w.drain()
            pt=asyncio.create_task(pinger())
            try:
                while not stop.is_set():
                    line=await r.readline()
                    if not line: break
                    msg=json.loads(line.decode())
                    if msg.get("type")=="nodes": state.update(msg)
                    elif msg.get("type")=="open": asyncio.create_task(handle_open(net,msg))
            finally:
                pt.cancel()
                try: w.close(); await w.wait_closed()
                except Exception: pass
        except Exception as exc:
            print(f"WQPU relay reconnect: {exc}"); await asyncio.sleep(delay); delay=min(delay*2,15)

async def dial_proxy(net,target,service,client_r,client_w):
    try:
        rr,rw=await relay_stream(net,"dial",target=target,service=service); await bridge(client_r,client_w,rr,rw)
    except Exception:
        try: client_w.close(); await client_w.wait_closed()
        except Exception: pass

async def local_api_handler(net,state,r,w):
    target=state.coordinator
    if not target: w.close(); await w.wait_closed(); return
    if target==node_id():
        try:
            rr,rw=await asyncio.open_connection("127.0.0.1",INTERNAL_API_PORT); await bridge(r,w,rr,rw)
        except Exception: w.close()
    else: await dial_proxy(net,target,"api",r,w)

async def coordinator_manager(net,state,server_bin,stop):
    server_proc=None; proxy_servers=[]; signature=None
    while not stop.is_set():
        try: await asyncio.wait_for(state.changed.wait(),timeout=2)
        except asyncio.TimeoutError: pass
        state.changed.clear(); me=node_id(); coord=state.coordinator; workers=sorted(n.get("node_id") for n in state.workers() if n.get("node_id")); sig=(coord,tuple(workers),model_name()); dead=server_proc is not None and server_proc.poll() is not None
        if sig==signature and not dead:
            write_status(state, "coordinator" if coord==me else "worker"); continue
        signature=sig; stop_proc(server_proc); server_proc=None
        for s in proxy_servers: s.close()
        for s in proxy_servers:
            try: await s.wait_closed()
            except Exception: pass
        proxy_servers=[]
        if coord!=me:
            write_status(state,"worker"); continue
        endpoints=[]
        for i,target in enumerate(workers):
            port=RPC_PROXY_BASE+i
            async def h(r,w,t=target): await dial_proxy(net,t,"rpc",r,w)
            srv=await asyncio.start_server(h,"127.0.0.1",port); proxy_servers.append(srv); endpoints.append(f"127.0.0.1:{port}")
        cmd=[str(server_bin),"--hf-repo",model_name(),"--threads",str(threads_for()),"--threads-batch",str(threads_for()),"--ctx-size","4096","--host","127.0.0.1","--port",str(INTERNAL_API_PORT),"--prio","-1","--poll","0","--parallel","2","--fit","on","--fit-target",str(reserve_mb())]
        if endpoints: cmd+=["--rpc",",".join(endpoints)]
        server_proc=start_proc(cmd,"server.log"); print(f"Role: COORDINATOR | nodes={len(state.nodes)} | local UI: http://127.0.0.1:{USER_API_PORT}"); write_status(state,"coordinator"); await asyncio.sleep(1)
    stop_proc(server_proc)
    for s in proxy_servers: s.close()

def write_status(state,role):
    ensure_home(); STATUS_FILE.write_text(json.dumps({"version":VERSION,"updated":time.time(),"role":role,"node_id":node_id(),"coordinator":state.coordinator,"nodes":state.nodes,"ui":f"http://127.0.0.1:{USER_API_PORT}"},indent=2)+"\n")

async def run_node():
    net=load_network(); server_bin,rpc_bin,tag=ensure_runtime(); print(f"WQPU {VERSION} | llama.cpp {tag}"); print(f"Node: {socket.gethostname()} | RAM {total_ram_mb()} MiB | threads {threads_for()}/{os.cpu_count() or '?'}")
    rpc=start_proc([str(rpc_bin),"--host","127.0.0.1","--port",str(RPC_PORT),"--threads",str(threads_for()),"--device","CPU","--cache"],"rpc.log")
    stop=asyncio.Event(); state=State(); loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):
        try: loop.add_signal_handler(sig,stop.set)
        except (NotImplementedError,RuntimeError): pass
    api=await asyncio.start_server(lambda r,w: local_api_handler(net,state,r,w),"127.0.0.1",USER_API_PORT)
    tasks=[asyncio.create_task(control_loop(net,state,stop)),asyncio.create_task(coordinator_manager(net,state,server_bin,stop))]
    try: await stop.wait()
    finally:
        for t in tasks: t.cancel()
        api.close(); await api.wait_closed(); stop_proc(rpc)
    return 0

def cmd_status():
    if not STATUS_FILE.exists(): print("WQPU: no status yet"); return 1
    d=json.loads(STATUS_FILE.read_text()); live=time.time()-d.get("updated",0)<20; print(f"WQPU: {'RUNNING' if live else 'STALE'} | role={d.get('role')} | nodes={len(d.get('nodes',[]))}")
    for n in d.get("nodes",[]):
        mark="*" if n.get("node_id")==d.get("coordinator") else "-"; print(f"{mark} {n.get('hostname')} | RAM {n.get('ram_mb')} MiB | threads {n.get('threads')}")
    print("UI:",d.get("ui")); return 0 if live else 1

def cmd_ask(text):
    payload=json.dumps({"model":model_name(),"messages":[{"role":"user","content":text}],"stream":False}).encode(); req=urllib.request.Request(f"http://127.0.0.1:{USER_API_PORT}/v1/chat/completions",data=payload,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=600) as r: d=json.load(r)
    print(d["choices"][0]["message"]["content"]); return 0

def main():
    ap=argparse.ArgumentParser(prog="wqpu"); ap.add_argument("--version",action="version",version=f"WQPU {VERSION}"); sp=ap.add_subparsers(dest="cmd"); j=sp.add_parser("join"); j.add_argument("token"); sp.add_parser("start"); sp.add_parser("status"); a=sp.add_parser("ask"); a.add_argument("text",nargs="+"); args=ap.parse_args()
    try:
        if args.cmd=="join": d=save_network(args.token); print(f"Joined WQPU relay {d['host']}:{d['port']}"); return 0
        if args.cmd=="status": return cmd_status()
        if args.cmd=="ask": return cmd_ask(" ".join(args.text))
        return asyncio.run(run_node())
    except KeyboardInterrupt: return 130
    except Exception as exc: print(f"WQPU error: {exc}",file=sys.stderr); return 1

if __name__=="__main__": raise SystemExit(main())
