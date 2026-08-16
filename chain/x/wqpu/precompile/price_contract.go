package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

const closePriceEpochGas uint64 = 1_000_000

var (
	selectorClosePriceEpoch = methodSelector("closePriceEpoch()")
	selectorPriceEpoch      = methodSelector("priceEpoch()")
)

type PricedSettlementNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = PricedSettlementNetworkContract{}

func NewPricedSettlementNetworkContract(config NetworkConfig) PricedSettlementNetworkContract {
	return PricedSettlementNetworkContract{config: config}
}
func (PricedSettlementNetworkContract) Address() common.Address { return Address }
func (PricedSettlementNetworkContract) Name() string { return Name }

func (c PricedSettlementNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok {
		switch m {
		case selectorClosePriceEpoch:
			return closePriceEpochGas
		case selectorPriceEpoch:
			return networkReadGas
		}
	}
	return NewBondedSettlementNetworkContract(c.config).RequiredGas(input)
}

func (c PricedSettlementNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil || contract == nil {
		return nil, errors.New("WQPU precompile requires an EVM execution context")
	}
	m, ok := method(contract.Input)
	if !ok {
		return NewBondedSettlementNetworkContract(c.config).Run(evm, contract, readOnly)
	}
	switch m {
	case selectorPriceEpoch:
		if len(contract.Input) != 4 {
			return nil, errors.New("priceEpoch takes no arguments")
		}
		if contract.Value() != nil && !contract.Value().IsZero() {
			return nil, errors.New("priceEpoch does not accept value")
		}
		epoch, err := GetUint64(evm.StateDB, "global", []byte("price-epoch"))
		if err != nil {
			return nil, err
		}
		return encodeUint256(epoch), nil
	case selectorClosePriceEpoch:
		if len(contract.Input) != 4 {
			return nil, errors.New("closePriceEpoch takes no arguments")
		}
		if readOnly {
			return nil, errors.New("closePriceEpoch cannot run in static context")
		}
		if contract.Value() != nil && !contract.Value().IsZero() {
			return nil, errors.New("closePriceEpoch does not accept value")
		}
		height, err := currentHeight(evm)
		if err != nil {
			return nil, err
		}
		snapshot := evm.StateDB.Snapshot()
		price, err := CloseBondedPriceEpoch(evm.StateDB, height)
		if err != nil {
			evm.StateDB.RevertToSnapshot(snapshot)
			return nil, err
		}
		return encodeUint256(price.PricePerMillionUnits), nil
	default:
		return NewBondedSettlementNetworkContract(c.config).Run(evm, contract, readOnly)
	}
}
