package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

var selectorPeerControlSession = methodSelector("peerControlSession(bytes32)")

type RegistrySettlementNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = RegistrySettlementNetworkContract{}

func NewRegistrySettlementNetworkContract(config NetworkConfig) RegistrySettlementNetworkContract {
	return RegistrySettlementNetworkContract{config: config}
}
func (RegistrySettlementNetworkContract) Address() common.Address { return Address }
func (RegistrySettlementNetworkContract) Name() string { return Name }

func (c RegistrySettlementNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok && m == selectorPeerControlSession {
		return networkReadGas
	}
	return NewPricedSettlementNetworkContract(c.config).RequiredGas(input)
}

func peerControlSessionForRegistry(state WordState, peerID common.Hash) (common.Address, error) {
	if _, exists, err := LoadPeerProvider(state, peerID); err != nil {
		return common.Address{}, err
	} else if !exists {
		return common.Address{}, errors.New("unknown WQPU peer")
	}
	session, exists, err := LoadPeerControlSession(state, peerID)
	if err != nil {
		return common.Address{}, err
	}
	if !exists {
		return common.Address{}, errors.New("WQPU peer has no control session")
	}
	return session, nil
}

func (c RegistrySettlementNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if contract == nil {
		return nil, errors.New("WQPU precompile requires a contract context")
	}
	m, ok := method(contract.Input)
	if !ok || m != selectorPeerControlSession {
		return NewPricedSettlementNetworkContract(c.config).Run(evm, contract, readOnly)
	}
	if evm == nil || evm.StateDB == nil {
		return nil, errors.New("WQPU precompile requires an EVM execution context")
	}
	if contract.Value() != nil && !contract.Value().IsZero() {
		return nil, errors.New("peerControlSession does not accept value")
	}
	if len(contract.Input) != 36 {
		return nil, errors.New("peerControlSession requires one bytes32 peer id")
	}
	peerID, err := decodeHashWord(contract.Input[4:])
	if err != nil {
		return nil, err
	}
	session, err := peerControlSessionForRegistry(evm.StateDB, peerID)
	if err != nil {
		return nil, err
	}
	return encodeAddress(session), nil
}
