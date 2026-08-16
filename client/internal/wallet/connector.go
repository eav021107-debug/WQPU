package wallet

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

type ConnectConfig struct {
	WQPUChainID     string
	EVMChainID      uint64
	RPCURL          string
	SessionPubkey   string
	IssuedHeight    uint64
	ExpiresHeight   uint64
	MaxSpendUnits   uint64
	MaxJobUnits     uint64
	RevocationNonce uint64
	Permissions     uint64
}

type ConnectResult struct {
	Wallet    string
	Signature string
}

type Connector struct {
	URL     string
	Results <-chan ConnectResult
	server  *http.Server
	listener net.Listener
}

func (c *Connector) Close(ctx context.Context) error {
	if c == nil || c.server == nil {
		return nil
	}
	return c.server.Shutdown(ctx)
}

func randomToken() (string, error) {
	buf := make([]byte, 24)
	if _, err := rand.Read(buf); err != nil {
		return "", err
	}
	return hex.EncodeToString(buf), nil
}

func (cfg ConnectConfig) request(wallet string) SessionRequest {
	return SessionRequest{
		WQPUChainID:     cfg.WQPUChainID,
		EVMChainID:      cfg.EVMChainID,
		Wallet:          wallet,
		SessionPubkey:   cfg.SessionPubkey,
		IssuedHeight:    cfg.IssuedHeight,
		ExpiresHeight:   cfg.ExpiresHeight,
		MaxSpendUnits:   cfg.MaxSpendUnits,
		MaxJobUnits:     cfg.MaxJobUnits,
		RevocationNonce: cfg.RevocationNonce,
		Permissions:     cfg.Permissions,
	}
}

func (cfg ConnectConfig) validate() error {
	if cfg.RPCURL == "" {
		return errors.New("local WQPU chain RPC URL is required")
	}
	return cfg.request("0x0000000000000000000000000000000000000001").Validate()
}

func StartConnector(ctx context.Context, cfg ConnectConfig) (*Connector, error) {
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	token, err := randomToken()
	if err != nil {
		return nil, err
	}
	ln, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, err
	}

	origin := "http://" + ln.Addr().String()
	base := "/" + token + "/"
	results := make(chan ConnectResult, 1)
	var mu sync.Mutex
	preparedWallet := ""
	completed := false

	secureHeaders := func(w http.ResponseWriter) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
		w.Header().Set("Referrer-Policy", "no-referrer")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
	}
	checkHost := func(r *http.Request) bool {
		return r.Host == ln.Addr().String()
	}
	checkPost := func(w http.ResponseWriter, r *http.Request) bool {
		secureHeaders(w)
		if r.Method != http.MethodPost || !checkHost(r) || r.Header.Get("Origin") != origin {
			http.Error(w, "forbidden", http.StatusForbidden)
			return false
		}
		return true
	}

	mux := http.NewServeMux()
	mux.HandleFunc(base, func(w http.ResponseWriter, r *http.Request) {
		secureHeaders(w)
		if r.Method != http.MethodGet || !checkHost(r) {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		data := struct {
			ChainHex string
			RPCURL   string
		}{
			ChainHex: fmt.Sprintf("0x%x", cfg.EVMChainID),
			RPCURL:   cfg.RPCURL,
		}
		if err := connectorPage.Execute(w, data); err != nil {
			http.Error(w, "page error", http.StatusInternalServerError)
		}
	})

	mux.HandleFunc(base+"prepare", func(w http.ResponseWriter, r *http.Request) {
		if !checkPost(w, r) {
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 4096)
		var body struct {
			Wallet string `json:"wallet"`
		}
		dec := json.NewDecoder(r.Body)
		dec.DisallowUnknownFields()
		if err := dec.Decode(&body); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		typed, err := BuildSessionTypedData(cfg.request(body.Wallet))
		if err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		mu.Lock()
		if completed || (preparedWallet != "" && !strings.EqualFold(preparedWallet, body.Wallet)) {
			mu.Unlock()
			http.Error(w, "connector already bound to another wallet", http.StatusConflict)
			return
		}
		preparedWallet = body.Wallet
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(typed)
	})

	mux.HandleFunc(base+"complete", func(w http.ResponseWriter, r *http.Request) {
		if !checkPost(w, r) {
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 4096)
		var body ConnectResult
		dec := json.NewDecoder(r.Body)
		dec.DisallowUnknownFields()
		if err := dec.Decode(&body); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		if !validHex(body.Wallet, 20) || !validHex(body.Signature, 65) {
			http.Error(w, "invalid wallet response", http.StatusBadRequest)
			return
		}
		mu.Lock()
		if completed || preparedWallet == "" || !strings.EqualFold(preparedWallet, body.Wallet) {
			mu.Unlock()
			http.Error(w, "wallet response does not match prepared session", http.StatusConflict)
			return
		}
		completed = true
		mu.Unlock()
		select {
		case results <- body:
		default:
			http.Error(w, "connector already completed", http.StatusConflict)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true}`))
	})

	srv := &http.Server{
		Handler:           mux,
		ReadHeaderTimeout: 3 * time.Second,
		ReadTimeout:       5 * time.Second,
		WriteTimeout:      5 * time.Second,
		IdleTimeout:       10 * time.Second,
	}
	connector := &Connector{
		URL:      origin + base,
		Results:  results,
		server:   srv,
		listener: ln,
	}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdown)
	}()
	go func() {
		_ = srv.Serve(ln)
	}()
	return connector, nil
}

var connectorPage = template.Must(template.New("connect").Parse(`<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect WQPU Wallet</title>
<style>body{font-family:system-ui,sans-serif;max-width:560px;margin:12vh auto;padding:24px}button{font:inherit;padding:12px 18px}#status{margin-top:18px;white-space:pre-wrap}</style></head>
<body><h1>WQPU</h1><p>Connect your existing wallet. WQPU never asks for your seed phrase or private key.</p>
<button id="connect">Connect Wallet</button><div id="status"></div>
<script>
const button=document.getElementById('connect'); const status=document.getElementById('status');
const chainId={{printf "%q" .ChainHex}}; const rpcUrl={{printf "%q" .RPCURL}};
async function post(name, body){const r=await fetch(name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw new Error(await r.text());return r.json();}
button.onclick=async()=>{button.disabled=true;try{
 if(!window.ethereum)throw new Error('No compatible browser wallet found.');
 const accounts=await window.ethereum.request({method:'eth_requestAccounts'}); if(!accounts||!accounts[0])throw new Error('Wallet did not provide an account.');
 try{await window.ethereum.request({method:'wallet_switchEthereumChain',params:[{chainId}]});}
 catch(e){await window.ethereum.request({method:'wallet_addEthereumChain',params:[{chainId,chainName:'WQPU',nativeCurrency:{name:'WQPU',symbol:'WQPU',decimals:18},rpcUrls:[rpcUrl]}]}); await window.ethereum.request({method:'wallet_switchEthereumChain',params:[{chainId}]});}
 const wallet=accounts[0]; const typed=await post('prepare',{wallet});
 const signature=await window.ethereum.request({method:'eth_signTypedData_v4',params:[wallet,JSON.stringify(typed)]});
 await post('complete',{Wallet:wallet,Signature:signature}); status.textContent='Wallet connected. You can return to WQPU.';
}catch(e){status.textContent='Error: '+(e&&e.message?e.message:String(e));button.disabled=false;}};
</script></body></html>`))
