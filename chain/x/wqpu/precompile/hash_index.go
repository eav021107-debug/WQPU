package precompile

import (
	"encoding/binary"
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

func HashIndexCountSlot(namespace string) common.Hash {
	return slot("hash-index-count/"+namespace)
}

func HashIndexItemSlot(namespace string, index uint64) common.Hash {
	var raw [8]byte
	binary.BigEndian.PutUint64(raw[:], index)
	return slot("hash-index-item/"+namespace, raw[:])
}

func HashIndexReverseSlot(namespace string, value common.Hash) common.Hash {
	return slot("hash-index-reverse/"+namespace, value.Bytes())
}

func AddIndexedHash(state WordState, namespace string, value common.Hash) (uint64, bool, error) {
	if state == nil || value == (common.Hash{}) {
		return 0, false, errors.New("valid state and non-zero hash required")
	}
	reverse := HashIndexReverseSlot(namespace, value)
	existing, err := hashToUint64(state.GetState(Address, reverse))
	if err != nil {
		return 0, false, err
	}
	if existing != 0 {
		return existing, false, nil
	}
	count, err := hashToUint64(state.GetState(Address, HashIndexCountSlot(namespace)))
	if err != nil {
		return 0, false, err
	}
	if count == ^uint64(0) {
		return 0, false, errors.New("WQPU hash index overflow")
	}
	index := count + 1
	state.SetState(Address, HashIndexItemSlot(namespace, index), value)
	state.SetState(Address, reverse, uint64Hash(index))
	state.SetState(Address, HashIndexCountSlot(namespace), uint64Hash(index))
	return index, true, nil
}

func RemoveIndexedHash(state WordState, namespace string, value common.Hash) (bool, error) {
	if state == nil || value == (common.Hash{}) {
		return false, errors.New("valid state and non-zero hash required")
	}
	reverse := HashIndexReverseSlot(namespace, value)
	index, err := hashToUint64(state.GetState(Address, reverse))
	if err != nil {
		return false, err
	}
	if index == 0 {
		return false, nil
	}
	count, err := hashToUint64(state.GetState(Address, HashIndexCountSlot(namespace)))
	if err != nil {
		return false, err
	}
	if count == 0 || index > count {
		return false, errors.New("corrupt WQPU hash index")
	}
	if index != count {
		last := state.GetState(Address, HashIndexItemSlot(namespace, count))
		if last == (common.Hash{}) {
			return false, errors.New("corrupt WQPU hash index tail")
		}
		state.SetState(Address, HashIndexItemSlot(namespace, index), last)
		state.SetState(Address, HashIndexReverseSlot(namespace, last), uint64Hash(index))
	}
	state.SetState(Address, HashIndexItemSlot(namespace, count), common.Hash{})
	state.SetState(Address, reverse, common.Hash{})
	state.SetState(Address, HashIndexCountSlot(namespace), uint64Hash(count-1))
	return true, nil
}

func IndexedHashes(state WordState, namespace string) ([]common.Hash, error) {
	if state == nil {
		return nil, errors.New("nil WQPU state")
	}
	count, err := hashToUint64(state.GetState(Address, HashIndexCountSlot(namespace)))
	if err != nil {
		return nil, err
	}
	out := make([]common.Hash, 0, count)
	for i := uint64(1); i <= count; i++ {
		value := state.GetState(Address, HashIndexItemSlot(namespace, i))
		if value == (common.Hash{}) {
			return nil, errors.New("corrupt WQPU hash index")
		}
		out = append(out, value)
	}
	return out, nil
}
