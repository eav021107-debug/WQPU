package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

func ActiveSessionForPermission(state WordState, wallet, sessionAddress common.Address, height, permission uint64) (SessionState, error) {
	if !singlePermission(permission) {
		return SessionState{}, errors.New("WQPU permission must be one known bit")
	}
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
	if session.Delegation.Permissions&permission == 0 {
		return SessionState{}, errors.New("WQPU session permission denied")
	}
	return session, nil
}
