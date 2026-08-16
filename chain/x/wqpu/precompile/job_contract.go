package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

const (
	reserveJobGas uint64 = 1_500_000
	MaxJobEnvelopeBytes uint64 = 8192
)

var selectorReserveJob = methodSelector("reserveJob(bytes)")

type JobNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = JobNetworkContract{}

func NewJobNetworkContract(config NetworkConfig) JobNetworkContract { return JobNetworkContract{config: config} }
func (JobNetworkContract) Address() common.Address { return Address }
func (JobNetworkContract) Name() string { return Name }

func (c JobNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok && m == selectorReserveJob { return reserveJobGas }
	return NewProviderNetworkContract(c.config).RequiredGas(input)
}

func decodeReserveJob(input []byte) (JobReserveEnvelope, error) {
	if len(input) < 4 { return JobReserveEnvelope{}, errors.New("missing WQPU method selector") }
	args := input[4:]
	if len(args) < 32 { return JobReserveEnvelope{}, errors.New("truncated reserveJob arguments") }
	raw, err := decodeDynamicBytes(args, 0, 1, MaxJobEnvelopeBytes)
	if err != nil { return JobReserveEnvelope{}, err }
	return DecodeJobReserveEnvelope(raw)
}

func (c JobNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if contract == nil { return nil, errors.New("WQPU precompile requires a contract context") }
	m, ok := method(contract.Input)
	if !ok || m != selectorReserveJob { return NewProviderNetworkContract(c.config).Run(evm, contract, readOnly) }
	if evm == nil || evm.StateDB == nil { return nil, errors.New("WQPU precompile requires an EVM execution context") }
	if readOnly { return nil, errors.New("reserveJob cannot run in static context") }
	if contract.Value() != nil && !contract.Value().IsZero() { return nil, errors.New("reserveJob does not accept value") }
	envelope, err := decodeReserveJob(contract.Input)
	if err != nil { return nil, err }
	height, err := currentHeight(evm)
	if err != nil { return nil, err }
	snapshot := evm.StateDB.Snapshot()
	job, err := CommitSignedJobReservation(evm.StateDB, envelope, c.config, height)
	if err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return job.Request.JobID.Bytes(), nil
}

func WithWQPUJobNetwork(existing map[common.Address]vm.PrecompiledContract) map[common.Address]vm.PrecompiledContract {
	if existing == nil { existing = make(map[common.Address]vm.PrecompiledContract) }
	if current, exists := existing[Address]; exists { panic("WQPU precompile address collision with " + current.Name()) }
	existing[Address] = NewJobNetworkContract(DevNetworkConfig)
	return existing
}
