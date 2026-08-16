package service

import (
	"errors"

	"github.com/eav021107-debug/WQPU/chain/x/wqpu/auth"
	"github.com/eav021107-debug/WQPU/chain/x/wqpu/kernel"
)

// AuthorizeWalletSession is the only transaction-boundary operation that should
// turn a wallet delegation into live WQPU chain state. Signature verification
// happens before any mutation.
func AuthorizeWalletSession(
	state *kernel.State,
	delegation kernel.SessionDelegation,
	evmChainID uint64,
	walletSignature string,
) error {
	if state == nil {
		return errors.New("nil WQPU state")
	}
	if err := auth.VerifySessionSignature(delegation, evmChainID, walletSignature); err != nil {
		return err
	}
	return state.AuthorizeSession(delegation)
}
