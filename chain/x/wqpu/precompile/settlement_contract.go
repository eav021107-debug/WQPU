package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

const (
	finalizeJobGas uint64 = 2_000_000
	timeoutJobGas  uint64 = 2_000_000
	MaxFinalizeABIBytes uint64 = 512
)

var (
	selectorFinalizeJob = methodSelector("finalizeJob(bytes)")
	selectorTimeoutJob  = methodSelector("timeoutJob(bytes32)")
)

type SettlementNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = SettlementNetworkContract{}

func NewSettlementNetworkContract(config NetworkConfig) SettlementNetworkContract { return SettlementNetworkContract{config: config} }
func (SettlementNetworkContract) Address() common.Address { return Address }
func (SettlementNetworkContract) Name() string { return Name }

func (c SettlementNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok {
		switch m {
		case selectorFinalizeJob:
			return finalizeJobGas
		case selectorTimeoutJob:
			return timeoutJobGas
		}
	}
	return NewEconomicNetworkContract(c.config).RequiredGas(input)
}

func decodeFinalizeJob(input []byte) (FinalizeEnvelope, error) {
	if len(input) < 4 { return FinalizeEnvelope{}, errors.New("missing WQPU method selector") }
	raw, err := decodeDynamicBytes(input[4:], 0, 1, MaxFinalizeABIBytes)
	if err != nil { return FinalizeEnvelope{}, err }
	return DecodeFinalizeEnvelope(raw)
}

func decodeTimeoutJob(input []byte) (common.Hash, error) {
	if len(input) != 36 { return common.Hash{}, errors.New("timeoutJob requires one bytes32 job id") }
	return decodeHashWord(input[4:])
}

func (c SettlementNetworkContract) runFinalize(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil { return nil, errors.New("WQPU precompile requires an EVM execution context") }
	if readOnly { return nil, errors.New("finalizeJob cannot run in static context") }
	if contract.Value() != nil && !contract.Value().IsZero() { return nil, errors.New("finalizeJob does not accept value") }
	envelope, err := decodeFinalizeJob(contract.Input)
	if err != nil { return nil, err }
	height, err := currentHeight(evm)
	if err != nil { return nil, err }
	action, err := VerifyFinalizeEnvelope(evm.StateDB, envelope, c.config, height)
	if err != nil { return nil, err }
	snapshot := evm.StateDB.Snapshot()
	settlement, err := FinalizeJobNative(evm, envelope.JobID, height, false)
	if err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	if err := AdvanceSessionActionNonce(evm.StateDB, envelope.Wallet, envelope.Session, action.ActionNonce); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeUint256(settlement.TotalCharge), nil
}

func (c SettlementNetworkContract) runTimeout(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil { return nil, errors.New("WQPU precompile requires an EVM execution context") }
	if readOnly { return nil, errors.New("timeoutJob cannot run in static context") }
	if contract.Value() != nil && !contract.Value().IsZero() { return nil, errors.New("timeoutJob does not accept value") }
	jobID, err := decodeTimeoutJob(contract.Input)
	if err != nil { return nil, err }
	height, err := currentHeight(evm)
	if err != nil { return nil, err }
	snapshot := evm.StateDB.Snapshot()
	settlement, err := FinalizeJobNative(evm, jobID, height, true)
	if err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeUint256(settlement.TotalCharge), nil
}

func (c SettlementNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if contract == nil { return nil, errors.New("WQPU precompile requires a contract context") }
	m, ok := method(contract.Input)
	if !ok { return NewEconomicNetworkContract(c.config).Run(evm, contract, readOnly) }
	switch m {
	case selectorFinalizeJob:
		return c.runFinalize(evm, contract, readOnly)
	case selectorTimeoutJob:
		return c.runTimeout(evm, contract, readOnly)
	default:
		return NewEconomicNetworkContract(c.config).Run(evm, contract, readOnly)
	}
}

func WithWQPUSettlementNetwork(existing map[common.Address]vm.PrecompiledContract) map[common.Address]vm.PrecompiledContract {
	if existing == nil { existing = make(map[common.Address]vm.PrecompiledContract) }
	if current, exists := existing[Address]; exists { panic("WQPU precompile address collision with " + current.Name()) }
	existing[Address] = NewBondedSettlementNetworkContract(DevNetworkConfig)
	return existing
}
