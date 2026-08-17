package devidentity

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

func TestDevIdentitySlots(t *testing.T) {
	seenPeers := map[common.Hash]struct{}{}
	seenSessions := map[common.Address]struct{}{}
	for slot := 0; slot < SlotCount; slot++ {
		peer := PeerID(slot)
		if peer == (common.Hash{}) { t.Fatalf("slot %d has zero peer id", slot) }
		if _, exists := seenPeers[peer]; exists { t.Fatalf("duplicate peer id for slot %d", slot) }
		seenPeers[peer] = struct{}{}
		address, err := SessionAddress(slot)
		if err != nil { t.Fatal(err) }
		if address == (common.Address{}) { t.Fatalf("slot %d has zero session", slot) }
		if _, exists := seenSessions[address]; exists { t.Fatalf("duplicate session for slot %d", slot) }
		seenSessions[address] = struct{}{}
		key, err := SessionKey(slot)
		if err != nil { t.Fatal(err) }
		if key.Address() != address.Hex() && key.Address() != "0x"+string(address.Hex()[2:]) {
			// Key.Address is normalized lowercase; compare through HexToAddress below.
			if common.HexToAddress(key.Address()) != address { t.Fatalf("slot %d session mismatch", slot) }
		}
		if RPCPort(slot) == 0 || ProviderPort(slot) == 0 { t.Fatalf("slot %d has invalid ports", slot) }
	}
	if PeerID(-1) != (common.Hash{}) || PeerID(SlotCount) != (common.Hash{}) { t.Fatal("invalid slot should return zero peer id") }
	if RPCPort(-1) != 0 || ProviderPort(SlotCount) != 0 { t.Fatal("invalid slot should return zero port") }
	if _, err := SessionKey(SlotCount); err == nil { t.Fatal("invalid slot should fail") }
}
