package precompile

import (
	"encoding/hex"
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func authorizedActionFixture(t *testing.T) (*memoryState, SessionDelegation, *SessionState, string) {
	t.Helper()
	state := newMemoryState()
	delegation, walletSig := testSessionDelegation(t)
	delegation.Permissions = SessionPermProvider | SessionPermJob | SessionPermSettle
	walletKey, err := crypto.HexToECDSA("4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a")
	if err != nil { t.Fatal(err) }
	digest, err := SessionDigest(delegation, 711711)
	if err != nil { t.Fatal(err) }
	sig, err := crypto.Sign(digest, walletKey)
	if err != nil { t.Fatal(err) }
	walletSig = "0x" + hex.EncodeToString(sig)
	if err := AuthorizeSession(state, delegation, 711711, 120, walletSig); err != nil {
		t.Fatal(err)
	}
	stored, exists, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil || !exists { t.Fatalf("session exists=%v err=%v", exists, err) }
	return state, delegation, &stored, walletSig
}

func signAction(t *testing.T, action SessionAction) string {
	t.Helper()
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil { t.Fatal(err) }
	digest, err := ActionDigest(action, 711711)
	if err != nil { t.Fatal(err) }
	sig, err := crypto.Sign(digest, sessionKey)
	if err != nil { t.Fatal(err) }
	return "0x" + hex.EncodeToString(sig)
}

func actionFor(d SessionDelegation, nonce uint64, payload string) SessionAction {
	return SessionAction{
		WQPUChainID:     d.WQPUChainID,
		Wallet:          d.Wallet,
		Session:         d.Session,
		ActionKind:      ActionPublishProvider,
		ActionNonce:     nonce,
		Permission:      SessionPermProvider,
		PayloadHash:     crypto.Keccak256Hash([]byte(payload)),
		ProtocolVersion: uint32(ProtocolVersion),
	}
}

func TestValidSessionActionThenReplayFailsAfterNonceCommit(t *testing.T) {
	state, delegation, _, _ := authorizedActionFixture(t)
	action := actionFor(delegation, 0, "provider-heartbeat")
	sig := signAction(t, action)
	if _, err := VerifySessionAction(state, action, SessionPermProvider, 711711, 120, sig); err != nil {
		t.Fatal(err)
	}
	if err := AdvanceSessionActionNonce(state, delegation.Wallet, delegation.Session, 0); err != nil {
		t.Fatal(err)
	}
	if _, err := VerifySessionAction(state, action, SessionPermProvider, 711711, 120, sig); err == nil {
		t.Fatal("replayed action should fail after nonce advances")
	}
}

func TestPayloadTamperingInvalidatesSessionSignature(t *testing.T) {
	state, delegation, _, _ := authorizedActionFixture(t)
	action := actionFor(delegation, 0, "provider-A")
	sig := signAction(t, action)
	action.PayloadHash = crypto.Keccak256Hash([]byte("provider-B"))
	if _, err := VerifySessionAction(state, action, SessionPermProvider, 711711, 120, sig); err == nil {
		t.Fatal("tampered payload should fail signature recovery")
	}
}

func TestOperationCannotDowngradeRequiredPermission(t *testing.T) {
	state, delegation, _, _ := authorizedActionFixture(t)
	action := actionFor(delegation, 0, "job")
	action.ActionKind = ActionReserveJob
	action.Permission = SessionPermJob
	sig := signAction(t, action)
	if _, err := VerifySessionAction(state, action, SessionPermProvider, 711711, 120, sig); err == nil {
		t.Fatal("operation must enforce its exact permission")
	}
}

func TestWalletRevocationNonceKillsExistingSessionAction(t *testing.T) {
	state, delegation, _, _ := authorizedActionFixture(t)
	action := actionFor(delegation, 0, "provider-heartbeat")
	sig := signAction(t, action)
	if err := SetWalletRevocationNonce(state, delegation.Wallet, 1); err != nil {
		t.Fatal(err)
	}
	if _, err := VerifySessionAction(state, action, SessionPermProvider, 711711, 120, sig); err == nil {
		t.Fatal("wallet nonce revocation should kill old session")
	}
}

func TestExpiredSessionCannotSignActions(t *testing.T) {
	state, delegation, _, _ := authorizedActionFixture(t)
	action := actionFor(delegation, 0, "provider-heartbeat")
	sig := signAction(t, action)
	if _, err := VerifySessionAction(state, action, SessionPermProvider, 711711, delegation.ExpiresHeight, sig); err == nil {
		t.Fatal("expired session should fail")
	}
}

func TestActionWrongChainOrSignerFails(t *testing.T) {
	state, delegation, _, _ := authorizedActionFixture(t)
	action := actionFor(delegation, 0, "provider-heartbeat")
	sig := signAction(t, action)
	action.WQPUChainID = "wqpu-other"
	if _, err := VerifySessionAction(state, action, SessionPermProvider, 711711, 120, sig); err == nil {
		t.Fatal("cross-chain action should fail")
	}

	action = actionFor(delegation, 0, "provider-heartbeat")
	wrongKey, err := crypto.GenerateKey()
	if err != nil { t.Fatal(err) }
	digest, err := ActionDigest(action, 711711)
	if err != nil { t.Fatal(err) }
	wrongSig, err := crypto.Sign(digest, wrongKey)
	if err != nil { t.Fatal(err) }
	if _, err := VerifySessionAction(state, action, SessionPermProvider, 711711, 120, "0x"+hex.EncodeToString(wrongSig)); err == nil {
		t.Fatal("wrong session signer should fail")
	}
}
