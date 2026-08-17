#!/usr/bin/env python3
"""Local browser-wallet connector for WQPU.

The browser wallet registers a node and signs bounded payment permissions. Escrow
funding uses EIP-2612 permit and a relayer, so WQPU never receives a wallet private
key and the user does not need separate approve/deposit/activation transactions.
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
HOME = Path(os.environ.get("WQPU_HOME", str(Path.home() / ".wqpu"))).expanduser()
FUNDING_FILE = HOME / "funding-permit.json"


def load_funding_permit():
    try:
        value = json.loads(FUNDING_FILE.read_text())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def save_funding_permit(value):
    HOME.mkdir(parents=True, exist_ok=True)
    FUNDING_FILE.write_text(json.dumps(value, indent=2) + "\n")
    try:
        FUNDING_FILE.chmod(0o600)
    except Exception:
        pass


def clear_funding_permit():
    try:
        FUNDING_FILE.unlink()
    except OSError:
        pass


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
const CFG=__CFG__, statusEl=document.getElementById('status');
document.getElementById('endpoint').textContent=CFG.endpoint;
if(!CFG.registerNode) document.getElementById('endpointRow').style.display='none';
if(CFG.session){
 const row=document.getElementById('sessionRow');row.style.display='block';
 row.textContent='Payment session: maximum '+CFG.session.maxAmount+' token-wei; price '+CFG.session.pricePerMillionUnits+' per 1M units; expires '+new Date(Number(CFG.session.validUntil)*1000).toLocaleString();
}
document.getElementById('connect').textContent=CFG.registerNode?'Connect wallet and start WQPU':'Authorize WQPU payment session';
function hexPad(v){return BigInt(v).toString(16).padStart(64,'0')}
function addrWord(a){return a.toLowerCase().replace(/^0x/,'').padStart(64,'0')}
function utf8Hex(s){return Array.from(new TextEncoder().encode(s)).map(b=>b.toString(16).padStart(2,'0')).join('')}
function encodeAnnounce(endpoint,fingerprint,capacity,loadBps){
 const str=utf8Hex(endpoint),bytes=str.length/2,padded=str.padEnd(Math.ceil(bytes/32)*64,'0');
 return '0x'+CFG.announceSelector+hexPad(128)+fingerprint.replace(/^0x/,'').padStart(64,'0')+hexPad(capacity)+hexPad(loadBps)+hexPad(bytes)+padded;
}
function encodeMapRead(selector,account){return '0x'+selector+addrWord(account)}
async function ensureNetwork(){
 if(!CFG.chainId)return;
 let current=await ethereum.request({method:'eth_chainId'});
 if(current.toLowerCase()===CFG.chainId.toLowerCase())return;
 try{await ethereum.request({method:'wallet_switchEthereumChain',params:[{chainId:CFG.chainId}]});}
 catch(e){
  const unknown=e&&(e.code===4902||(e.data&&e.data.originalError&&e.data.originalError.code===4902));
  if(!unknown||!CFG.rpcUrl||!CFG.chainName||!CFG.nativeSymbol)throw e;
  await ethereum.request({method:'wallet_addEthereumChain',params:[{chainId:CFG.chainId,chainName:CFG.chainName,rpcUrls:[CFG.rpcUrl],nativeCurrency:{name:CFG.nativeSymbol,symbol:CFG.nativeSymbol,decimals:18}}]});
 }
 current=await ethereum.request({method:'eth_chainId'});
 if(current.toLowerCase()!==CFG.chainId.toLowerCase())throw new Error('Wallet did not switch to WQPU network.');
}
function sessionTypedData(account){
 const s=CFG.session;
 return {types:{EIP712Domain:[{name:'name',type:'string'},{name:'version',type:'string'},{name:'chainId',type:'uint256'},{name:'verifyingContract',type:'address'}],SpendAuthorization:[{name:'requester',type:'address'},{name:'sessionKey',type:'address'},{name:'sessionId',type:'bytes32'},{name:'maxAmount',type:'uint128'},{name:'pricePerMillionUnits',type:'uint128'},{name:'validUntil',type:'uint64'}]},primaryType:'SpendAuthorization',domain:{name:'WQPU Compute Market',version:'1',chainId:Number(BigInt(CFG.chainId)),verifyingContract:s.market},message:{requester:account,sessionKey:s.sessionKey,sessionId:s.sessionId,maxAmount:String(s.maxAmount),pricePerMillionUnits:String(s.pricePerMillionUnits),validUntil:String(s.validUntil)}};
}
function permitTypedData(account,amount,nonce){
 const s=CFG.session;
 return {types:{EIP712Domain:[{name:'name',type:'string'},{name:'version',type:'string'},{name:'chainId',type:'uint256'},{name:'verifyingContract',type:'address'}],Permit:[{name:'owner',type:'address'},{name:'spender',type:'address'},{name:'value',type:'uint256'},{name:'nonce',type:'uint256'},{name:'deadline',type:'uint256'}]},primaryType:'Permit',domain:{name:'WQPU',version:'1',chainId:Number(BigInt(CFG.chainId)),verifyingContract:s.token},message:{owner:account,spender:s.market,value:String(amount),nonce:String(nonce),deadline:String(s.validUntil)}};
}
async function readUint(to,data){
 const value=await ethereum.request({method:'eth_call',params:[{to,data},'latest']});return BigInt(value||'0x0');
}
async function post(data){
 const r=await fetch('/done',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)});
 if(!r.ok)throw new Error('WQPU connector rejected wallet response.');
}
document.getElementById('connect').onclick=async()=>{
 try{
  if(!window.ethereum)throw new Error('No injected EVM wallet found. Use MetaMask, Rabby or another EVM wallet.');
  statusEl.textContent='Requesting wallet permission…';
  const accounts=await ethereum.request({method:'eth_requestAccounts'}),account=accounts[0];
  await ensureNetwork();
  const chainId=await ethereum.request({method:'eth_chainId'});
  let txHash=null,sessionAuthorizationSignature=null,fundingPermitSignature=null,fundingAmount='0',fundingDeadline='0';
  if(CFG.registerNode){
   statusEl.textContent='Confirm node registration…';
   txHash=await ethereum.request({method:'eth_sendTransaction',params:[{from:account,to:CFG.registry,data:encodeAnnounce(CFG.endpoint,CFG.fingerprint,CFG.capacity,CFG.loadBps)}]});
  }
  if(CFG.session){
   const s=CFG.session;
   const balance=await readUint(s.market,encodeMapRead(s.escrowBalanceSelector,account));
   const reserved=await readUint(s.market,encodeMapRead(s.reservedEscrowSelector,account));
   const free=balance>reserved?balance-reserved:0n,target=BigInt(s.maxAmount),shortfall=target>free?target-free:0n;
   if(shortfall>0n&&s.token){
    const nonce=await readUint(s.token,encodeMapRead(s.tokenNoncesSelector,account));
    statusEl.textContent='Sign permission to fund WQPU escrow…';
    fundingPermitSignature=await ethereum.request({method:'eth_signTypedData_v4',params:[account,JSON.stringify(permitTypedData(account,shortfall,nonce))]});
    fundingAmount=String(shortfall);fundingDeadline=String(s.validUntil);
   }
   statusEl.textContent='Sign the bounded WQPU payment session…';
   sessionAuthorizationSignature=await ethereum.request({method:'eth_signTypedData_v4',params:[account,JSON.stringify(sessionTypedData(account))]});
  }
  await post({wallet:account,chainId,txHash,sessionAuthorizationSignature,fundingPermitSignature,fundingAmount,fundingDeadline,challenge:CFG.challenge});
  statusEl.className='ok';statusEl.textContent='WQPU is connected. You can return to the terminal.';
 }catch(e){statusEl.className='err';statusEl.textContent=e&&e.message?e.message:String(e)}
};
</script></body></html>'''.replace("__CFG__",cfg)


def _network_token():
    env=os.environ.get("WQPU_TOKEN","").strip()
    if env:return env.lower()
    try:
        path=Path(__file__).resolve().with_name("network-config.json")
        data=json.loads(path.read_text())
        return str((data.get("public") or {}).get("token") or "").strip().lower()
    except Exception:return ""


def _rpc_selector(rpc_url,signature):
    if not rpc_url:raise RuntimeError("WQPU RPC URL is required for wallet onboarding")
    raw="0x"+signature.encode("utf-8").hex()
    payload=json.dumps({"jsonrpc":"2.0","id":1,"method":"web3_sha3","params":[raw]}).encode("utf-8")
    request=urllib.request.Request(rpc_url,data=payload,headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(request,timeout=10) as response:body=json.load(response)
    result=str(body.get("result") or "")
    if not result.startswith("0x") or len(result)!=66:raise RuntimeError("WQPU RPC could not derive ABI selector")
    return result[2:10]


class WalletConnector(object):
    def __init__(self,registry,endpoint,fingerprint,capacity,load_bps=0,chain_id=None,rpc_url=None,chain_name=None,native_symbol=None,register_node=True,session=None):
        self.registry=str(registry).lower();self.endpoint=str(endpoint);self.fingerprint=str(fingerprint).lower();self.capacity=int(capacity)
        self.load_bps=int(load_bps);self.chain_id=chain_id;self.rpc_url=rpc_url;self.chain_name=chain_name;self.native_symbol=native_symbol
        self.register_node=bool(register_node);self.session=dict(session) if session else None;self.challenge=secrets.token_hex(24);self.result=None;self.event=threading.Event()

    def _prepare_session(self):
        if not self.session:return None
        session=dict(self.session);session.setdefault("token",_network_token())
        for key,signature in {
            "escrowBalanceSelector":"escrowBalance(address)",
            "reservedEscrowSelector":"reservedEscrow(address)",
            "tokenNoncesSelector":"nonces(address)",
        }.items():session[key]=_rpc_selector(self.rpc_url,signature)
        return session

    def connect(self,timeout=300,open_browser=True):
        connector=self;self.session=self._prepare_session()
        config={"registry":self.registry,"endpoint":self.endpoint,"fingerprint":self.fingerprint,"capacity":self.capacity,"loadBps":self.load_bps,"chainId":self.chain_id,"rpcUrl":self.rpc_url,"chainName":self.chain_name,"nativeSymbol":self.native_symbol,"registerNode":self.register_node,"session":self.session,"challenge":self.challenge,"announceSelector":ANNOUNCE_SELECTOR}
        page=_html(config).encode("utf-8")
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,fmt,*args):return
            def do_GET(self):
                if self.path!="/":self.send_error(404);return
                self.send_response(200);self.send_header("Content-Type","text/html; charset=utf-8");self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(page)));self.end_headers();self.wfile.write(page)
            def do_POST(self):
                if self.path!="/done":self.send_error(404);return
                try:
                    body=json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))).decode("utf-8"))
                    if body.get("challenge")!=connector.challenge:raise ValueError("challenge mismatch")
                    wallet=str(body.get("wallet","")).lower()
                    if not wallet.startswith("0x") or len(wallet)!=42:raise ValueError("bad wallet")
                    tx_hash=body.get("txHash")
                    if connector.register_node:
                        tx_hash=str(tx_hash or "")
                        if not tx_hash.startswith("0x") or len(tx_hash)!=66:raise ValueError("bad transaction hash")
                    else:tx_hash=None
                    session_sig=body.get("sessionAuthorizationSignature")
                    if connector.session:
                        session_sig=str(session_sig or "")
                        if not session_sig.startswith("0x") or len(session_sig)!=132:raise ValueError("bad session authorization signature")
                    else:session_sig=None

                    funding=None
                    funding_sig=str(body.get("fundingPermitSignature") or "")
                    funding_amount=int(body.get("fundingAmount") or 0)
                    funding_deadline=int(body.get("fundingDeadline") or 0)
                    if funding_amount>0:
                        if not connector.session or not connector.session.get("token"):raise ValueError("funding token missing")
                        if not funding_sig.startswith("0x") or len(funding_sig)!=132:raise ValueError("bad funding permit signature")
                        funding={"market":str(connector.session["market"]).lower(),"token":str(connector.session["token"]).lower(),"requester":wallet,"amount":funding_amount,"deadline":funding_deadline,"permit_signature":funding_sig,"chain_id":str(body.get("chainId") or "").lower(),"created_at":int(time.time())}
                        save_funding_permit(funding)
                    elif connector.session:
                        clear_funding_permit()

                    connector.result={"wallet":wallet,"chain_id":body.get("chainId"),"tx_hash":tx_hash,"session_authorization_signature":session_sig,"funding_permit":funding,"connected_at":int(time.time())}
                    connector.event.set();self.send_response(204);self.end_headers()
                except Exception as exc:
                    raw=json.dumps({"error":str(exc)}).encode("utf-8");self.send_response(400);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(raw)));self.end_headers();self.wfile.write(raw)
        server=HTTPServer(("127.0.0.1",0),Handler);thread=threading.Thread(target=server.serve_forever);thread.daemon=True;thread.start();url="http://127.0.0.1:{}/".format(server.server_port)
        try:
            if open_browser:webbrowser.open(url)
            print("WQPU wallet connector: {}".format(url))
            if not self.event.wait(float(timeout)):raise RuntimeError("wallet connection timed out")
            return self.result
        finally:server.shutdown();server.server_close()


def connect_wallet(registry,endpoint,fingerprint,capacity,load_bps=0,chain_id=None,timeout=300,rpc_url=None,chain_name=None,native_symbol=None,register_node=True,session=None):
    return WalletConnector(registry,endpoint,fingerprint,capacity,load_bps,chain_id,rpc_url,chain_name,native_symbol,register_node,session).connect(timeout=timeout)


if __name__=="__main__":
    registry=os.environ.get("WQPU_REGISTRY","");endpoint=os.environ.get("WQPU_PUBLIC_ENDPOINT","");fingerprint=os.environ.get("WQPU_TLS_FINGERPRINT","");capacity=int(os.environ.get("WQPU_CAPACITY","1"))
    if not registry or not endpoint or not fingerprint:raise SystemExit("set WQPU_REGISTRY, WQPU_PUBLIC_ENDPOINT and WQPU_TLS_FINGERPRINT")
    print(json.dumps(connect_wallet(registry,endpoint,fingerprint,capacity,rpc_url=os.environ.get("WQPU_RPC_URL"),chain_name=os.environ.get("WQPU_CHAIN_NAME"),native_symbol=os.environ.get("WQPU_NATIVE_SYMBOL")),indent=2))
