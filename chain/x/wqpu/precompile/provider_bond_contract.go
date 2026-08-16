package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

const (
	bondProviderGas   uint64 = 350_000
	unbondProviderGas uint64 = 400_000
)

var (
	selectorBondProvider          = methodSelector("bondProvider(bytes32,uint64)")
	selectorUnbondProvider        = methodSelector("unbondProvider(bytes32,uint64)")
	selectorProviderBondCapacity  = methodSelector("providerBondCapacity(bytes32)")
	selectorProviderPriceCapacity = methodSelector("providerPriceCapacity(bytes32)")
	selectorBondedPriceCapacity   = methodSelector("bondedPriceCapacity()")
)

type BondedSettlementNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = BondedSettlementNetworkContract{}

func NewBondedSettlementNetworkContract(config NetworkConfig) BondedSettlementNetworkContract {
	return BondedSettlementNetworkContract{config: config}
}
func (BondedSettlementNetworkContract) Address() common.Address { return Address }
func (BondedSettlementNetworkContract) Name() string { return Name }

func (c BondedSettlementNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok {
		switch m {
		case selectorBondProvider:
			return bondProviderGas
		case selectorUnbondProvider:
			return unbondProviderGas
		case selectorProviderBondCapacity, selectorProviderPriceCapacity, selectorBondedPriceCapacity:
			return networkReadGas
		}
	}
	return NewSettlementNetworkContract(c.config).RequiredGas(input)
}

func decodePeerCapacity(input []byte) (common.Hash, uint64, error) {
	if len(input) != 4+2*32 {
		return common.Hash{}, 0, errors.New("provider bond method requires bytes32 peer id and uint64 capacity")
	}
	peerID, err := decodeHashWord(input[4:36])
	if err != nil {
		return common.Hash{}, 0, err
	}
	capacity, err := decodeABIUint64(input[4:], 1)
	if err != nil {
		return common.Hash{}, 0, err
	}
	if capacity == 0 {
		return common.Hash{}, 0, errors.New("provider bond capacity must be positive")
	}
	return peerID, capacity, nil
}

func decodePeerOnly(input []byte) (common.Hash, error) {
	if len(input) != 36 {
		return common.Hash{}, errors.New("provider bond query requires one bytes32 peer id")
	}
	return decodeHashWord(input[4:])
}

func providerOwnedByCaller(state WordState, peerID common.Hash, caller common.Address) (ProviderRecord, error) {
	provider, exists, err := LoadPeerProvider(state, peerID)
	if err != nil {
		return ProviderRecord{}, err
	}
	if !exists {
		return ProviderRecord{}, errors.New("unknown WQPU peer")
	}
	if provider.Wallet != caller {
		return ProviderRecord{}, errors.New("only the provider wallet may change its WQPU bond")
	}
	return provider, nil
}

func (c BondedSettlementNetworkContract) runBondProvider(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil {
		return nil, errors.New("WQPU precompile requires an EVM execution context")
	}
	if readOnly {
		return nil, errors.New("bondProvider cannot run in static context")
	}
	peerID, capacity, err := decodePeerCapacity(contract.Input)
	if err != nil {
		return nil, err
	}
	provider, err := providerOwnedByCaller(evm.StateDB, peerID, contract.CallerAddress)
	if err != nil {
		return nil, err
	}
	height, err := currentHeight(evm)
	if err != nil {
		return nil, err
	}
	if !provider.ActiveAt(height) {
		return nil, errors.New("cannot bond inactive WQPU peer")
	}
	paymentUnits, err := ProviderBondPaymentUnits(capacity)
	if err != nil {
		return nil, err
	}
	expected, err := PaymentUnitsToNative(paymentUnits)
	if err != nil {
		return nil, err
	}
	if contract.Value() == nil || contract.Value().Cmp(expected) != 0 {
		return nil, errors.New("bondProvider value does not match declared bonded capacity")
	}
	snapshot := evm.StateDB.Snapshot()
	if err := AddProviderBondCapacity(evm.StateDB, peerID, capacity); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeBool(true), nil
}

func (c BondedSettlementNetworkContract) runUnbondProvider(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil {
		return nil, errors.New("WQPU precompile requires an EVM execution context")
	}
	if readOnly {
		return nil, errors.New("unbondProvider cannot run in static context")
	}
	if contract.Value() != nil && !contract.Value().IsZero() {
		return nil, errors.New("unbondProvider does not accept value")
	}
	peerID, capacity, err := decodePeerCapacity(contract.Input)
	if err != nil {
		return nil, err
	}
	if _, err := providerOwnedByCaller(evm.StateDB, peerID, contract.CallerAddress); err != nil {
		return nil, err
	}
	paymentUnits, err := ProviderBondPaymentUnits(capacity)
	if err != nil {
		return nil, err
	}
	native, err := PaymentUnitsToNative(paymentUnits)
	if err != nil {
		return nil, err
	}
	if evm.Context.CanTransfer == nil || evm.Context.Transfer == nil || !evm.Context.CanTransfer(evm.StateDB, Address, native) {
		return nil, errors.New("WQPU precompile native bond balance is insufficient")
	}
	snapshot := evm.StateDB.Snapshot()
	if err := RemoveProviderBondCapacity(evm.StateDB, peerID, capacity); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	evm.Context.Transfer(evm.StateDB, Address, contract.CallerAddress, native)
	return encodeBool(true), nil
}

func (c BondedSettlementNetworkContract) runBondQuery(evm *vm.EVM, contract *vm.Contract, selector [4]byte) ([]byte, error) {
	if evm == nil || evm.StateDB == nil {
		return nil, errors.New("WQPU precompile requires an EVM execution context")
	}
	if contract.Value() != nil && !contract.Value().IsZero() {
		return nil, errors.New("WQPU bond queries do not accept value")
	}
	if selector == selectorBondedPriceCapacity {
		if len(contract.Input) != 4 {
			return nil, errors.New("bondedPriceCapacity takes no arguments")
		}
		height, err := currentHeight(evm)
		if err != nil {
			return nil, err
		}
		capacity, err := AggregateBondedPriceCapacity(evm.StateDB, height)
		if err != nil {
			return nil, err
		}
		return encodeUint256(capacity), nil
	}
	peerID, err := decodePeerOnly(contract.Input)
	if err != nil {
		return nil, err
	}
	if selector == selectorProviderBondCapacity {
		capacity, err := ProviderBondedCapacityUnits(evm.StateDB, peerID)
		if err != nil {
			return nil, err
		}
		return encodeUint256(capacity), nil
	}
	height, err := currentHeight(evm)
	if err != nil {
		return nil, err
	}
	capacity, err := ProviderPriceCapacityUnits(evm.StateDB, peerID, height)
	if err != nil {
		return nil, err
	}
	return encodeUint256(capacity), nil
}

func (c BondedSettlementNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if contract == nil {
		return nil, errors.New("WQPU precompile requires a contract context")
	}
	m, ok := method(contract.Input)
	if !ok {
		return NewSettlementNetworkContract(c.config).Run(evm, contract, readOnly)
	}
	switch m {
	case selectorBondProvider:
		return c.runBondProvider(evm, contract, readOnly)
	case selectorUnbondProvider:
		return c.runUnbondProvider(evm, contract, readOnly)
	case selectorProviderBondCapacity, selectorProviderPriceCapacity, selectorBondedPriceCapacity:
		return c.runBondQuery(evm, contract, m)
	default:
		return NewSettlementNetworkContract(c.config).Run(evm, contract, readOnly)
	}
}
