package precompile

import (
	"errors"
	"math/big"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
	"github.com/ethereum/go-ethereum/crypto"
)

const (
	Name = "wqpu"
	ProtocolVersion uint64 = 1
	InitialGlobalPrice uint64 = 1000
	readGas uint64 = 2_500
)

var (
	selectorProtocolVersion = selector("protocolVersion()")
	selectorGlobalPrice     = selector("globalPrice()")
	selectorProviderCount   = selector("providerCount()")
	selectorProviderAt      = selector("providerAt(uint256)")
)

type Contract struct{}

func New() Contract { return Contract{} }

func (Contract) Address() common.Address { return Address }
func (Contract) Name() string { return Name }

func selector(signature string) [4]byte {
	hash := crypto.Keccak256([]byte(signature))
	var out [4]byte
	copy(out[:], hash[:4])
	return out
}

func method(input []byte) ([4]byte, bool) {
	var out [4]byte
	if len(input) < 4 {
		return out, false
	}
	copy(out[:], input[:4])
	return out, true
}

func (Contract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if !ok {
		return readGas
	}
	switch m {
	case selectorProtocolVersion:
		return 500
	case selectorGlobalPrice, selectorProviderCount, selectorProviderAt:
		return readGas
	default:
		return readGas
	}
}

func encodeUint256(value uint64) []byte {
	return common.LeftPadBytes(new(big.Int).SetUint64(value).Bytes(), 32)
}

func encodeAddress(value common.Address) []byte {
	return common.LeftPadBytes(value.Bytes(), 32)
}

func decodeUint256Word(word []byte) (uint64, error) {
	if len(word) != 32 {
		return 0, errors.New("WQPU uint256 argument must be one ABI word")
	}
	for _, b := range word[:24] {
		if b != 0 {
			return 0, errors.New("WQPU uint256 argument exceeds uint64")
		}
	}
	return new(big.Int).SetBytes(word).Uint64(), nil
}

func currentGlobalPrice(state WordState) (uint64, error) {
	price, err := GetUint64(state, "global", []byte("price-per-million"))
	if err != nil {
		return 0, err
	}
	if price == 0 {
		return InitialGlobalPrice, nil
	}
	return price, nil
}

func providerCount(state WordState) (uint64, error) {
	return hashToUint64(state.GetState(Address, AddressIndexCountSlot("providers")))
}

func providerAt(state WordState, index uint64) (common.Address, error) {
	if index == 0 {
		return common.Address{}, errors.New("provider index is 1-based")
	}
	count, err := providerCount(state)
	if err != nil {
		return common.Address{}, err
	}
	if index > count {
		return common.Address{}, errors.New("provider index out of range")
	}
	address, err := addressFromHash(state.GetState(Address, AddressIndexItemSlot("providers", index)))
	if err != nil {
		return common.Address{}, err
	}
	if address == (common.Address{}) {
		return common.Address{}, errors.New("corrupt provider index")
	}
	return address, nil
}

func (Contract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil || contract == nil {
		return nil, errors.New("WQPU precompile requires an EVM execution context")
	}
	if contract.Value() != nil && !contract.Value().IsZero() {
		return nil, errors.New("WQPU read methods do not accept value")
	}
	m, ok := method(contract.Input)
	if !ok {
		return nil, errors.New("missing WQPU method selector")
	}

	switch m {
	case selectorProtocolVersion:
		if len(contract.Input) != 4 {
			return nil, errors.New("protocolVersion takes no arguments")
		}
		return encodeUint256(ProtocolVersion), nil
	case selectorGlobalPrice:
		if len(contract.Input) != 4 {
			return nil, errors.New("globalPrice takes no arguments")
		}
		price, err := currentGlobalPrice(evm.StateDB)
		if err != nil {
			return nil, err
		}
		return encodeUint256(price), nil
	case selectorProviderCount:
		if len(contract.Input) != 4 {
			return nil, errors.New("providerCount takes no arguments")
		}
		count, err := providerCount(evm.StateDB)
		if err != nil {
			return nil, err
		}
		return encodeUint256(count), nil
	case selectorProviderAt:
		if len(contract.Input) != 36 {
			return nil, errors.New("providerAt requires one uint256 argument")
		}
		index, err := decodeUint256Word(contract.Input[4:])
		if err != nil {
			return nil, err
		}
		address, err := providerAt(evm.StateDB, index)
		if err != nil {
			return nil, err
		}
		return encodeAddress(address), nil
	default:
		return nil, errors.New("unknown WQPU method selector")
	}
}
