package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestRemoveIndexedAddressKeepsIndexDense(t *testing.T) {
	state := newMemoryState()
	a := common.HexToAddress("0x1111111111111111111111111111111111111111")
	b := common.HexToAddress("0x2222222222222222222222222222222222222222")
	c := common.HexToAddress("0x3333333333333333333333333333333333333333")
	for _, address := range []common.Address{a, b, c} {
		if _, _, err := AddIndexedAddress(state, "providers", address); err != nil {
			t.Fatal(err)
		}
	}

	removed, err := RemoveIndexedAddress(state, "providers", b)
	if err != nil || !removed {
		t.Fatalf("removed=%v err=%v", removed, err)
	}
	addresses, err := IndexedAddresses(state, "providers")
	if err != nil {
		t.Fatal(err)
	}
	if len(addresses) != 2 || addresses[0] != a || addresses[1] != c {
		t.Fatalf("addresses=%v", addresses)
	}

	index, added, err := AddIndexedAddress(state, "providers", c)
	if err != nil || added || index != 2 {
		t.Fatalf("moved address reverse index broken: index=%d added=%v err=%v", index, added, err)
	}
	index, added, err = AddIndexedAddress(state, "providers", b)
	if err != nil || !added || index != 3 {
		t.Fatalf("re-add index=%d added=%v err=%v", index, added, err)
	}
}

func TestRemovingMissingAddressIsNoop(t *testing.T) {
	state := newMemoryState()
	address := common.HexToAddress("0x1111111111111111111111111111111111111111")
	removed, err := RemoveIndexedAddress(state, "providers", address)
	if err != nil || removed {
		t.Fatalf("removed=%v err=%v", removed, err)
	}
}
