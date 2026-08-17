package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func TestOneWalletCanOwnSeveralIndependentPeers(t *testing.T) {
	state := newMemoryState()
	first := validProvider()
	second := validProvider()
	second.PeerID = crypto.Keccak256Hash([]byte("peer-b"))
	second.Endpoints = []string{"wqpu://192.0.2.2:7443"}
	second.CapabilityHash = crypto.Keccak256Hash([]byte("cap-b"))

	if err := StorePeerProvider(state, first); err != nil {
		t.Fatal(err)
	}
	if err := StorePeerProvider(state, second); err != nil {
		t.Fatal(err)
	}
	ids, err := ProviderPeerIDs(state)
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 2 || ids[0] != first.PeerID || ids[1] != second.PeerID {
		t.Fatalf("peer ids=%v", ids)
	}
}

func TestPeerIDCannotMoveToAnotherWallet(t *testing.T) {
	state := newMemoryState()
	provider := validProvider()
	if err := StorePeerProvider(state, provider); err != nil {
		t.Fatal(err)
	}
	attacker := provider
	attacker.Wallet[19] ^= 0x01
	attacker.HeartbeatHeight++
	attacker.ExpiresHeight++
	if err := StorePeerProvider(state, attacker); err == nil {
		t.Fatal("peer id ownership takeover should fail")
	}
	stored, exists, err := LoadPeerProvider(state, provider.PeerID)
	if err != nil || !exists || stored.Wallet != provider.Wallet {
		t.Fatalf("stored=%+v exists=%v err=%v", stored, exists, err)
	}
}

func TestHeartbeatMustAdvance(t *testing.T) {
	state := newMemoryState()
	provider := validProvider()
	if err := StorePeerProvider(state, provider); err != nil {
		t.Fatal(err)
	}
	provider.ReportedBusyUnits++
	if err := StorePeerProvider(state, provider); err == nil {
		t.Fatal("same-height heartbeat should fail")
	}
	provider.HeartbeatHeight++
	provider.ExpiresHeight++
	if err := StorePeerProvider(state, provider); err != nil {
		t.Fatal(err)
	}
}

func TestPeerDeleteUsesDenseHashIndex(t *testing.T) {
	state := newMemoryState()
	first := validProvider()
	second := validProvider()
	second.PeerID = crypto.Keccak256Hash([]byte("peer-b"))
	second.Endpoints = []string{"wqpu://192.0.2.2:7443"}
	second.CapabilityHash = crypto.Keccak256Hash([]byte("cap-b"))
	third := validProvider()
	third.PeerID = crypto.Keccak256Hash([]byte("peer-c"))
	third.Endpoints = []string{"wqpu://192.0.2.3:7443"}
	third.CapabilityHash = crypto.Keccak256Hash([]byte("cap-c"))
	for _, provider := range []ProviderRecord{first, second, third} {
		if err := StorePeerProvider(state, provider); err != nil {
			t.Fatal(err)
		}
	}
	if err := DeletePeerProvider(state, second.PeerID); err != nil {
		t.Fatal(err)
	}
	ids, err := ProviderPeerIDs(state)
	if err != nil {
		t.Fatal(err)
	}
	if len(ids) != 2 || ids[0] != first.PeerID || ids[1] != third.PeerID {
		t.Fatalf("peer ids=%v", ids)
	}
	_, exists, err := LoadPeerProvider(state, second.PeerID)
	if err != nil || exists {
		t.Fatalf("deleted peer exists=%v err=%v", exists, err)
	}
}
