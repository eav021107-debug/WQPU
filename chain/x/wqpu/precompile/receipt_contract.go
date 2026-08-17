package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

const (
	submitReceiptGas uint64 = 1_200_000
	MaxReceiptABIBytes uint64 = 2048
)

var selectorSubmitReceipt = methodSelector("submitReceipt(bytes)")

type ReceiptNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = ReceiptNetworkContract{}

func NewReceiptNetworkContract(config NetworkConfig) ReceiptNetworkContract { return ReceiptNetworkContract{config: config} }
func (ReceiptNetworkContract) Address() common.Address { return Address }
func (ReceiptNetworkContract) Name() string { return Name }

func (c ReceiptNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok {
		switch m {
		case selectorSubmitReceipt:
			return submitReceiptGas
		case selectorPublishProvider:
			return publishProviderGas
		}
	}
	return NewJobNetworkContract(c.config).RequiredGas(input)
}

func decodeSubmitReceipt(input []byte) (ReceiptEnvelope, error) {
	if len(input) < 4 { return ReceiptEnvelope{}, errors.New("missing WQPU method selector") }
	args := input[4:]
	if len(args) < 32 { return ReceiptEnvelope{}, errors.New("truncated submitReceipt arguments") }
	raw, err := decodeDynamicBytes(args, 0, 1, MaxReceiptABIBytes)
	if err != nil { return ReceiptEnvelope{}, err }
	return DecodeReceiptEnvelope(raw)
}

func (c ReceiptNetworkContract) runProviderPublishV2(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil { return nil, errors.New("WQPU precompile requires an EVM execution context") }
	if readOnly { return nil, errors.New("publishProvider cannot run in static context") }
	if contract.Value() != nil && !contract.Value().IsZero() { return nil, errors.New("publishProvider does not accept value") }
	envelope, err := decodePublishProvider(contract.Input)
	if err != nil { return nil, err }
	height, err := currentHeight(evm)
	if err != nil { return nil, err }
	snapshot := evm.StateDB.Snapshot()
	if err := CommitProviderPublishV2(evm.StateDB, envelope, c.config, height); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeBool(true), nil
}

func (c ReceiptNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if contract == nil { return nil, errors.New("WQPU precompile requires a contract context") }
	m, ok := method(contract.Input)
	if !ok { return NewJobNetworkContract(c.config).Run(evm, contract, readOnly) }
	if m == selectorPublishProvider { return c.runProviderPublishV2(evm, contract, readOnly) }
	if m != selectorSubmitReceipt { return NewJobNetworkContract(c.config).Run(evm, contract, readOnly) }
	if evm == nil || evm.StateDB == nil { return nil, errors.New("WQPU precompile requires an EVM execution context") }
	if readOnly { return nil, errors.New("submitReceipt cannot run in static context") }
	if contract.Value() != nil && !contract.Value().IsZero() { return nil, errors.New("submitReceipt does not accept value") }
	envelope, err := decodeSubmitReceipt(contract.Input)
	if err != nil { return nil, err }
	height, err := currentHeight(evm)
	if err != nil { return nil, err }
	snapshot := evm.StateDB.Snapshot()
	if err := CommitAcceptedReceipt(evm.StateDB, envelope, c.config, height); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeBool(true), nil
}

func WithWQPUReceiptNetwork(existing map[common.Address]vm.PrecompiledContract) map[common.Address]vm.PrecompiledContract {
	if existing == nil { existing = make(map[common.Address]vm.PrecompiledContract) }
	if current, exists := existing[Address]; exists { panic("WQPU precompile address collision with " + current.Name()) }
	existing[Address] = NewReceiptNetworkContract(DevNetworkConfig)
	return existing
}
