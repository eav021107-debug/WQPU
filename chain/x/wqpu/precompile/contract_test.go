package precompile

import (
	"bytes"
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestSelectorsAreStableAndDistinct(t *testing.T) {
	selectors := [][4]byte{
		selectorProtocolVersion,
		selectorGlobalPrice,
		selectorProviderCount,
		selectorProviderAt,
	}
	for i := range selectors {
		for j := i + 1; j < len(selectors); j++ {
			if selectors[i] == selectors[j] {
				t.Fatalf("selector collision at %d and %d", i, j)
			}
		}
	}
}

func TestReadGasIsBounded(t *testing.T) {
	contract := New()
	if got := contract.RequiredGas(selectorProtocolVersion[:]); got != 500 {
		t.Fatalf("protocol gas=%d", got)
	}
	for _, input := range [][]byte{
		selectorGlobalPrice[:],
		selectorProviderCount[:],
		append(append([]byte{}, selectorProviderAt[:]...), make([]byte, 32)...),
		{0xff, 0xff, 0xff, 0xff},
	} {
		if got := contract.RequiredGas(input); got != readGas {
			t.Fatalf("read gas=%d", got)
		}
	}
}

func TestGlobalPriceDefaultsThenReadsState(t *testing.T) {
	state := newMemoryState()
	price, err := currentGlobalPrice(state)
	if err != nil {
		t.Fatal(err)
	}
	if price != InitialGlobalPrice {
		t.Fatalf("initial price=%d", price)
	}
	if err := SetUint64(state, "global", []byte("price-per-million"), 4321); err != nil {
		t.Fatal(err)
	}
	price, err = currentGlobalPrice(state)
	if err != nil || price != 4321 {
		t.Fatalf("stored price=%d err=%v", price, err)
	}
}

func TestProviderReadHelpersUseDeterministicIndex(t *testing.T) {
	state := newMemoryState()
	a := common.HexToAddress("0x1111111111111111111111111111111111111111")
	b := common.HexToAddress("0x2222222222222222222222222222222222222222")
	if _, _, err := AddIndexedAddress(state, "providers", a); err != nil {
		t.Fatal(err)
	}
	if _, _, err := AddIndexedAddress(state, "providers", b); err != nil {
		t.Fatal(err)
	}
	count, err := providerCount(state)
	if err != nil || count != 2 {
		t.Fatalf("count=%d err=%v", count, err)
	}
	got, err := providerAt(state, 1)
	if err != nil || got != a {
		t.Fatalf("provider1=%s err=%v", got.Hex(), err)
	}
	got, err = providerAt(state, 2)
	if err != nil || got != b {
		t.Fatalf("provider2=%s err=%v", got.Hex(), err)
	}
	if _, err := providerAt(state, 0); err == nil {
		t.Fatal("zero provider index should fail")
	}
	if _, err := providerAt(state, 3); err == nil {
		t.Fatal("out-of-range provider index should fail")
	}
}

func TestABIEncodingHelpers(t *testing.T) {
	if got := encodeUint256(7); len(got) != 32 || got[31] != 7 {
		t.Fatalf("uint encoding=%x", got)
	}
	address := common.HexToAddress("0x1234567890123456789012345678901234567890")
	encoded := encodeAddress(address)
	if len(encoded) != 32 || !bytes.Equal(encoded[12:], address.Bytes()) {
		t.Fatalf("address encoding=%x", encoded)
	}
	word := make([]byte, 32)
	word[31] = 9
	value, err := decodeUint256Word(word)
	if err != nil || value != 9 {
		t.Fatalf("decoded=%d err=%v", value, err)
	}
	word[0] = 1
	if _, err := decodeUint256Word(word); err == nil {
		t.Fatal("uint256 larger than uint64 should fail")
	}
}
