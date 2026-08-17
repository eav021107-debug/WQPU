package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
	"github.com/ethereum/go-ethereum/crypto"
)

const (
	networkReadGas uint64 = 3_500
	providerRecordReadGas uint64 = 150_000
)

var (
	selectorPeerCount      = methodSelector("peerCount()")
	selectorPeerAt         = methodSelector("peerAt(uint256)")
	selectorProviderRecord = methodSelector("providerRecord(bytes32)")
	selectorProviderActive = methodSelector("providerActive(bytes32)")
)

// NetworkContract is the target v1 WQPU precompile surface. The earlier
// Contract type remains only as a small integration probe while this surface
// is hardened.
type NetworkContract struct{}

var _ vm.PrecompiledContract = NetworkContract{}

func NewNetworkContract() NetworkContract { return NetworkContract{} }

func (NetworkContract) Address() common.Address { return Address }
func (NetworkContract) Name() string { return Name }

func methodSelector(signature string) [4]byte {
	hash := crypto.Keccak256([]byte(signature))
	var out [4]byte
	copy(out[:], hash[:4])
	return out
}

func (NetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if !ok {
		return networkReadGas
	}
	switch m {
	case selectorProtocolVersion:
		return 500
	case selectorGlobalPrice, selectorPeerCount, selectorPeerAt, selectorProviderActive:
		return networkReadGas
	case selectorProviderRecord:
		return providerRecordReadGas
	default:
		return networkReadGas
	}
}

func peerCount(state WordState) (uint64, error) {
	if state == nil {
		return 0, errors.New("nil WQPU state")
	}
	return hashToUint64(state.GetState(Address, HashIndexCountSlot(providerPeerIndex)))
}

func peerAt(state WordState, index uint64) (common.Hash, error) {
	if state == nil {
		return common.Hash{}, errors.New("nil WQPU state")
	}
	if index == 0 {
		return common.Hash{}, errors.New("WQPU peer index is 1-based")
	}
	count, err := peerCount(state)
	if err != nil {
		return common.Hash{}, err
	}
	if index > count {
		return common.Hash{}, errors.New("WQPU peer index out of range")
	}
	peerID := state.GetState(Address, HashIndexItemSlot(providerPeerIndex, index))
	if peerID == (common.Hash{}) {
		return common.Hash{}, errors.New("corrupt WQPU peer index")
	}
	return peerID, nil
}

func decodeHashWord(word []byte) (common.Hash, error) {
	if len(word) != common.HashLength {
		return common.Hash{}, errors.New("WQPU bytes32 argument must contain one ABI word")
	}
	value := common.BytesToHash(word)
	if value == (common.Hash{}) {
		return common.Hash{}, errors.New("zero WQPU bytes32 argument")
	}
	return value, nil
}

func encodeDynamicBytes(data []byte) []byte {
	padded := ((len(data) + 31) / 32) * 32
	out := make([]byte, 64+padded)
	out[31] = 32
	length := encodeUint256(uint64(len(data)))
	copy(out[32:64], length)
	copy(out[64:], data)
	return out
}

func encodeBool(value bool) []byte {
	out := make([]byte, 32)
	if value {
		out[31] = 1
	}
	return out
}

func (NetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
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
		if err != nil { return nil, err }
		return encodeUint256(price), nil
	case selectorPeerCount:
		if len(contract.Input) != 4 {
			return nil, errors.New("peerCount takes no arguments")
		}
		count, err := peerCount(evm.StateDB)
		if err != nil { return nil, err }
		return encodeUint256(count), nil
	case selectorPeerAt:
		if len(contract.Input) != 36 {
			return nil, errors.New("peerAt requires one uint256 argument")
		}
		index, err := decodeUint256Word(contract.Input[4:])
		if err != nil { return nil, err }
		peerID, err := peerAt(evm.StateDB, index)
		if err != nil { return nil, err }
		return peerID.Bytes(), nil
	case selectorProviderRecord, selectorProviderActive:
		if len(contract.Input) != 36 {
			return nil, errors.New("provider method requires one bytes32 peer id")
		}
		peerID, err := decodeHashWord(contract.Input[4:])
		if err != nil { return nil, err }
		provider, exists, err := LoadPeerProvider(evm.StateDB, peerID)
		if err != nil { return nil, err }
		if !exists {
			return nil, errors.New("unknown WQPU peer")
		}
		if m == selectorProviderRecord {
			encoded, err := EncodeProvider(provider)
			if err != nil { return nil, err }
			return encodeDynamicBytes(encoded), nil
		}
		if evm.Context.BlockNumber == nil || evm.Context.BlockNumber.Sign() < 0 || evm.Context.BlockNumber.BitLen() > 64 {
			return nil, errors.New("invalid WQPU block height")
		}
		return encodeBool(provider.ActiveAt(evm.Context.BlockNumber.Uint64())), nil
	default:
		return nil, errors.New("unknown WQPU method selector")
	}
}

func WithWQPUNetwork(existing map[common.Address]vm.PrecompiledContract) map[common.Address]vm.PrecompiledContract {
	if existing == nil {
		existing = make(map[common.Address]vm.PrecompiledContract)
	}
	if current, exists := existing[Address]; exists {
		panic("WQPU precompile address collision with " + current.Name())
	}
	existing[Address] = NewNetworkContract()
	return existing
}
