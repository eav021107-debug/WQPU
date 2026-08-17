package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/tracing"
	"github.com/ethereum/go-ethereum/core/vm"
	"github.com/holiman/uint256"
)

// transferNative moves native WQPU inside the active EVM StateDB. Cosmos EVM's
// BlockContext.Transfer currently requires its private active chain-rules value,
// which custom precompiles cannot read. The consensus balance mutation performed
// by core.Transfer is exactly SubBalance + AddBalance; the extra fork-dependent
// branch only emits a transfer log on newer forks and does not change balances.
func transferNative(evm *vm.EVM, from, to common.Address, amount *uint256.Int) error {
	if evm == nil || evm.StateDB == nil || amount == nil {
		return errors.New("WQPU native transfer requires EVM state and amount")
	}
	if amount.IsZero() || from == to {
		return nil
	}
	if evm.Context.CanTransfer == nil || !evm.Context.CanTransfer(evm.StateDB, from, amount) {
		return errors.New("insufficient native WQPU balance")
	}
	evm.StateDB.SubBalance(from, amount, tracing.BalanceChangeTransfer)
	evm.StateDB.AddBalance(to, amount, tracing.BalanceChangeTransfer)
	return nil
}
