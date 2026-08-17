#!/usr/bin/env python3
"""Local browser-wallet connector for WQPU.

The browser wallet can register a node, fund shared WQPU escrow and activate a
bounded local session key. WQPU never receives the wallet private key or seed phrase.
"""

from __future__ import print_function

import json
import os
import secrets
import threading
import time
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


ANNOUNCE_SELECTOR = "581dc5c3"  # announce(string,bytes32,uint64,uint16)


def _html(config):
    cfg = json.dumps(config).replace("</", "<\\/")
    return r'''<!doctype html>
<html><head><meta charset="utf-8"><title>WQPU Wallet</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:system-ui,sans-serif;max-width:720px;margin:48px auto;padding:0 20px;background:#0d1117;color:#e6edf3}
button{font-size:18px;padding:12px 18px;border:0;border-radius:10px;cursor:pointer}
code{word-break:break-all}.muted{color:#8b949e}.ok{color:#3fb950}.err{color:#f85149}
</style></head><body>
<h1>Connect WQPU wallet</h1>
<p>WQPU never receives your seed phrase or wallet private key.</p>
<p class="muted" id="endpointRow">Endpoint: <code id="endpoint"></code></p>
<p class="muted" id="sessionRow" style="display:none"></p>
<button id="connect">Connect wallet</button>
<p id="status" class="muted"></p>
<script>
const CFG = __CFG__;
const statusEl = document.getElementById('status');
document.getElementById('endpoint').textContent = CFG.endpoint;
if(!CFG.registerNode) document.getElementById('endpointRow').style.display='none';
if(CFG.session) {
  const row=document.getElementById('sessionRow'); row.style.display='block';
  row.textContent='Payment session: maximum '+CFG.session.maxAmount+' token-wei; price '+CFG.session.pricePerMillionUnits+' per 1M units; expires '+new Date(Number(CFG.session.validUntil)*1000).toLocaleString();
}
document.getElementById('connect').textContent = CFG.registerNode ? 'Connect wallet and start WQPU' : 'Authorize WQPU payment session';
function hexPad(v){return BigInt(v).toString(16).padStart(64,'0')}
function addrWord(a){return a.toLowerCase().replace(/^0x/,'').padStart(64,'0')}
function utf8Hex(s){return Array.from(new TextEncoder().encode(s)).map(b=>b.toString(16).padStart(2,'0')).join('')}
function encodeAnnounce(endpoint,fingerprint,capacity,loadBps){
  const str=utf8Hex(endpoint), bytes=str.length/2, padded=str.padEnd(Math.ceil(bytes/32)*64,'0');
  return '0x'+CFG.announceSelector+hexPad(128)+fingerprint.replace(/^0x/,'').padStart(64,'0')+hexPad(capacity)+hexPad(loadBps)+hexPad(bytes)+padded;
}
function encodeApprove(market,amount){return '0x'+CFG.session.approveSelector+addrWord(market)+hexPad(amount)}
function encodeDeposit(amount){return '0x'+CFG.session.depositSelector+hexPad(amount)}
function encodeMapRead(selector,account){return '0x'+selector+addrWord(account)}
function encodeActivate(account,sig){
  const s=CFG.session, raw=sig.replace(/^0x/,''), padded=raw.padEnd(Math.ceil((raw.length/2)/32)*64,'0');
  return '0x'+s.activateSelector+addrWord(account)+addrWord(s.sessionKey)+s.sessionId.replace(/^0x/,'')
    +hexPad(s.maxAmount)+hexPad(s.pricePerMillionUnits)+hexPad(s.validUntil)
    +hexPad(224)+hexPad(raw.length/2)+padded;
}
async function ensureNetwork(){
  if(!CFG.chainId) return;
  let current=await ethereum.request({method:'eth_chainId'});
  if(current.toLowerCase()===CFG.chainId.toLowerCase()) return;
  try { await ethereum.request({method:'wallet_switchEthereumChain',params:[{chainId:CFG.chainId}]}); }
  catch(e){
    const unknown=e&&(e.code===4902||(e.data&&e.data.originalError&&e.data.originalError.code===4902));
    if(!unknown||!CFG.rpcUrl||!CFG.chainName||!CFG.nativeSymbol) throw e;
    await ethereum.request({method:'wallet_addEthereumChain',params:[{
      chainId:CFG.chainId,chainName:CFG.chainName,rpcUrls:[CFG.rpcUrl],
      nativeCurrency:{name:CFG.nativeSymbol,symbol:CFG.nativeSymbol,decimals:18}
    }]});
  }
  current=await ethereum.request({method:'eth_chainId'});
  if(current.toLowerCase()!==CFG.chainId.toLowerCase()) throw new Error('Wallet did not switch to WQPU network.');
}
function sessionTypedData(account){
  const s=CFG.session;
  return {
    types:{
      EIP712Domain:[{name:'name',type:'string'},{name:'version',type:'string'},{name:'chainId',type:'uint256'},{name:'verifyingContract',type:'address'}],
      SpendAuthorization:[
        {name:'requester',type:'address'},{name:'sessionKey',type:'address'},
        {name:'sessionId',type:'bytes32'},{name:'maxAmount',type:'uint128'},
        {name:'pricePerMillionUnits',type:'uint128'},{name:'validUntil',type:'uint64'}
      ]
    },primaryType:'SpendAuthorization',
    domain:{name:'WQPU Compute Market',version:'1',chainId:Number(BigInt(CFG.chainId)),verifyingContract:s.market},
    message:{requester:account,sessionKey:s.sessionKey,sessionId:s.sessionId,maxAmount:String(s.maxAmount),pricePerMillionUnits:String(s.pricePerMillionUnits),validUntil:String(s.validUntil)}
  };
}
async function readUint(to,data){
  const value=await ethereum.request({method:'eth_call',params:[{to,data},'latest']});
  return BigInt(value||'0x0');
}
async function sendTx(account,to,data,label){
  statusEl.textContent=label;
  return await ethereum.request({method:'eth_sendTransaction',params:[{from:account,to,data}]});
}
async function post(data){
  const r=await fetch('/done',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)});
  if(!r.ok) throw new Error('WQPU connector rejected wallet response.');
}
document.getElementById('connect').onclick=async()=>{
 try{
   if(!window.ethereum) throw new Error('No injected EVM wallet found. Use MetaMask, Rabby or another EVM wallet.');
   statusEl.textContent='Requesting wallet permission…';
   const accounts=await ethereum.request({method:'eth_requestAccounts'}), account=accounts[0];
   await ensureNetwork();
   const chainId=await ethereum.request({method:'eth_chainId'});
   let txHash=null,approveHash=null,depositHash=null,activationHash=null,sessionAuthorizationSignature=null;
   if(CFG.registerNode){
     txHash=await sendTx(account,CFG.registry,encodeAnnounce(CFG.endpoint,CFG.fingerprint,CFG.capacity,CFG.loadBps),'Confirm node registration…');
   }
   if(CFG.session){
     const s=CFG.session;
     const balance=await readUint(s.market,encodeMapRead(s.escrowBalanceSelector,account));
     const reserved=await readUint(s.market,encodeMapRead(s.reservedEscrowSelector,account));
     const free=balance>reserved?balance-reserved:0n, target=BigInt(s.maxAmount);
     const shortfall=target>free?target-free:0n;
     if(shortfall>0n){
       if(!s.token) throw new Error('WQPU token is not configured for escrow funding.');
       approveHash=await sendTx(account,s.token,encodeApprove(s.market,shortfall),'Confirm WQPU token approval…');
       depositHash=await sendTx(account,s.market,encodeDeposit(shortfall),'Confirm WQPU escrow deposit…');
     }
     statusEl.textContent='Approve the bounded local payment session…';
     sessionAuthorizationSignature=await ethereum.request({method:'eth_signTypedData_v4',params:[account,JSON.stringify(sessionTypedData(account))]});
     activationHash=await sendTx(account,s.market,encodeActivate(account,sessionAuthorizationSignature),'Confirm payment-session activation…');
   }
   await post({wallet:account,chainId,txHash,approveHash,depositHash,activationHash,sessionAuthorizationSignature,challenge:CFG.challenge});
   statusEl.className='ok';statusEl.textContent='WQPU is connected. You can return to the terminal.';
 }catch(e){statusEl.className='err';statusEl.textContent=e&&e.message?e.message:String(e)}
};
</script></body></html>'''.replace("__CFG__", cfg)


