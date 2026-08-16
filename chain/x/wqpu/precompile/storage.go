package precompile

import (
	"encoding/binary"
	"errors"
	"fmt"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

const (
	// Address is intentionally outside the Cosmos EVM v0.7.1 static range
	// (0x0100, 0x0400, 0x0800-0x0807). It is part of WQPU protocol v1.
	AddressHex = "0x0000000000000000000000000000000000000900"
	StorageVersion = "wqpu/v1"
	MaxBlobBytes = 64 * 1024
)

var Address = common.HexToAddress(AddressHex)

// WordState is the tiny subset of vm.StateDB needed by the WQPU storage layer.
// vm.StateDB satisfies it directly. Tests can use an in-memory map.
type WordState interface {
	GetState(common.Address, common.Hash) common.Hash
	SetState(common.Address, common.Hash, common.Hash) common.Hash
}

func slot(namespace string, parts ...[]byte) common.Hash {
	buf := make([]byte, 0, len(StorageVersion)+len(namespace)+64)
	appendPart := func(part []byte) {
		var size [4]byte
		binary.BigEndian.PutUint32(size[:], uint32(len(part)))
		buf = append(buf, size[:]...)
		buf = append(buf, part...)
	}
	appendPart([]byte(StorageVersion))
	appendPart([]byte(namespace))
	for _, part := range parts {
		appendPart(part)
	}
	return crypto.Keccak256Hash(buf)
}

func Uint64Slot(namespace string, key []byte) common.Hash {
	return slot("u64/"+namespace, key)
}

func BlobMetaSlot(namespace string, key []byte) common.Hash {
	return slot("blob-meta/"+namespace, key)
}

func BlobChunkSlot(namespace string, key []byte, chunk uint64) common.Hash {
	var index [8]byte
	binary.BigEndian.PutUint64(index[:], chunk)
	return slot("blob-chunk/"+namespace, key, index[:])
}

func AddressIndexCountSlot(namespace string) common.Hash {
	return slot("addr-index-count/"+namespace)
}

func AddressIndexItemSlot(namespace string, index uint64) common.Hash {
	var raw [8]byte
	binary.BigEndian.PutUint64(raw[:], index)
	return slot("addr-index-item/"+namespace, raw[:])
}

func AddressIndexReverseSlot(namespace string, address common.Address) common.Hash {
	return slot("addr-index-reverse/"+namespace, address.Bytes())
}

func hashToUint64(value common.Hash) (uint64, error) {
	for _, b := range value[:24] {
		if b != 0 {
			return 0, errors.New("stored uint64 exceeds 64 bits")
		}
	}
	return binary.BigEndian.Uint64(value[24:]), nil
}

func uint64Hash(value uint64) common.Hash {
	var out common.Hash
	binary.BigEndian.PutUint64(out[24:], value)
	return out
}

func GetUint64(state WordState, namespace string, key []byte) (uint64, error) {
	if state == nil {
		return 0, errors.New("nil WQPU state")
	}
	return hashToUint64(state.GetState(Address, Uint64Slot(namespace, key)))
}

func SetUint64(state WordState, namespace string, key []byte, value uint64) error {
	if state == nil {
		return errors.New("nil WQPU state")
	}
	state.SetState(Address, Uint64Slot(namespace, key), uint64Hash(value))
	return nil
}

func blobLength(state WordState, namespace string, key []byte) (uint64, error) {
	if state == nil {
		return 0, errors.New("nil WQPU state")
	}
	return hashToUint64(state.GetState(Address, BlobMetaSlot(namespace, key)))
}

func WriteBlob(state WordState, namespace string, key, data []byte) error {
	if state == nil {
		return errors.New("nil WQPU state")
	}
	if len(data) > MaxBlobBytes {
		return fmt.Errorf("WQPU blob exceeds %d bytes", MaxBlobBytes)
	}
	oldLength, err := blobLength(state, namespace, key)
	if err != nil {
		return err
	}
	oldChunks := (oldLength + 31) / 32
	newChunks := (uint64(len(data)) + 31) / 32

	for i := uint64(0); i < newChunks; i++ {
		start := i * 32
		end := start + 32
		if end > uint64(len(data)) {
			end = uint64(len(data))
		}
		var word common.Hash
		copy(word[:], data[start:end])
		state.SetState(Address, BlobChunkSlot(namespace, key, i), word)
	}
	// Zero chunks that are no longer reachable so shrinking/deleting a record
	// cannot leave hidden stale bytes in consensus state.
	for i := newChunks; i < oldChunks; i++ {
		state.SetState(Address, BlobChunkSlot(namespace, key, i), common.Hash{})
	}
	state.SetState(Address, BlobMetaSlot(namespace, key), uint64Hash(uint64(len(data))))
	return nil
}

func ReadBlob(state WordState, namespace string, key []byte) ([]byte, error) {
	length, err := blobLength(state, namespace, key)
	if err != nil {
		return nil, err
	}
	if length > MaxBlobBytes {
		return nil, errors.New("stored WQPU blob length exceeds protocol limit")
	}
	if length == 0 {
		return nil, nil
	}
	out := make([]byte, length)
	chunks := (length + 31) / 32
	for i := uint64(0); i < chunks; i++ {
		word := state.GetState(Address, BlobChunkSlot(namespace, key, i))
		start := i * 32
		end := start + 32
		if end > length {
			end = length
		}
		copy(out[start:end], word[:end-start])
	}
	return out, nil
}

func DeleteBlob(state WordState, namespace string, key []byte) error {
	return WriteBlob(state, namespace, key, nil)
}

func addressFromHash(value common.Hash) (common.Address, error) {
	for _, b := range value[:12] {
		if b != 0 {
			return common.Address{}, errors.New("stored address has non-zero prefix")
		}
	}
	return common.BytesToAddress(value[12:]), nil
}

func addressHash(address common.Address) common.Hash {
	return common.BytesToHash(address.Bytes())
}

// AddIndexedAddress inserts an address exactly once and returns its 1-based
// index. The index enables deterministic enumeration from chain state.
func AddIndexedAddress(state WordState, namespace string, address common.Address) (uint64, bool, error) {
	if state == nil || address == (common.Address{}) {
		return 0, false, errors.New("valid state and non-zero address required")
	}
	reverse := AddressIndexReverseSlot(namespace, address)
	existing, err := hashToUint64(state.GetState(Address, reverse))
	if err != nil {
		return 0, false, err
	}
	if existing != 0 {
		return existing, false, nil
	}
	count, err := hashToUint64(state.GetState(Address, AddressIndexCountSlot(namespace)))
	if err != nil {
		return 0, false, err
	}
	if count == ^uint64(0) {
		return 0, false, errors.New("WQPU address index overflow")
	}
	index := count + 1
	state.SetState(Address, AddressIndexItemSlot(namespace, index), addressHash(address))
	state.SetState(Address, reverse, uint64Hash(index))
	state.SetState(Address, AddressIndexCountSlot(namespace), uint64Hash(index))
	return index, true, nil
}

func IndexedAddresses(state WordState, namespace string) ([]common.Address, error) {
	if state == nil {
		return nil, errors.New("nil WQPU state")
	}
	count, err := hashToUint64(state.GetState(Address, AddressIndexCountSlot(namespace)))
	if err != nil {
		return nil, err
	}
	out := make([]common.Address, 0, count)
	for i := uint64(1); i <= count; i++ {
		address, err := addressFromHash(state.GetState(Address, AddressIndexItemSlot(namespace, i)))
		if err != nil {
			return nil, err
		}
		if address == (common.Address{}) {
			return nil, errors.New("corrupt WQPU address index")
		}
		out = append(out, address)
	}
	return out, nil
}
