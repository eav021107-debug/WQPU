package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

func SessionCanReserveSpend(state WordState, wallet, sessionAddress common.Address, height, amount uint64) (SessionState, error) {
	session, exists, err := LoadSession(state, wallet, sessionAddress)
	if err != nil {
		return SessionState{}, err
	}
	if !exists {
		return SessionState{}, errors.New("unknown WQPU session")
	}
	if session.Revoked || height < session.Delegation.IssuedHeight || height >= session.Delegation.ExpiresHeight {
		return SessionState{}, errors.New("WQPU session is not active")
	}
	walletNonce, err := WalletRevocationNonce(state, wallet)
	if err != nil {
		return SessionState{}, err
	}
	if walletNonce != session.Delegation.RevocationNonce {
		return SessionState{}, errors.New("WQPU session was revoked by wallet nonce")
	}
	if session.Delegation.Permissions&SessionPermJob == 0 {
		return SessionState{}, errors.New("WQPU session cannot reserve jobs")
	}
	if amount == 0 || amount > session.Delegation.MaxJobUnits {
		return SessionState{}, errors.New("job charge is outside WQPU session limit")
	}
	if session.ReservedUnits > ^uint64(0)-session.SpentUnits {
		return SessionState{}, errors.New("WQPU session accounting overflow")
	}
	committed := session.SpentUnits + session.ReservedUnits
	if amount > ^uint64(0)-committed || committed+amount > session.Delegation.MaxSpendUnits {
		return SessionState{}, errors.New("WQPU session total spend limit exceeded")
	}
	return session, nil
}

func ReserveSessionSpend(state WordState, wallet, sessionAddress common.Address, height, amount uint64) error {
	session, err := SessionCanReserveSpend(state, wallet, sessionAddress, height, amount)
	if err != nil {
		return err
	}
	session.ReservedUnits += amount
	return StoreSession(state, session)
}

func SettleSessionSpend(state WordState, wallet, sessionAddress common.Address, reservedAmount, actualAmount uint64) error {
	session, exists, err := LoadSession(state, wallet, sessionAddress)
	if err != nil {
		return err
	}
	if !exists {
		return errors.New("unknown WQPU session")
	}
	if actualAmount > reservedAmount || reservedAmount > session.ReservedUnits {
		return errors.New("WQPU settlement exceeds reserved session spend")
	}
	if actualAmount > ^uint64(0)-session.SpentUnits {
		return errors.New("WQPU session settled spend overflow")
	}
	newSpent := session.SpentUnits + actualAmount
	newReserved := session.ReservedUnits - reservedAmount
	if newReserved > ^uint64(0)-newSpent || newSpent+newReserved > session.Delegation.MaxSpendUnits {
		return errors.New("WQPU settlement exceeds session total limit")
	}
	session.SpentUnits = newSpent
	session.ReservedUnits = newReserved
	return StoreSession(state, session)
}

func ReleaseSessionSpend(state WordState, wallet, sessionAddress common.Address, amount uint64) error {
	session, exists, err := LoadSession(state, wallet, sessionAddress)
	if err != nil {
		return err
	}
	if !exists {
		return errors.New("unknown WQPU session")
	}
	if amount > session.ReservedUnits {
		return errors.New("cannot release more WQPU session spend than reserved")
	}
	session.ReservedUnits -= amount
	return StoreSession(state, session)
}
