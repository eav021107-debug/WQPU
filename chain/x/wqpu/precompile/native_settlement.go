package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/core/vm"
)

// FinalizeJobNative must execute inside an EVM StateDB snapshot owned by the
// caller. It updates WQPU accounting, debits the requester's funded escrow and
// transfers native awqpu from the precompile balance to accepted providers.
func FinalizeJobNative(evm *vm.EVM, jobID [32]byte, height uint64, timedOut bool) (JobSettlement, error) {
	if evm == nil || evm.StateDB == nil { return JobSettlement{}, errors.New("WQPU native settlement requires EVM state") }
	job, exists, err := LoadJob(evm.StateDB, jobID)
	if err != nil { return JobSettlement{}, err }
	if !exists { return JobSettlement{}, errors.New("unknown WQPU job") }
	settlement, err := BuildJobSettlement(evm.StateDB, job, height, timedOut)
	if err != nil { return JobSettlement{}, err }
	escrow, err := SessionEscrowUnits(evm.StateDB, job.Request.RequesterWallet, job.RequesterSession)
	if err != nil { return JobSettlement{}, err }
	if settlement.TotalCharge > escrow { return JobSettlement{}, errors.New("WQPU native escrow cannot cover accepted work") }
	totalNative, err := PaymentUnitsToNative(settlement.TotalCharge)
	if err != nil { return JobSettlement{}, err }
	if settlement.TotalCharge > 0 {
		if evm.Context.CanTransfer == nil || !evm.Context.CanTransfer(evm.StateDB, Address, totalNative) {
			return JobSettlement{}, errors.New("WQPU precompile native balance cannot cover settlement")
		}
	}

	settlement, err = FinalizeJobAccounting(evm.StateDB, jobID, height, timedOut)
	if err != nil { return JobSettlement{}, err }
	if settlement.TotalCharge > 0 {
		if err := DebitSessionEscrow(evm.StateDB, job.Request.RequesterWallet, job.RequesterSession, settlement.TotalCharge); err != nil {
			return JobSettlement{}, err
		}
		for _, payout := range settlement.Payouts {
			amount, err := PaymentUnitsToNative(payout.PaymentUnits)
			if err != nil { return JobSettlement{}, err }
			if err := transferNative(evm, Address, payout.ProviderWallet, amount); err != nil {
				return JobSettlement{}, err
			}
		}
	}
	return settlement, nil
}
