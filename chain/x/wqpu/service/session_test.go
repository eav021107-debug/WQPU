package service

import (
	"encoding/hex"
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/crypto"

	"github.com/eav021107-debug/WQPU/chain/x/wqpu/auth"
	"github.com/eav021107-debug/WQPU/chain/x/wqpu/kernel"
)

func signedSession(t *testing.T, chainID string) (kernel.SessionDelegation, string) {
	t.Helper()
	key, err := crypto.HexToECDSA("4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a")
	if err != nil {
		t.Fatal(err)
	}
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil {
		t.Fatal(err)
	}
	d := kernel.SessionDelegation{
		ChainID:         chainID,
		Wallet:          crypto.PubkeyToAddress(key.PublicKey).Hex(),
		SessionAddress:  strings.ToLower(crypto.PubkeyToAddress(sessionKey.PublicKey).Hex()),
		IssuedHeight:    0,
		ExpiresHeight:   100,
		MaxSpendUnits:   1000,
		MaxJobUnits:     200,
		RevocationNonce: 0,
		Permissions:     kernel.SessionPermJob,
		ProtocolVersion: kernel.ProtocolVersion,
	}
	digest, err := auth.SessionDigest(d, 711711)
	if err != nil {
		t.Fatal(err)
	}
	sig, err := crypto.Sign(digest, key)
	if err != nil {
		t.Fatal(err)
	}
	return d, "0x" + hex.EncodeToString(sig)
}

func TestValidWalletSignatureMutatesStateOnce(t *testing.T) {
	state, err := kernel.NewState("wqpu-dev-1", 1000)
	if err != nil {
		t.Fatal(err)
	}
	d, sig := signedSession(t, "wqpu-dev-1")
	if err := AuthorizeWalletSession(state, d, 711711, sig); err != nil {
		t.Fatal(err)
	}
	if len(state.Sessions) != 1 {
		t.Fatalf("sessions=%d", len(state.Sessions))
	}
}

func TestInvalidSignatureCannotMutateState(t *testing.T) {
	state, err := kernel.NewState("wqpu-dev-1", 1000)
	if err != nil {
		t.Fatal(err)
	}
	d, sig := signedSession(t, "wqpu-dev-1")
	d.MaxSpendUnits++
	if err := AuthorizeWalletSession(state, d, 711711, sig); err == nil {
		t.Fatal("tampered delegation should fail")
	}
	if len(state.Sessions) != 0 {
		t.Fatal("invalid wallet signature changed chain state")
	}
}

func TestSignatureForAnotherWQPUChainCannotMutateState(t *testing.T) {
	state, err := kernel.NewState("wqpu-dev-1", 1000)
	if err != nil {
		t.Fatal(err)
	}
	d, sig := signedSession(t, "wqpu-other")
	if err := AuthorizeWalletSession(state, d, 711711, sig); err == nil {
		t.Fatal("delegation for another WQPU chain should fail")
	}
	if len(state.Sessions) != 0 {
		t.Fatal("cross-chain delegation changed state")
	}
}
