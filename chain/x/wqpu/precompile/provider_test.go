package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

func validProvider() ProviderRecord {
	return ProviderRecord{
		Wallet:            common.HexToAddress("0x1111111111111111111111111111111111111111"),
		PeerID:            crypto.Keccak256Hash([]byte("peer-a")),
		Endpoints:         []string{"wqpu://127.0.0.1:7443", "wqpu://node.example:7443"},
		ModelHashes:       []common.Hash{crypto.Keccak256Hash([]byte("model-a")), crypto.Keccak256Hash([]byte("model-b"))},
		CapacityUnits:     100,
		ReportedBusyUnits: 25,
		FreeMemoryBytes:   8 * 1024 * 1024 * 1024,
		CapabilityHash:    crypto.Keccak256Hash([]byte("capabilities")),
		HeartbeatHeight:   100,
		ExpiresHeight:     120,
		ProtocolVersion:   uint32(ProtocolVersion),
	}
}

func TestProviderCodecRoundTrip(t *testing.T) {
	provider := validProvider()
	encoded, err := EncodeProvider(provider)
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := DecodeProvider(encoded)
	if err != nil {
		t.Fatal(err)
	}
	if !EqualProvider(provider, decoded) {
		t.Fatalf("decoded provider differs: %+v", decoded)
	}
}

func TestProviderCodecRejectsTrailingAndTruncatedData(t *testing.T) {
	encoded, err := EncodeProvider(validProvider())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := DecodeProvider(encoded[:len(encoded)-1]); err == nil {
		t.Fatal("truncated provider should fail")
	}
	if _, err := DecodeProvider(append(append([]byte{}, encoded...), 0)); err == nil {
		t.Fatal("provider with trailing bytes should fail")
	}
}

func TestProviderEndpointPolicy(t *testing.T) {
	bad := []string{
		"",
		"http://example.com:7443",
		"wqpu://example.com",
		"wqpu://0.0.0.0:7443",
		"wqpu://user@example.com:7443",
		"wqpu://example.com:0",
		"wqpu://example.com:99999",
		"wqpu://example.com:7443/path",
		"wqpu://example.com:7443?x=1",
	}
	for _, endpoint := range bad {
		provider := validProvider()
		provider.Endpoints = []string{endpoint}
		if err := provider.Validate(); err == nil {
			t.Fatalf("invalid endpoint accepted: %q", endpoint)
		}
	}
}

func TestProviderBoundsAndDuplicates(t *testing.T) {
	provider := validProvider()
	provider.ReportedBusyUnits = provider.CapacityUnits + 1
	if err := provider.Validate(); err == nil {
		t.Fatal("busy above capacity should fail")
	}
	provider = validProvider()
	provider.Endpoints = []string{provider.Endpoints[0], provider.Endpoints[0]}
	if err := provider.Validate(); err == nil {
		t.Fatal("duplicate endpoint should fail")
	}
	provider = validProvider()
	provider.ModelHashes = []common.Hash{provider.ModelHashes[0], provider.ModelHashes[0]}
	if err := provider.Validate(); err == nil {
		t.Fatal("duplicate model should fail")
	}
	provider = validProvider()
	provider.ExpiresHeight = provider.HeartbeatHeight
	if err := provider.Validate(); err == nil {
		t.Fatal("non-forward expiry should fail")
	}
}

func TestProviderStoreLoadDeleteAndIndex(t *testing.T) {
	state := newMemoryState()
	provider := validProvider()
	if err := StoreProvider(state, provider); err != nil {
		t.Fatal(err)
	}
	loaded, exists, err := LoadProvider(state, provider.Wallet)
	if err != nil || !exists || !EqualProvider(provider, loaded) {
		t.Fatalf("loaded=%+v exists=%v err=%v", loaded, exists, err)
	}
	addresses, err := IndexedAddresses(state, "providers")
	if err != nil || len(addresses) != 1 || addresses[0] != provider.Wallet {
		t.Fatalf("addresses=%v err=%v", addresses, err)
	}

	provider.ReportedBusyUnits = 50
	provider.HeartbeatHeight = 101
	provider.ExpiresHeight = 121
	if err := StoreProvider(state, provider); err != nil {
		t.Fatal(err)
	}
	addresses, err = IndexedAddresses(state, "providers")
	if err != nil || len(addresses) != 1 {
		t.Fatalf("heartbeat duplicated provider index: %v err=%v", addresses, err)
	}

	if err := DeleteProvider(state, provider.Wallet); err != nil {
		t.Fatal(err)
	}
	_, exists, err = LoadProvider(state, provider.Wallet)
	if err != nil || exists {
		t.Fatalf("provider survived delete exists=%v err=%v", exists, err)
	}
	addresses, err = IndexedAddresses(state, "providers")
	if err != nil || len(addresses) != 0 {
		t.Fatalf("provider index survived delete: %v err=%v", addresses, err)
	}
}

func TestProviderActivityWindow(t *testing.T) {
	provider := validProvider()
	if provider.ActiveAt(99) || !provider.ActiveAt(100) || !provider.ActiveAt(119) || provider.ActiveAt(120) {
		t.Fatal("provider activity window is incorrect")
	}
}
