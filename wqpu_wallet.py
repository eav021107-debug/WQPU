#!/usr/bin/env python3
"""Local browser-wallet connector for WQPU.

WQPU never asks for or stores a seed phrase/private key. The browser wallet submits
the registry transaction itself, then returns only the public wallet address/tx hash.
"""

from __future__ import print_function

import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer


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
<p>WQPU never receives your seed phrase or private key. Your wallet submits the node registration itself.</p>
<p class="muted">Endpoint: <code id="endpoint"></code></p>
<button id="connect">Connect wallet and register node</button>
<p id="status" class="muted"></p>
<script>
const CFG = __CFG__;
const statusEl = document.getElementById('status');
document.getElementById('endpoint').textContent = CFG.endpoint;
function hexPad(v){return BigInt(v).toString(16).padStart(64,'0')}
function utf8Hex(s){return Array.from(new TextEncoder().encode(s)).map(b=>b.toString(16).padStart(2,'0')).join('')}
function encodeAnnounce(endpoint,fingerprint,capacity,loadBps){
  const str = utf8Hex(endpoint); const bytes = str.length/2;
  const padded = str.padEnd(Math.ceil(bytes/32)*64,'0');
  const fp = fingerprint.replace(/^0x/,'').padStart(64,'0');
  return '0x' + CFG.announceSelector + hexPad(128) + fp + hexPad(capacity) + hexPad(loadBps) + hexPad(bytes) + padded;
}
async function ensureNetwork(){
  if(!CFG.chainId) return;
  let current = await ethereum.request({method:'eth_chainId'});
  if(current.toLowerCase() === CFG.chainId.toLowerCase()) return;
  try {
    await ethereum.request({method:'wallet_switchEthereumChain',params:[{chainId:CFG.chainId}]});
  } catch(e) {
    const unknown = e && (e.code === 4902 || (e.data && e.data.originalError && e.data.originalError.code === 4902));
    if(!unknown || !CFG.rpcUrl || !CFG.chainName || !CFG.nativeSymbol) throw e;
    await ethereum.request({method:'wallet_addEthereumChain',params:[{
      chainId:CFG.chainId,
      chainName:CFG.chainName,
      rpcUrls:[CFG.rpcUrl],
      nativeCurrency:{name:CFG.nativeSymbol,symbol:CFG.nativeSymbol,decimals:18}
    }]});
  }
  current = await ethereum.request({method:'eth_chainId'});
  if(current.toLowerCase() !== CFG.chainId.toLowerCase()) throw new Error('Wallet did not switch to WQPU network.');
}
async function post(data){
  await fetch('/done',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)});
}
document.getElementById('connect').onclick = async () => {
 try {
   if(!window.ethereum) throw new Error('No injected EVM wallet found. Open this page in a browser with MetaMask/Rabby or another EVM wallet.');
   statusEl.textContent='Requesting wallet permission…';
   const accounts = await ethereum.request({method:'eth_requestAccounts'});
   const account = accounts[0];
   statusEl.textContent='Switching to the WQPU network…';
   await ensureNetwork();
   const chainId = await ethereum.request({method:'eth_chainId'});
   statusEl.textContent='Confirm node registration transaction in your wallet…';
   const data = encodeAnnounce(CFG.endpoint,CFG.fingerprint,CFG.capacity,CFG.loadBps);
   const txHash = await ethereum.request({method:'eth_sendTransaction',params:[{from:account,to:CFG.registry,data}]});
   await post({wallet:account,chainId,txHash,challenge:CFG.challenge});
   statusEl.className='ok'; statusEl.textContent='Connected. You can return to the terminal.';
 } catch(e) {
   statusEl.className='err'; statusEl.textContent=e && e.message ? e.message : String(e);
 }
};
</script></body></html>'''.replace("__CFG__", cfg)


class WalletConnector(object):
    def __init__(
        self,
        registry,
        endpoint,
        fingerprint,
        capacity,
        load_bps=0,
        chain_id=None,
        rpc_url=None,
        chain_name=None,
        native_symbol=None,
    ):
        self.registry = str(registry).lower()
        self.endpoint = str(endpoint)
        self.fingerprint = str(fingerprint).lower()
        self.capacity = int(capacity)
        self.load_bps = int(load_bps)
        self.chain_id = chain_id
        self.rpc_url = rpc_url
        self.chain_name = chain_name
        self.native_symbol = native_symbol
        self.challenge = secrets.token_hex(24)
        self.result = None
        self.event = threading.Event()

    def connect(self, timeout=180, open_browser=True):
        connector = self
        config = {
            "registry": self.registry,
            "endpoint": self.endpoint,
            "fingerprint": self.fingerprint,
            "capacity": self.capacity,
            "loadBps": self.load_bps,
            "chainId": self.chain_id,
            "rpcUrl": self.rpc_url,
            "chainName": self.chain_name,
            "nativeSymbol": self.native_symbol,
            "challenge": self.challenge,
            "announceSelector": ANNOUNCE_SELECTOR,
        }
        page = _html(config).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def do_GET(self):
                if self.path != "/":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def do_POST(self):
                if self.path != "/done":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                    if body.get("challenge") != connector.challenge:
                        raise ValueError("challenge mismatch")
                    wallet = str(body.get("wallet", "")).lower()
                    tx_hash = str(body.get("txHash", ""))
                    if not wallet.startswith("0x") or len(wallet) != 42:
                        raise ValueError("bad wallet")
                    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
                        raise ValueError("bad transaction hash")
                    connector.result = {
                        "wallet": wallet,
                        "chain_id": body.get("chainId"),
                        "tx_hash": tx_hash,
                        "connected_at": int(time.time()),
                    }
                    connector.event.set()
                    self.send_response(204)
                    self.end_headers()
                except Exception as exc:
                    raw = json.dumps({"error": str(exc)}).encode("utf-8")
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        url = "http://127.0.0.1:{}/".format(server.server_port)
        try:
            if open_browser:
                webbrowser.open(url)
            print("WQPU wallet connector: {}".format(url))
            if not self.event.wait(float(timeout)):
                raise RuntimeError("wallet connection timed out")
            return self.result
        finally:
            server.shutdown()
            server.server_close()


def connect_wallet(
    registry,
    endpoint,
    fingerprint,
    capacity,
    load_bps=0,
    chain_id=None,
    timeout=180,
    rpc_url=None,
    chain_name=None,
    native_symbol=None,
):
    return WalletConnector(
        registry=registry,
        endpoint=endpoint,
        fingerprint=fingerprint,
        capacity=capacity,
        load_bps=load_bps,
        chain_id=chain_id,
        rpc_url=rpc_url,
        chain_name=chain_name,
        native_symbol=native_symbol,
    ).connect(timeout=timeout)


if __name__ == "__main__":
    registry = os.environ.get("WQPU_REGISTRY", "")
    endpoint = os.environ.get("WQPU_PUBLIC_ENDPOINT", "")
    fingerprint = os.environ.get("WQPU_TLS_FINGERPRINT", "")
    capacity = int(os.environ.get("WQPU_CAPACITY", "1"))
    if not registry or not endpoint or not fingerprint:
        raise SystemExit("set WQPU_REGISTRY, WQPU_PUBLIC_ENDPOINT and WQPU_TLS_FINGERPRINT")
    print(json.dumps(connect_wallet(
        registry,
        endpoint,
        fingerprint,
        capacity,
        rpc_url=os.environ.get("WQPU_RPC_URL"),
        chain_name=os.environ.get("WQPU_CHAIN_NAME"),
        native_symbol=os.environ.get("WQPU_NATIVE_SYMBOL"),
    ), indent=2))