def _network_token():
    env = os.environ.get("WQPU_TOKEN", "").strip()
    if env:
        return env.lower()
    try:
        path = Path(__file__).resolve().with_name("network-config.json")
        data = json.loads(path.read_text())
        token = str((data.get("public") or {}).get("token") or "").strip()
        return token.lower()
    except Exception:
        return ""


def _rpc_selector(rpc_url, signature):
    if not rpc_url:
        raise RuntimeError("WQPU RPC URL is required for wallet onboarding")
    raw = "0x" + signature.encode("utf-8").hex()
    payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"web3_sha3","params":[raw]}).encode("utf-8")
    request = urllib.request.Request(rpc_url, data=payload, headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    result = str(body.get("result") or "")
    if not result.startswith("0x") or len(result) != 66:
        raise RuntimeError("WQPU RPC could not derive ABI selector")
    return result[2:10]


class WalletConnector(object):
    def __init__(self, registry, endpoint, fingerprint, capacity, load_bps=0,
                 chain_id=None, rpc_url=None, chain_name=None, native_symbol=None,
                 register_node=True, session=None):
        self.registry=str(registry).lower(); self.endpoint=str(endpoint)
        self.fingerprint=str(fingerprint).lower(); self.capacity=int(capacity)
        self.load_bps=int(load_bps); self.chain_id=chain_id; self.rpc_url=rpc_url
        self.chain_name=chain_name; self.native_symbol=native_symbol
        self.register_node=bool(register_node); self.session=dict(session) if session else None
        self.challenge=secrets.token_hex(24); self.result=None; self.event=threading.Event()

    def _prepare_session(self):
        if not self.session:
            return None
        session = dict(self.session)
        session.setdefault("token", _network_token())
        selectors = {
            "approveSelector": "approve(address,uint256)",
            "depositSelector": "deposit(uint256)",
            "activateSelector": "activateSession((address,address,bytes32,uint128,uint128,uint64),bytes)",
            "escrowBalanceSelector": "escrowBalance(address)",
            "reservedEscrowSelector": "reservedEscrow(address)",
        }
        for key, signature in selectors.items():
            session[key] = _rpc_selector(self.rpc_url, signature)
        return session

    def connect(self, timeout=300, open_browser=True):
        connector=self
        self.session = self._prepare_session()
        config={"registry":self.registry,"endpoint":self.endpoint,"fingerprint":self.fingerprint,
                "capacity":self.capacity,"loadBps":self.load_bps,"chainId":self.chain_id,
                "rpcUrl":self.rpc_url,"chainName":self.chain_name,"nativeSymbol":self.native_symbol,
                "registerNode":self.register_node,"session":self.session,"challenge":self.challenge,
                "announceSelector":ANNOUNCE_SELECTOR}
        page=_html(config).encode("utf-8")
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,fmt,*args): return
            def do_GET(self):
                if self.path!="/": self.send_error(404); return
                self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
                self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(page)))
                self.end_headers(); self.wfile.write(page)
            def do_POST(self):
                if self.path!="/done": self.send_error(404); return
                try:
                    body=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))).decode("utf-8"))
                    if body.get("challenge")!=connector.challenge: raise ValueError("challenge mismatch")
                    wallet=str(body.get("wallet","")).lower()
                    if not wallet.startswith("0x") or len(wallet)!=42: raise ValueError("bad wallet")
                    def tx(name,required=False):
                        value=body.get(name)
                        if not value and not required: return None
                        value=str(value or "")
                        if not value.startswith("0x") or len(value)!=66: raise ValueError("bad transaction hash")
                        return value
                    session_sig=body.get("sessionAuthorizationSignature")
                    if connector.session:
                        session_sig=str(session_sig or "")
                        if not session_sig.startswith("0x") or len(session_sig)!=132: raise ValueError("bad session authorization signature")
                    else: session_sig=None
                    connector.result={"wallet":wallet,"chain_id":body.get("chainId"),
                        "tx_hash":tx("txHash",connector.register_node),"approve_tx":tx("approveHash"),
                        "deposit_tx":tx("depositHash"),"activation_tx":tx("activationHash",bool(connector.session)),
                        "session_authorization_signature":session_sig,"connected_at":int(time.time())}
                    connector.event.set(); self.send_response(204); self.end_headers()
                except Exception as exc:
                    raw=json.dumps({"error":str(exc)}).encode("utf-8")
                    self.send_response(400); self.send_header("Content-Type","application/json")
                    self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
        server=HTTPServer(("127.0.0.1",0),Handler)
        thread=threading.Thread(target=server.serve_forever); thread.daemon=True; thread.start()
        url="http://127.0.0.1:{}/".format(server.server_port)
        try:
            if open_browser: webbrowser.open(url)
            print("WQPU wallet connector: {}".format(url))
            if not self.event.wait(float(timeout)): raise RuntimeError("wallet connection timed out")
            return self.result
        finally:
            server.shutdown(); server.server_close()


def connect_wallet(registry, endpoint, fingerprint, capacity, load_bps=0, chain_id=None,
                   timeout=300, rpc_url=None, chain_name=None, native_symbol=None,
                   register_node=True, session=None):
    return WalletConnector(registry,endpoint,fingerprint,capacity,load_bps,chain_id,rpc_url,
                           chain_name,native_symbol,register_node,session).connect(timeout=timeout)


if __name__ == "__main__":
    registry=os.environ.get("WQPU_REGISTRY",""); endpoint=os.environ.get("WQPU_PUBLIC_ENDPOINT","")
    fingerprint=os.environ.get("WQPU_TLS_FINGERPRINT",""); capacity=int(os.environ.get("WQPU_CAPACITY","1"))
    if not registry or not endpoint or not fingerprint:
        raise SystemExit("set WQPU_REGISTRY, WQPU_PUBLIC_ENDPOINT and WQPU_TLS_FINGERPRINT")
    print(json.dumps(connect_wallet(registry,endpoint,fingerprint,capacity,rpc_url=os.environ.get("WQPU_RPC_URL"),
        chain_name=os.environ.get("WQPU_CHAIN_NAME"),native_symbol=os.environ.get("WQPU_NATIVE_SYMBOL")),indent=2))
