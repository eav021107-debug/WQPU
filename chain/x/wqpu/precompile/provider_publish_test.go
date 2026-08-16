package precompile

import (
	"encoding/hex"
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func providerEnvelope(t *testing.T, state *memoryState, delegation SessionDelegation, nonce uint64, peerLabel string) ProviderPublishEnvelope {
	t.Helper()
	announcement := ProviderAnnouncement{
		Wallet: delegation.Wallet,
		PeerID: crypto.Keccak256Hash([]byte(peerLabel)),
		Endpoints: []string{"wqpu://192.0.2.10:7443"},
		ModelHashes: []common.Hash{crypto.Keccak256Hash([]byte("model-a"))},
		CapacityUnits: 100,
		ReportedBusyUnits: 10,
		FreeMemoryBytes: 4 * 1024 * 1024 * 1024,
		CapabilityHash: crypto.Keccak256Hash([]byte("cap-" + peerLabel)),
		ProtocolVersion: uint32(ProtocolVersion),
	}
	payloadHash, err := ProviderAnnouncementHash(announcement)
	if err != nil { t.Fatal(err) }
	action := SessionAction{
		WQPUChainID: delegation.WQPUChainID,
		Wallet: delegation.Wallet,
		Session: delegation.Session,
		ActionKind: ActionPublishProvider,
		ActionNonce: nonce,
		Permission: SessionPermProvider,
		PayloadHash: payloadHash,
		ProtocolVersion: uint32(ProtocolVersion),
	}
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil { t.Fatal(err) }
	digest, err := ActionDigest(action, DevNetworkConfig.EVMChainID)
	if err != nil { t.Fatal(err) }
	sig, err := crypto.Sign(digest, sessionKey)
	if err != nil { t.Fatal(err) }
	return ProviderPublishEnvelope{Wallet: delegation.Wallet, Session: delegation.Session, ActionNonce: nonce, Announcement: announcement, Signature: sig}
}

func providerAuthorizedFixture(t *testing.T) (*memoryState, SessionDelegation) {
	t.Helper()
	state := newMemoryState()
	delegation, _ := testSessionDelegation(t)
	delegation.Permissions = SessionPermProvider | SessionPermJob
	walletKey, _ := crypto.HexToECDSA("4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a")
	digest, err := SessionDigest(delegation, DevNetworkConfig.EVMChainID)
	if err != nil { t.Fatal(err) }
	sig, err := crypto.Sign(digest, walletKey)
	if err != nil { t.Fatal(err) }
	if err := AuthorizeSession(state, delegation, DevNetworkConfig.EVMChainID, 120, "0x"+hex.EncodeToString(sig)); err != nil {
		t.Fatal(err)
	}
	return state, delegation
}

func TestProviderPublishEnvelopeRoundTrip(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "peer-a")
	encoded, err := EncodeProviderPublishEnvelope(envelope)
	if err != nil { t.Fatal(err) }
	decoded, err := DecodeProviderPublishEnvelope(encoded)
	if err != nil { t.Fatal(err) }
	if decoded.Wallet != envelope.Wallet || decoded.Session != envelope.Session || decoded.ActionNonce != 0 || decoded.Announcement.PeerID != envelope.Announcement.PeerID || string(decoded.Signature) != string(envelope.Signature) {
		t.Fatalf("decoded=%+v", decoded)
	}
}

func TestSignedProviderPublishCommitsRegistryAndNonce(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "peer-a")
	if err := CommitProviderPublish(state, envelope, DevNetworkConfig, 121); err != nil {
		t.Fatal(err)
	}
	provider, exists, err := LoadPeerProvider(state, envelope.Announcement.PeerID)
	if err != nil || !exists { t.Fatalf("exists=%v err=%v", exists, err) }
	if provider.Wallet != delegation.Wallet || provider.HeartbeatHeight != 121 || provider.ExpiresHeight != 121+ProviderTTLBlocks {
		t.Fatalf("provider=%+v", provider)
	}
	session, exists, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil || !exists || session.ActionNonce != 1 {
		t.Fatalf("session=%+v exists=%v err=%v", session, exists, err)
	}
}

func TestSameWalletCanPublishSecondDeviceWithNextNonce(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	first := providerEnvelope(t, state, delegation, 0, "peer-a")
	if err := CommitProviderPublish(state, first, DevNetworkConfig, 121); err != nil { t.Fatal(err) }
	second := providerEnvelope(t, state, delegation, 1, "peer-b")
	second.Announcement.Endpoints = []string{"wqpu://192.0.2.11:7443"}
	// Endpoint is signed; regenerate signature after changing it.
	payloadHash, err := ProviderAnnouncementHash(second.Announcement)
	if err != nil { t.Fatal(err) }
	action := SessionAction{WQPUChainID: delegation.WQPUChainID, Wallet: delegation.Wallet, Session: delegation.Session, ActionKind: ActionPublishProvider, ActionNonce: 1, Permission: SessionPermProvider, PayloadHash: payloadHash, ProtocolVersion: uint32(ProtocolVersion)}
	sessionKey, _ := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	digest, _ := ActionDigest(action, DevNetworkConfig.EVMChainID)
	second.Signature, _ = crypto.Sign(digest, sessionKey)
	if err := CommitProviderPublish(state, second, DevNetworkConfig, 122); err != nil { t.Fatal(err) }
	ids, err := ProviderPeerIDs(state)
	if err != nil || len(ids) != 2 { t.Fatalf("ids=%v err=%v", ids, err) }
}

func TestTamperedProviderAnnouncementCannotPublish(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "peer-a")
	envelope.Announcement.CapacityUnits++
	if err := CommitProviderPublish(state, envelope, DevNetworkConfig, 121); err == nil {
		t.Fatal("tampered provider announcement should fail")
	}
	ids, err := ProviderPeerIDs(state)
	if err != nil || len(ids) != 0 { t.Fatalf("ids=%v err=%v", ids, err) }
}

func TestProviderPublishReplayFails(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "peer-a")
	if err := CommitProviderPublish(state, envelope, DevNetworkConfig, 121); err != nil { t.Fatal(err) }
	if err := CommitProviderPublish(state, envelope, DevNetworkConfig, 122); err == nil {
		t.Fatal("replayed provider publish should fail")
	}
}

func TestProviderPublishEnvelopeRejectsTrailingBytes(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "peer-a")
	encoded, err := EncodeProviderPublishEnvelope(envelope)
	if err != nil { t.Fatal(err) }
	if _, err := DecodeProviderPublishEnvelope(append(encoded, 0)); err == nil {
		t.Fatal("provider envelope with trailing bytes should fail")
	}
}
