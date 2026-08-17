package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

// RemoveIndexedAddress removes an address in O(1) storage operations by moving
// the last item into its slot. Enumeration stays dense and bounded by the
// currently indexed set rather than accumulating tombstones forever.
func RemoveIndexedAddress(state WordState, namespace string, address common.Address) (bool, error) {
	if state == nil || address == (common.Address{}) {
		return false, errors.New("valid state and non-zero address required")
	}

	reverseSlot := AddressIndexReverseSlot(namespace, address)
	index, err := hashToUint64(state.GetState(Address, reverseSlot))
	if err != nil {
		return false, err
	}
	if index == 0 {
		return false, nil
	}
	count, err := hashToUint64(state.GetState(Address, AddressIndexCountSlot(namespace)))
	if err != nil {
		return false, err
	}
	if count == 0 || index > count {
		return false, errors.New("corrupt WQPU address index")
	}

	if index != count {
		lastHash := state.GetState(Address, AddressIndexItemSlot(namespace, count))
		lastAddress, err := addressFromHash(lastHash)
		if err != nil || lastAddress == (common.Address{}) {
			return false, errors.New("corrupt WQPU last address index entry")
		}
		state.SetState(Address, AddressIndexItemSlot(namespace, index), lastHash)
		state.SetState(Address, AddressIndexReverseSlot(namespace, lastAddress), uint64Hash(index))
	}

	state.SetState(Address, AddressIndexItemSlot(namespace, count), common.Hash{})
	state.SetState(Address, reverseSlot, common.Hash{})
	state.SetState(Address, AddressIndexCountSlot(namespace), uint64Hash(count-1))
	return true, nil
}
