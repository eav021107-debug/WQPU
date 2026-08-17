package auth

import (
	"encoding/hex"
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/crypto"

	"github.com/eav021107-debug/WQPU/chain/x/wqpu/kernel"
)

func signedDelegation(t *testing.T) (kernel.SessionDelegation, string) {
	t.Helper()
	key, err := crypto.HexToECDSA("4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a")
	if err != nil {
		t.Fatal(err)
	}
	wallet := crypto.PubkeyToAddress(key.PublicKey).Hex()
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil {
		t.Fatal(err)
	}
	d := kernel.SessionDelegation{
		ChainID:         "wqpu-dev-1",
		Wallet:          wallet,
		SessionAddress:  strings.ToLower(crypto.PubkeyToAddress(sessionKey.PublicKey).Hex()),
		IssuedHeight:    100,
		ExpiresHeight:   200,
		MaxSpendUnits:   1_000_000,
		MaxJobUnits:     100_000,
		RevocationNonce: 7,
		Permissions:     kernel.SessionPermJob | kernel.SessionPermSettle,
		ProtocolVersion: kernel.ProtocolVersion,
	}
	digest, err := SessionDigest(d, 711711)
	if err != nil {
		t.Fatal(err)
	}
	sig, err := crypto.Sign(digest, key)
	if err != nil {
		t.Fatal(err)
	}
	return d, "0x" + hex.EncodeToString(sig)
}

func TestVerifySessionSignature(t *testing.T) {
	d, sig := signedDelegation(t)
	if err := VerifySessionSignature(d, 711711, sig); err != nil {
		t.Fatal(err)
	}
}

func TestWalletStyleRecoveryID27Or28IsAccepted(t *testing.T) {
	d, sigHex := signedDelegation(t)
	sig, err := hex.DecodeString(strings.TrimPrefix(sigHex, "0x"))
	if err != nil {
		t.Fatal(err)
	}
	sig[64] += 27
	if err := VerifySessionSignature(d, 711711, "0x"+hex.EncodeToString(sig)); err != nil {
		t.Fatal(err)
	}
}

func TestTamperedSessionLimitInvalidatesSignature(t *testing.T) {
	d, sig := signedDelegation(t)
	d.MaxSpendUnits++
	if err := VerifySessionSignature(d, 711711, sig); err == nil {
		t.Fatal("tampered spend limit should invalidate wallet signature")
	}
}

func TestTamperedSessionAddressInvalidatesSignature(t *testing.T) {
	d, sig := signedDelegation(t)
	d.SessionAddress = "0x1111111111111111111111111111111111111111"
	if err := VerifySessionSignature(d, 711711, sig); err == nil {
		t.Fatal("tampered session address should invalidate wallet signature")
	}
}

func TestWrongChainInvalidatesSignature(t *testing.T) {
	d, sig := signedDelegation(t)
	if err := VerifySessionSignature(d, 711712, sig); err == nil {
		t.Fatal("signature should be bound to the EVM chain id")
	}
	d.ChainID = "wqpu-other"
	if err := VerifySessionSignature(d, 711711, sig); err == nil {
		t.Fatal("signature should be bound to the WQPU chain id")
	}
}

func TestWrongWalletInvalidatesSignature(t *testing.T) {
	d, sig := signedDelegation(t)
	d.Wallet = "0x1111111111111111111111111111111111111111"
	if err := VerifySessionSignature(d, 711711, sig); err == nil {
		t.Fatal("signature should recover the exact authorizing wallet")
	}
}

func TestMalformedSignatureRejected(t *testing.T) {
	d, _ := signedDelegation(t)
	if err := VerifySessionSignature(d, 711711, "0x1234"); err == nil {
		t.Fatal("short signature should be rejected")
	}
}
