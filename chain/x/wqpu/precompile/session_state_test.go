package precompile

import (
	"encoding/hex"
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"

	legacyauth "github.com/eav021107-debug/WQPU/chain/x/wqpu/auth"
	legacykernel "github.com/eav021107-debug/WQPU/chain/x/wqpu/kernel"
)

func testSessionDelegation(t *testing.T) (SessionDelegation, string) {
	t.Helper()
	walletKey, err := crypto.HexToECDSA("4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a")
	if err != nil { t.Fatal(err) }
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil { t.Fatal(err) }
	d := SessionDelegation{
		WQPUChainID:     "wqpu-dev-1",
		Wallet:          crypto.PubkeyToAddress(walletKey.PublicKey),
		Session:         crypto.PubkeyToAddress(sessionKey.PublicKey),
		IssuedHeight:    100,
		ExpiresHeight:   200,
		MaxSpendUnits:   1_000_000,
		MaxJobUnits:     100_000,
		RevocationNonce: 0,
		Permissions:     SessionPermJob | SessionPermSettle,
		ProtocolVersion: uint32(ProtocolVersion),
	}
	digest, err := SessionDigest(d, 711711)
	if err != nil { t.Fatal(err) }
	sig, err := crypto.Sign(digest, walletKey)
	if err != nil { t.Fatal(err) }
	return d, "0x" + hex.EncodeToString(sig)
}

func TestPrecompileSessionDigestMatchesReferenceKernel(t *testing.T) {
	d, _ := testSessionDelegation(t)
	got, err := SessionDigest(d, 711711)
	if err != nil { t.Fatal(err) }
	legacy := legacykernel.SessionDelegation{
		ChainID: d.WQPUChainID,
		Wallet: d.Wallet.Hex(),
		SessionAddress: strings.ToLower(d.Session.Hex()),
		IssuedHeight: d.IssuedHeight,
		ExpiresHeight: d.ExpiresHeight,
		MaxSpendUnits: d.MaxSpendUnits,
		MaxJobUnits: d.MaxJobUnits,
		RevocationNonce: d.RevocationNonce,
		Permissions: d.Permissions,
		ProtocolVersion: d.ProtocolVersion,
	}
	want, err := legacyauth.SessionDigest(legacy, 711711)
	if err != nil { t.Fatal(err) }
	if string(got) != string(want) {
		t.Fatalf("digest mismatch\nprecompile=%x\nreference=%x", got, want)
	}
}

func TestAuthorizeSessionVerifiesWalletBeforeStateMutation(t *testing.T) {
	state := newMemoryState()
	d, sig := testSessionDelegation(t)
	if err := AuthorizeSession(state, d, 711711, 120, sig); err != nil {
		t.Fatal(err)
	}
	stored, exists, err := LoadSession(state, d.Wallet, d.Session)
	if err != nil || !exists {
		t.Fatalf("exists=%v err=%v", exists, err)
	}
	if stored.Delegation.Wallet != d.Wallet || stored.Delegation.Session != d.Session || stored.SpentUnits != 0 || stored.ActionNonce != 0 {
		t.Fatalf("stored=%+v", stored)
	}
}

func TestTamperedSessionAuthorizationWritesNothing(t *testing.T) {
	state := newMemoryState()
	d, sig := testSessionDelegation(t)
	d.MaxSpendUnits++
	if err := AuthorizeSession(state, d, 711711, 120, sig); err == nil {
		t.Fatal("tampered delegation should fail")
	}
	_, exists, err := LoadSession(state, d.Wallet, d.Session)
	if err != nil { t.Fatal(err) }
	if exists {
		t.Fatal("invalid wallet proof created session state")
	}
}

func TestSessionCodecRejectsCorruptionAndOverspend(t *testing.T) {
	d, _ := testSessionDelegation(t)
	state := SessionState{Delegation: d, SpentUnits: 100, ReservedUnits: 200, ActionNonce: 9}
	encoded, err := EncodeSession(state)
	if err != nil { t.Fatal(err) }
	decoded, err := DecodeSession(encoded)
	if err != nil { t.Fatal(err) }
	if decoded.SpentUnits != 100 || decoded.ReservedUnits != 200 || decoded.ActionNonce != 9 {
		t.Fatalf("decoded=%+v", decoded)
	}
	if _, err := DecodeSession(encoded[:len(encoded)-1]); err == nil {
		t.Fatal("truncated session should fail")
	}
	state.SpentUnits = d.MaxSpendUnits
	state.ReservedUnits = 1
	if encoded, err = EncodeSession(state); err != nil {
		t.Fatal(err)
	}
	if _, err := DecodeSession(encoded); err == nil {
		t.Fatal("stored overspend should fail decoding")
	}
}

func TestRevocationNoncePreventsStaleAuthorization(t *testing.T) {
	state := newMemoryState()
	d, _ := testSessionDelegation(t)
	if err := SetWalletRevocationNonce(state, d.Wallet, 1); err != nil {
		t.Fatal(err)
	}
	walletKey, _ := crypto.HexToECDSA("4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a")
	digest, err := SessionDigest(d, 711711)
	if err != nil { t.Fatal(err) }
	sig, err := crypto.Sign(digest, walletKey)
	if err != nil { t.Fatal(err) }
	if err := AuthorizeSession(state, d, 711711, 120, "0x"+hex.EncodeToString(sig)); err == nil {
		t.Fatal("stale revocation nonce should fail")
	}
}

func TestSessionIdentityCannotBeZero(t *testing.T) {
	d, _ := testSessionDelegation(t)
	d.Session = common.Address{}
	if err := d.Validate(); err == nil {
		t.Fatal("zero session address should fail")
	}
}
