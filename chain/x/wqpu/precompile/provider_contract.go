package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

const (
	publishProviderGas        uint64 = 900_000
	MaxProviderEnvelopeBytes uint64 = 8192
)

var selectorPublishProvider = methodSelector("publishProvider(bytes)")

// ProviderNetworkContract extends wallet-session authorization + read-only
// discovery with signed provider heartbeats. It remains one native precompile
// at 0x0900; no separate discovery server exists.
type ProviderNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = ProviderNetworkContract{}

func NewProviderNetworkContract(config NetworkConfig) ProviderNetworkContract {
	return ProviderNetworkContract{config: config}
}

func (ProviderNetworkContract) Address() common.Address { return Address }
func (ProviderNetworkContract) Name() string           { return Name }

func (c ProviderNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok && m == selectorPublishProvider {
		return publishProviderGas
	}
	return NewStatefulNetworkContract(c.config).RequiredGas(input)
}

func decodePublishProvider(input []byte) (ProviderPublishEnvelope, error) {
	if len(input) < 4 {
		return ProviderPublishEnvelope{}, errors.New("missing WQPU method selector")
	}
	args := input[4:]
	if len(args) < 32 {
		return ProviderPublishEnvelope{}, errors.New("truncated publishProvider arguments")
	}
	raw, err := decodeDynamicBytes(args, 0, 1, MaxProviderEnvelopeBytes)
	if err != nil {
		return ProviderPublishEnvelope{}, err
	}
	return DecodeProviderPublishEnvelope(raw)
}

func (c ProviderNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if contract == nil {
		return nil, errors.New("WQPU precompile requires a contract context")
	}
	m, ok := method(contract.Input)
	if !ok || m != selectorPublishProvider {
		return NewStatefulNetworkContract(c.config).Run(evm, contract, readOnly)
	}
	if evm == nil || evm.StateDB == nil {
		return nil, errors.New("WQPU precompile requires an EVM execution context")
	}
	if readOnly {
		return nil, errors.New("publishProvider cannot run in static context")
	}
	if contract.Value() != nil && !contract.Value().IsZero() {
		return nil, errors.New("publishProvider does not accept value")
	}
	envelope, err := decodePublishProvider(contract.Input)
	if err != nil {
		return nil, err
	}
	height, err := currentHeight(evm)
	if err != nil {
		return nil, err
	}

	snapshot := evm.StateDB.Snapshot()
	if err := CommitProviderPublishV2(evm.StateDB, envelope, c.config, height); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeBool(true), nil
}

func WithWQPUProviderNetwork(existing map[common.Address]vm.PrecompiledContract) map[common.Address]vm.PrecompiledContract {
	if existing == nil {
		existing = make(map[common.Address]vm.PrecompiledContract)
	}
	if current, exists := existing[Address]; exists {
		panic("WQPU precompile address collision with " + current.Name())
	}
	existing[Address] = NewProviderNetworkContract(DevNetworkConfig)
	return existing
}
