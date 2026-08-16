package wallet

import (
	"encoding/json"
	"strings"
	"testing"
)

func validRequest() SessionRequest {
	return SessionRequest{
		WQPUChainID:     "wqpu-dev-1",
		EVMChainID:      711711,
		Wallet:          "0x1111111111111111111111111111111111111111",
		SessionAddress:  "0x2222222222222222222222222222222222222222",
		IssuedHeight:    100,
		ExpiresHeight:   200,
		MaxSpendUnits:   1_000_000,
		MaxJobUnits:     100_000,
		RevocationNonce: 0,
		Permissions:     SessionPermJob | SessionPermSettle,
	}
}

func TestTypedDataBindsWalletSessionAndBothChainIDs(t *testing.T) {
	r := validRequest()
	typed, err := BuildSessionTypedData(r)
	if err != nil {
		t.Fatal(err)
	}
	if typed.PrimaryType != "WQPUSession" {
		t.Fatalf("primary type=%q", typed.PrimaryType)
	}
	if typed.Domain["chainId"] != r.EVMChainID {
		t.Fatalf("domain chain id=%v", typed.Domain["chainId"])
	}
	if typed.Message["wqpuChainId"] != r.WQPUChainID {
		t.Fatalf("WQPU chain id=%v", typed.Message["wqpuChainId"])
	}
	if typed.Message["sessionAddress"] != r.SessionAddress {
		t.Fatal("session address not bound into wallet authorization")
	}
}

func TestSignRequestContainsNoWalletSecretFields(t *testing.T) {
	request, err := BuildSignRequest(validRequest())
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(request)
	if err != nil {
		t.Fatal(err)
	}
	lower := strings.ToLower(string(encoded))
	for _, forbidden := range []string{"privatekey", "private_key", "mnemonic", "seed phrase", "seed_phrase"} {
		if strings.Contains(lower, forbidden) {
			t.Fatalf("wallet request contains forbidden field %q", forbidden)
		}
	}
	if request["method"] != "eth_signTypedData_v4" {
		t.Fatalf("method=%v", request["method"])
	}
}

func TestInvalidWalletOrSessionAddressRejected(t *testing.T) {
	r := validRequest()
	r.Wallet = "bad"
	if _, err := BuildSessionTypedData(r); err == nil {
		t.Fatal("bad wallet should fail")
	}
	r = validRequest()
	r.SessionAddress = "0x12"
	if _, err := BuildSessionTypedData(r); err == nil {
		t.Fatal("bad session address should fail")
	}
	r = validRequest()
	r.SessionAddress = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
	if _, err := BuildSessionTypedData(r); err == nil {
		t.Fatal("non-canonical session address should fail")
	}
}

func TestUnknownPermissionRejectedBeforeWalletPrompt(t *testing.T) {
	r := validRequest()
	r.Permissions = SessionAllPermissions | (1 << 20)
	if _, err := BuildSignRequest(r); err == nil {
		t.Fatal("unknown permission should fail")
	}
}
