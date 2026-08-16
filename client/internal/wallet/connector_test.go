package wallet

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"strings"
	"testing"
	"time"
)

func connectorConfig() ConnectConfig {
	return ConnectConfig{
		WQPUChainID:     "wqpu-dev-1",
		EVMChainID:      711711,
		RPCURL:          "http://127.0.0.1:8545",
		SessionPubkey:   "0x" + strings.Repeat("22", 32),
		IssuedHeight:    100,
		ExpiresHeight:   200,
		MaxSpendUnits:   1_000_000,
		MaxJobUnits:     100_000,
		RevocationNonce: 0,
		Permissions:     SessionPermJob,
	}
}

func postJSON(t *testing.T, target, origin string, body any) *http.Response {
	t.Helper()
	encoded, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequest(http.MethodPost, target, bytes.NewReader(encoded))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if origin != "" {
		req.Header.Set("Origin", origin)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	return resp
}

func TestConnectorRequiresLocalRPC(t *testing.T) {
	cfg := connectorConfig()
	cfg.RPCURL = "https://rpc.example.com"
	if _, err := StartConnector(context.Background(), cfg); err == nil {
		t.Fatal("external wallet RPC should be rejected")
	}
}

func TestConnectorPrepareAndCompleteFlow(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	connector, err := StartConnector(ctx, connectorConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer connector.Close(context.Background())

	parsed, err := url.Parse(connector.URL)
	if err != nil {
		t.Fatal(err)
	}
	origin := parsed.Scheme + "://" + parsed.Host
	wallet := "0x1111111111111111111111111111111111111111"

	resp, err := http.Get(connector.URL)
	if err != nil {
		t.Fatal(err)
	}
	page, _ := io.ReadAll(resp.Body)
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(page), "Connect Wallet") {
		t.Fatalf("connector page status=%d", resp.StatusCode)
	}
	if !strings.Contains(resp.Header.Get("Content-Security-Policy"), "default-src 'none'") {
		t.Fatal("connector page is missing restrictive CSP")
	}

	forbidden := postJSON(t, connector.URL+"prepare", "", map[string]string{"wallet": wallet})
	_ = forbidden.Body.Close()
	if forbidden.StatusCode != http.StatusForbidden {
		t.Fatalf("cross-origin protection status=%d", forbidden.StatusCode)
	}

	prepared := postJSON(t, connector.URL+"prepare", origin, map[string]string{"wallet": wallet})
	if prepared.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(prepared.Body)
		t.Fatalf("prepare status=%d body=%s", prepared.StatusCode, body)
	}
	var typed TypedData
	if err := json.NewDecoder(prepared.Body).Decode(&typed); err != nil {
		t.Fatal(err)
	}
	_ = prepared.Body.Close()
	if typed.Message["wallet"] != wallet || typed.Domain["chainId"] != float64(711711) {
		t.Fatalf("unexpected typed data: %+v", typed)
	}

	signature := "0x" + strings.Repeat("33", 65)
	completed := postJSON(t, connector.URL+"complete", origin, ConnectResult{Wallet: wallet, Signature: signature})
	_ = completed.Body.Close()
	if completed.StatusCode != http.StatusOK {
		t.Fatalf("complete status=%d", completed.StatusCode)
	}

	select {
	case result := <-connector.Results:
		if result.Wallet != wallet || result.Signature != signature {
			t.Fatalf("result=%+v", result)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("connector did not return wallet result")
	}
}

func TestConnectorRejectsWalletSwapAfterPrepare(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	connector, err := StartConnector(ctx, connectorConfig())
	if err != nil {
		t.Fatal(err)
	}
	defer connector.Close(context.Background())
	parsed, _ := url.Parse(connector.URL)
	origin := parsed.Scheme + "://" + parsed.Host

	walletA := "0x1111111111111111111111111111111111111111"
	walletB := "0x2222222222222222222222222222222222222222"
	resp := postJSON(t, connector.URL+"prepare", origin, map[string]string{"wallet": walletA})
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("prepare status=%d", resp.StatusCode)
	}
	resp = postJSON(t, connector.URL+"complete", origin, ConnectResult{
		Wallet: walletB, Signature: "0x" + strings.Repeat("33", 65),
	})
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusConflict {
		t.Fatalf("wallet swap status=%d", resp.StatusCode)
	}
}
