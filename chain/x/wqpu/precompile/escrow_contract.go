package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

const (
	fundSessionGas     uint64 = 250_000
	withdrawSessionGas uint64 = 300_000
)

var (
	selectorFundSession     = methodSelector("fundSession(address,address,uint64)")
	selectorWithdrawSession = methodSelector("withdrawSession(address,address,uint64)")
)

type EconomicNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = EconomicNetworkContract{}

func NewEconomicNetworkContract(config NetworkConfig) EconomicNetworkContract { return EconomicNetworkContract{config: config} }
func (EconomicNetworkContract) Address() common.Address { return Address }
func (EconomicNetworkContract) Name() string { return Name }

func (c EconomicNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok {
		switch m {
		case selectorFundSession:
			return fundSessionGas
		case selectorWithdrawSession:
			return withdrawSessionGas
		case selectorReserveJob:
			return reserveJobGas
		}
	}
	return NewReceiptNetworkContract(c.config).RequiredGas(input)
}

func decodeSessionMoney(input []byte) (common.Address, common.Address, uint64, error) {
	if len(input) != 4+3*32 { return common.Address{}, common.Address{}, 0, errors.New("invalid WQPU session money ABI length") }
	args := input[4:]
	wallet, err := decodeABIAddress(args, 0); if err != nil { return common.Address{}, common.Address{}, 0, err }
	session, err := decodeABIAddress(args, 1); if err != nil { return common.Address{}, common.Address{}, 0, err }
	units, err := decodeABIUint64(args, 2); if err != nil { return common.Address{}, common.Address{}, 0, err }
	if units == 0 { return common.Address{}, common.Address{}, 0, errors.New("WQPU escrow amount must be positive") }
	return wallet, session, units, nil
}

func (c EconomicNetworkContract) runFundSession(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil { return nil, errors.New("WQPU precompile requires an EVM execution context") }
	if readOnly { return nil, errors.New("fundSession cannot run in static context") }
	wallet, session, units, err := decodeSessionMoney(contract.Input)
	if err != nil { return nil, err }
	if contract.Caller() != wallet { return nil, errors.New("only the wallet may fund its WQPU session") }
	expected, err := PaymentUnitsToNative(units)
	if err != nil { return nil, err }
	if contract.Value() == nil || contract.Value().Cmp(expected) != 0 { return nil, errors.New("fundSession value does not match declared WQPU payment units") }
	height, err := currentHeight(evm)
	if err != nil { return nil, err }
	if _, err := ActiveSessionForPermission(evm.StateDB, wallet, session, height, SessionPermJob); err != nil { return nil, err }
	snapshot := evm.StateDB.Snapshot()
	if err := CreditSessionEscrow(evm.StateDB, wallet, session, units); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeBool(true), nil
}

func (c EconomicNetworkContract) runWithdrawSession(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil { return nil, errors.New("WQPU precompile requires an EVM execution context") }
	if readOnly { return nil, errors.New("withdrawSession cannot run in static context") }
	if contract.Value() != nil && !contract.Value().IsZero() { return nil, errors.New("withdrawSession does not accept value") }
	wallet, session, units, err := decodeSessionMoney(contract.Input)
	if err != nil { return nil, err }
	if contract.Caller() != wallet { return nil, errors.New("only the wallet may withdraw its WQPU escrow") }
	withdrawable, err := WithdrawableSessionEscrow(evm.StateDB, wallet, session)
	if err != nil { return nil, err }
	if units > withdrawable { return nil, errors.New("withdrawal would consume reserved WQPU escrow") }
	native, err := PaymentUnitsToNative(units)
	if err != nil { return nil, err }
	snapshot := evm.StateDB.Snapshot()
	if err := DebitSessionEscrow(evm.StateDB, wallet, session, units); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	if err := transferNative(evm, Address, wallet, native); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeBool(true), nil
}

func (c EconomicNetworkContract) runReserveJobV3(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if evm == nil || evm.StateDB == nil { return nil, errors.New("WQPU precompile requires an EVM execution context") }
	if readOnly { return nil, errors.New("reserveJob cannot run in static context") }
	if contract.Value() != nil && !contract.Value().IsZero() { return nil, errors.New("reserveJob does not accept value") }
	envelope, err := decodeReserveJob(contract.Input)
	if err != nil { return nil, err }
	height, err := currentHeight(evm)
	if err != nil { return nil, err }
	snapshot := evm.StateDB.Snapshot()
	job, err := CommitSignedJobReservationV3(evm.StateDB, envelope, c.config, height)
	if err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return job.Request.JobID.Bytes(), nil
}

func (c EconomicNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if contract == nil { return nil, errors.New("WQPU precompile requires a contract context") }
	m, ok := method(contract.Input)
	if !ok { return NewReceiptNetworkContract(c.config).Run(evm, contract, readOnly) }
	switch m {
	case selectorFundSession:
		return c.runFundSession(evm, contract, readOnly)
	case selectorWithdrawSession:
		return c.runWithdrawSession(evm, contract, readOnly)
	case selectorReserveJob:
		return c.runReserveJobV3(evm, contract, readOnly)
	default:
		return NewReceiptNetworkContract(c.config).Run(evm, contract, readOnly)
	}
}

func WithWQPUEconomicNetwork(existing map[common.Address]vm.PrecompiledContract) map[common.Address]vm.PrecompiledContract {
	if existing == nil { existing = make(map[common.Address]vm.PrecompiledContract) }
	if current, exists := existing[Address]; exists { panic("WQPU precompile address collision with " + current.Name()) }
	existing[Address] = NewEconomicNetworkContract(DevNetworkConfig)
	return existing
}
