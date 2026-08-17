package precompile

import (
	"bytes"
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/common"
)

type memoryState struct {
	words map[common.Address]map[common.Hash]common.Hash
}

func newMemoryState() *memoryState {
	return &memoryState{words: map[common.Address]map[common.Hash]common.Hash{}}
}

func (m *memoryState) GetState(address common.Address, key common.Hash) common.Hash {
	if m.words[address] == nil {
		return common.Hash{}
	}
	return m.words[address][key]
}

func (m *memoryState) SetState(address common.Address, key common.Hash, value common.Hash) common.Hash {
	if m.words[address] == nil {
		m.words[address] = map[common.Hash]common.Hash{}
	}
	old := m.words[address][key]
	if value == (common.Hash{}) {
		delete(m.words[address], key)
	} else {
		m.words[address][key] = value
	}
	return old
}

func TestWQPUPrecompileAddressIsDedicated(t *testing.T) {
	if Address.Hex() != "0x0000000000000000000000000000000000000900" {
		t.Fatalf("address=%s", Address.Hex())
	}
	for _, reserved := range []string{
		"0x0000000000000000000000000000000000000100",
		"0x0000000000000000000000000000000000000400",
		"0x0000000000000000000000000000000000000800",
		"0x0000000000000000000000000000000000000801",
		"0x0000000000000000000000000000000000000802",
		"0x0000000000000000000000000000000000000803",
		"0x0000000000000000000000000000000000000804",
		"0x0000000000000000000000000000000000000805",
		"0x0000000000000000000000000000000000000806",
		"0x0000000000000000000000000000000000000807",
	} {
		if Address == common.HexToAddress(reserved) {
			t.Fatalf("WQPU address collides with %s", reserved)
		}
	}
}

func TestLengthPrefixedSlotsAvoidAmbiguousKeys(t *testing.T) {
	a := slot("provider", []byte("ab"), []byte("c"))
	b := slot("provider", []byte("a"), []byte("bc"))
	c := slot("provider/ab", []byte("c"))
	if a == b || a == c || b == c {
		t.Fatal("storage slot derivation collided")
	}
	if a != slot("provider", []byte("ab"), []byte("c")) {
		t.Fatal("same logical key did not produce the same slot")
	}
}

func TestUint64RoundTrip(t *testing.T) {
	state := newMemoryState()
	if err := SetUint64(state, "price", []byte("current"), 123456789); err != nil {
		t.Fatal(err)
	}
	got, err := GetUint64(state, "price", []byte("current"))
	if err != nil {
		t.Fatal(err)
	}
	if got != 123456789 {
		t.Fatalf("value=%d", got)
	}
}

func TestBlobRoundTripAcrossSeveralWords(t *testing.T) {
	state := newMemoryState()
	data := []byte(strings.Repeat("wqpu-", 37))
	if err := WriteBlob(state, "provider", []byte("wallet-a"), data); err != nil {
		t.Fatal(err)
	}
	got, err := ReadBlob(state, "provider", []byte("wallet-a"))
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, data) {
		t.Fatalf("blob mismatch: got %d bytes want %d", len(got), len(data))
	}
}

func TestShrinkingBlobClearsOldChunks(t *testing.T) {
	state := newMemoryState()
	key := []byte("record")
	large := bytes.Repeat([]byte{0xAB}, 100)
	if err := WriteBlob(state, "test", key, large); err != nil {
		t.Fatal(err)
	}
	staleSlot := BlobChunkSlot("test", key, 3)
	if state.GetState(Address, staleSlot) == (common.Hash{}) {
		t.Fatal("expected fourth chunk before shrink")
	}
	if err := WriteBlob(state, "test", key, []byte("small")); err != nil {
		t.Fatal(err)
	}
	if state.GetState(Address, staleSlot) != (common.Hash{}) {
		t.Fatal("shrinking blob left stale chunk")
	}
	got, err := ReadBlob(state, "test", key)
	if err != nil || string(got) != "small" {
		t.Fatalf("got=%q err=%v", got, err)
	}
}

func TestDeleteBlobClearsPayload(t *testing.T) {
	state := newMemoryState()
	key := []byte("record")
	if err := WriteBlob(state, "test", key, bytes.Repeat([]byte{1}, 70)); err != nil {
		t.Fatal(err)
	}
	if err := DeleteBlob(state, "test", key); err != nil {
		t.Fatal(err)
	}
	got, err := ReadBlob(state, "test", key)
	if err != nil || len(got) != 0 {
		t.Fatalf("deleted blob=%x err=%v", got, err)
	}
	for i := uint64(0); i < 3; i++ {
		if state.GetState(Address, BlobChunkSlot("test", key, i)) != (common.Hash{}) {
			t.Fatalf("chunk %d survived delete", i)
		}
	}
}

func TestBlobProtocolLimit(t *testing.T) {
	state := newMemoryState()
	if err := WriteBlob(state, "test", []byte("too-big"), make([]byte, MaxBlobBytes+1)); err == nil {
		t.Fatal("oversized consensus blob should be rejected")
	}
}

func TestAddressIndexIsDeterministicAndDeduplicated(t *testing.T) {
	state := newMemoryState()
	a := common.HexToAddress("0x1111111111111111111111111111111111111111")
	b := common.HexToAddress("0x2222222222222222222222222222222222222222")
	index, added, err := AddIndexedAddress(state, "providers", a)
	if err != nil || !added || index != 1 {
		t.Fatalf("first add index=%d added=%v err=%v", index, added, err)
	}
	index, added, err = AddIndexedAddress(state, "providers", a)
	if err != nil || added || index != 1 {
		t.Fatalf("duplicate add index=%d added=%v err=%v", index, added, err)
	}
	index, added, err = AddIndexedAddress(state, "providers", b)
	if err != nil || !added || index != 2 {
		t.Fatalf("second address index=%d added=%v err=%v", index, added, err)
	}
	addresses, err := IndexedAddresses(state, "providers")
	if err != nil {
		t.Fatal(err)
	}
	if len(addresses) != 2 || addresses[0] != a || addresses[1] != b {
		t.Fatalf("addresses=%v", addresses)
	}
}
