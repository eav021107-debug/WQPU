package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func bondTestProvider(walletHex string, peerByte byte, capacity uint64) ProviderRecord {
	var peer common.Hash
	peer[31] = peerByte
	var capability common.Hash
	capability[31] = peerByte + 10
	return ProviderRecord{
		Wallet: common.HexToAddress(walletHex),
		PeerID: peer,
		Endpoints: []string{"wqpu://192.0.2.20:7443"},
		ModelHashes: []common.Hash{{31: 1}},
		CapacityUnits: capacity,
		ReportedBusyUnits: 0,
		FreeMemoryBytes: 8 * 1024 * 1024 * 1024,
		CapabilityHash: capability,
		HeartbeatHeight: 100,
		ExpiresHeight: 200,
		ProtocolVersion: uint32(ProtocolVersion),
	}
}

func TestAdvertisedCapacityDoesNotMovePriceCapacityWithoutBond(t *testing.T) {
	state := newMemoryState()
	provider := bondTestProvider("0x1000000000000000000000000000000000000001", 1, 1_000_000_000_000)
	if err := StorePeerProvider(state, provider); err != nil { t.Fatal(err) }
	got, err := AggregateBondedPriceCapacity(state, 120)
	if err != nil { t.Fatal(err) }
	if got != 0 {
		t.Fatalf("unbonded advertised capacity affected price supply: %d", got)
	}
}

func TestBondCapsProviderPriceCapacity(t *testing.T) {
	state := newMemoryState()
	provider := bondTestProvider("0x1000000000000000000000000000000000000001", 1, 1_000)
	if err := StorePeerProvider(state, provider); err != nil { t.Fatal(err) }
	if err := AddProviderBondCapacity(state, provider.PeerID, 250); err != nil { t.Fatal(err) }
	got, err := ProviderPriceCapacityUnits(state, provider.PeerID, 120)
	if err != nil { t.Fatal(err) }
	if got != 250 { t.Fatalf("price capacity=%d", got) }
	if err := AddProviderBondCapacity(state, provider.PeerID, 751); err == nil {
		t.Fatal("bond above advertised capacity should fail")
	}
}

func TestOnePeerBondCannotBackSecondSybilPeer(t *testing.T) {
	state := newMemoryState()
	wallet := "0x1000000000000000000000000000000000000001"
	first := bondTestProvider(wallet, 1, 500)
	second := bondTestProvider(wallet, 2, 500)
	second.Endpoints = []string{"wqpu://192.0.2.21:7443"}
	if err := StorePeerProvider(state, first); err != nil { t.Fatal(err) }
	if err := StorePeerProvider(state, second); err != nil { t.Fatal(err) }
	if err := AddProviderBondCapacity(state, first.PeerID, 500); err != nil { t.Fatal(err) }
	got, err := AggregateBondedPriceCapacity(state, 120)
	if err != nil { t.Fatal(err) }
	if got != 500 {
		t.Fatalf("one peer bond was multiplied across peers: %d", got)
	}
}

func TestProviderCannotUnbondDuringReservedWork(t *testing.T) {
	state := newMemoryState()
	provider := bondTestProvider("0x1000000000000000000000000000000000000001", 1, 500)
	if err := StorePeerProvider(state, provider); err != nil { t.Fatal(err) }
	if err := AddProviderBondCapacity(state, provider.PeerID, 500); err != nil { t.Fatal(err) }
	if err := setReservedPeerUnits(state, provider.PeerID, 1); err != nil { t.Fatal(err) }
	if err := RemoveProviderBondCapacity(state, provider.PeerID, 1); err == nil {
		t.Fatal("provider bond should stay locked while work is reserved")
	}
	bonded, err := ProviderBondedCapacityUnits(state, provider.PeerID)
	if err != nil { t.Fatal(err) }
	if bonded != 500 { t.Fatalf("bond changed after rejected unbond: %d", bonded) }
}

func TestExpiredProviderDoesNotCountAsPriceSupply(t *testing.T) {
	state := newMemoryState()
	provider := bondTestProvider("0x1000000000000000000000000000000000000001", 1, 500)
	if err := StorePeerProvider(state, provider); err != nil { t.Fatal(err) }
	if err := AddProviderBondCapacity(state, provider.PeerID, 500); err != nil { t.Fatal(err) }
	got, err := AggregateBondedPriceCapacity(state, 200)
	if err != nil { t.Fatal(err) }
	if got != 0 { t.Fatalf("expired provider counted toward price supply: %d", got) }
}
