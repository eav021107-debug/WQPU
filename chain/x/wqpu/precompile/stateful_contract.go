package precompile

import (
	"encoding/hex"
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
)

const authorizeSessionGas uint64 = 500_000

var selectorAuthorizeSession = methodSelector("authorizeSession(address,address,uint64,uint64,uint64,uint64,uint64,uint64,bytes)")

type NetworkConfig struct {
	WQPUChainID string
	EVMChainID  uint64
}

var DevNetworkConfig = NetworkConfig{WQPUChainID: "wqpu-dev-1", EVMChainID: 711711}

type StatefulNetworkContract struct {
	config NetworkConfig
}

var _ vm.PrecompiledContract = StatefulNetworkContract{}

func NewStatefulNetworkContract(config NetworkConfig) StatefulNetworkContract {
	return StatefulNetworkContract{config: config}
}

func (StatefulNetworkContract) Address() common.Address { return Address }
func (StatefulNetworkContract) Name() string { return Name }

func (c StatefulNetworkContract) RequiredGas(input []byte) uint64 {
	m, ok := method(input)
	if ok && m == selectorAuthorizeSession {
		return authorizeSessionGas
	}
	return NewNetworkContract().RequiredGas(input)
}

func currentHeight(evm *vm.EVM) (uint64, error) {
	if evm == nil || evm.Context.BlockNumber == nil || evm.Context.BlockNumber.Sign() < 0 || evm.Context.BlockNumber.BitLen() > 64 {
		return 0, errors.New("invalid WQPU block height")
	}
	return evm.Context.BlockNumber.Uint64(), nil
}

func decodeAuthorizeSession(input []byte, config NetworkConfig) (SessionDelegation, string, error) {
	if config.WQPUChainID == "" || config.EVMChainID == 0 {
		return SessionDelegation{}, "", errors.New("invalid WQPU network configuration")
	}
	if len(input) < 4 {
		return SessionDelegation{}, "", errors.New("missing WQPU method selector")
	}
	args := input[4:]
	if len(args) < 9*32 {
		return SessionDelegation{}, "", errors.New("truncated authorizeSession arguments")
	}
	wallet, err := decodeABIAddress(args, 0)
	if err != nil { return SessionDelegation{}, "", err }
	session, err := decodeABIAddress(args, 1)
	if err != nil { return SessionDelegation{}, "", err }
	issued, err := decodeABIUint64(args, 2)
	if err != nil { return SessionDelegation{}, "", err }
	expires, err := decodeABIUint64(args, 3)
	if err != nil { return SessionDelegation{}, "", err }
	maxSpend, err := decodeABIUint64(args, 4)
	if err != nil { return SessionDelegation{}, "", err }
	maxJob, err := decodeABIUint64(args, 5)
	if err != nil { return SessionDelegation{}, "", err }
	revocation, err := decodeABIUint64(args, 6)
	if err != nil { return SessionDelegation{}, "", err }
	permissions, err := decodeABIUint64(args, 7)
	if err != nil { return SessionDelegation{}, "", err }
	signature, err := decodeDynamicBytes(args, 8, 9, 65)
	if err != nil { return SessionDelegation{}, "", err }
	if len(signature) != 65 {
		return SessionDelegation{}, "", errors.New("wallet authorization must contain a 65-byte EVM signature")
	}
	delegation := SessionDelegation{
		WQPUChainID: config.WQPUChainID,
		Wallet: wallet,
		Session: session,
		IssuedHeight: issued,
		ExpiresHeight: expires,
		MaxSpendUnits: maxSpend,
		MaxJobUnits: maxJob,
		RevocationNonce: revocation,
		Permissions: permissions,
		ProtocolVersion: uint32(ProtocolVersion),
	}
	if err := delegation.Validate(); err != nil {
		return SessionDelegation{}, "", err
	}
	return delegation, "0x" + hex.EncodeToString(signature), nil
}

func (c StatefulNetworkContract) Run(evm *vm.EVM, contract *vm.Contract, readOnly bool) ([]byte, error) {
	if contract == nil {
		return nil, errors.New("WQPU precompile requires a contract context")
	}
	m, ok := method(contract.Input)
	if !ok || m != selectorAuthorizeSession {
		return NewNetworkContract().Run(evm, contract, readOnly)
	}
	if evm == nil || evm.StateDB == nil {
		return nil, errors.New("WQPU precompile requires an EVM execution context")
	}
	if readOnly {
		return nil, errors.New("authorizeSession cannot run in static context")
	}
	if contract.Value() != nil && !contract.Value().IsZero() {
		return nil, errors.New("authorizeSession does not accept value")
	}
	delegation, signature, err := decodeAuthorizeSession(contract.Input, c.config)
	if err != nil {
		return nil, err
	}
	height, err := currentHeight(evm)
	if err != nil {
		return nil, err
	}

	snapshot := evm.StateDB.Snapshot()
	if err := AuthorizeSession(evm.StateDB, delegation, c.config.EVMChainID, height, signature); err != nil {
		evm.StateDB.RevertToSnapshot(snapshot)
		return nil, err
	}
	return encodeBool(true), nil
}

func WithWQPUStatefulNetwork(existing map[common.Address]vm.PrecompiledContract) map[common.Address]vm.PrecompiledContract {
	if existing == nil {
		existing = make(map[common.Address]vm.PrecompiledContract)
	}
	if current, exists := existing[Address]; exists {
		panic("WQPU precompile address collision with " + current.Name())
	}
	existing[Address] = NewStatefulNetworkContract(DevNetworkConfig)
	return existing
}
