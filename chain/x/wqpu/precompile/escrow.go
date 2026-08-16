package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/holiman/uint256"
)

// WQPU uses 18 native decimals (awqpu). Protocol payment counters stay uint64
// and use nano-WQPU precision: 1 payment unit = 1e9 awqpu = 1e-9 WQPU.
const NativeUnitsPerPaymentUnit uint64 = 1_000_000_000

func PaymentUnitsToNative(units uint64) (*uint256.Int, error) {
	if units == 0 {
		return uint256.NewInt(0), nil
	}
	value := new(uint256.Int).SetUint64(units)
	factor := uint256.NewInt(NativeUnitsPerPaymentUnit)
	_, overflow := value.MulOverflow(value, factor)
	if overflow {
		return nil, errors.New("WQPU native payment conversion overflow")
	}
	return value, nil
}

func SessionEscrowUnits(state WordState, wallet, session common.Address) (uint64, error) {
	if wallet == (common.Address{}) || session == (common.Address{}) {
		return 0, errors.New("WQPU escrow wallet/session are required")
	}
	return GetUint64(state, "session-escrow", sessionStorageKey(wallet, session))
}

func CreditSessionEscrow(state WordState, wallet, sessionAddress common.Address, units uint64) error {
	if units == 0 {
		return errors.New("WQPU escrow credit must be positive")
	}
	session, exists, err := LoadSession(state, wallet, sessionAddress)
	if err != nil { return err }
	if !exists { return errors.New("unknown WQPU session") }
	current, err := SessionEscrowUnits(state, wallet, sessionAddress)
	if err != nil { return err }
	if units > ^uint64(0)-current { return errors.New("WQPU session escrow overflow") }
	updated := current + units
	if updated > ^uint64(0)-session.SpentUnits || updated+session.SpentUnits > session.Delegation.MaxSpendUnits {
		return errors.New("WQPU escrow exceeds delegated lifetime spend limit")
	}
	return SetUint64(state, "session-escrow", sessionStorageKey(wallet, sessionAddress), updated)
}

func DebitSessionEscrow(state WordState, wallet, sessionAddress common.Address, units uint64) error {
	current, err := SessionEscrowUnits(state, wallet, sessionAddress)
	if err != nil { return err }
	if units > current { return errors.New("WQPU session escrow underflow") }
	return SetUint64(state, "session-escrow", sessionStorageKey(wallet, sessionAddress), current-units)
}

func WithdrawableSessionEscrow(state WordState, wallet, sessionAddress common.Address) (uint64, error) {
	session, exists, err := LoadSession(state, wallet, sessionAddress)
	if err != nil { return 0, err }
	if !exists { return 0, errors.New("unknown WQPU session") }
	escrow, err := SessionEscrowUnits(state, wallet, sessionAddress)
	if err != nil { return 0, err }
	if session.ReservedUnits > escrow { return 0, errors.New("corrupt WQPU escrow reservation accounting") }
	return escrow - session.ReservedUnits, nil
}

func ReserveSessionSpendV2(state WordState, wallet, sessionAddress common.Address, height, amount uint64) error {
	session, err := SessionCanReserveSpend(state, wallet, sessionAddress, height, amount)
	if err != nil { return err }
	escrow, err := SessionEscrowUnits(state, wallet, sessionAddress)
	if err != nil { return err }
	if session.ReservedUnits > escrow || amount > escrow-session.ReservedUnits {
		return errors.New("WQPU session has insufficient funded escrow")
	}
	session.ReservedUnits += amount
	return StoreSession(state, session)
}

func SettleSessionEscrow(state WordState, wallet, sessionAddress common.Address, reservedAmount, actualAmount uint64) error {
	if actualAmount > reservedAmount { return errors.New("WQPU actual settlement exceeds reserved amount") }
	escrow, err := SessionEscrowUnits(state, wallet, sessionAddress)
	if err != nil { return err }
	if actualAmount > escrow { return errors.New("WQPU funded escrow cannot cover settlement") }
	if err := SettleSessionSpend(state, wallet, sessionAddress, reservedAmount, actualAmount); err != nil { return err }
	return DebitSessionEscrow(state, wallet, sessionAddress, actualAmount)
}
