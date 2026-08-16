package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

func peerControlSlot(peerID common.Hash) common.Hash {
	return slot("peer-control-session", peerID.Bytes())
}

func StorePeerControlSession(state WordState, peerID common.Hash, session common.Address) error {
	if state == nil || peerID == (common.Hash{}) || session == (common.Address{}) {
		return errors.New("valid WQPU state, peer id and control session required")
	}
	state.SetState(Address, peerControlSlot(peerID), common.BytesToHash(session.Bytes()))
	return nil
}

func LoadPeerControlSession(state WordState, peerID common.Hash) (common.Address, bool, error) {
	if state == nil || peerID == (common.Hash{}) {
		return common.Address{}, false, errors.New("valid WQPU state and peer id required")
	}
	word := state.GetState(Address, peerControlSlot(peerID))
	if word == (common.Hash{}) {
		return common.Address{}, false, nil
	}
	session, err := addressFromHash(word)
	if err != nil {
		return common.Address{}, false, err
	}
	if session == (common.Address{}) {
		return common.Address{}, false, errors.New("corrupt WQPU peer control session")
	}
	return session, true, nil
}

func DeletePeerControlSession(state WordState, peerID common.Hash) error {
	if state == nil || peerID == (common.Hash{}) {
		return errors.New("valid WQPU state and peer id required")
	}
	state.SetState(Address, peerControlSlot(peerID), common.Hash{})
	return nil
}

// CommitProviderPublishV2 additionally binds the concrete peer to the device's
// currently authorized session. A new session may take over the same peer only
// when the same wallet signs the heartbeat; peer ownership itself cannot move.
func CommitProviderPublishV2(state WordState, envelope ProviderPublishEnvelope, config NetworkConfig, height uint64) error {
	action, err := VerifyProviderPublish(state, envelope, config, height)
	if err != nil {
		return err
	}
	provider, err := envelope.Announcement.ToRecord(height)
	if err != nil {
		return err
	}
	if err := StorePeerProvider(state, provider); err != nil {
		return err
	}
	if err := StorePeerControlSession(state, provider.PeerID, envelope.Session); err != nil {
		return err
	}
	return AdvanceSessionActionNonce(state, envelope.Wallet, envelope.Session, action.ActionNonce)
}
