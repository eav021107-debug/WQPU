package precompile

import (
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

const providerPeerIndex = "provider-peers"

func peerProviderKey(peerID common.Hash) []byte { return peerID.Bytes() }

// StorePeerProvider allows one wallet to own many independent compute peers.
// A peer id, however, can never silently move to another wallet.
func StorePeerProvider(state WordState, provider ProviderRecord) error {
	encoded, err := EncodeProvider(provider)
	if err != nil {
		return err
	}
	if state == nil {
		return errors.New("nil WQPU state")
	}
	old, exists, err := LoadPeerProvider(state, provider.PeerID)
	if err != nil {
		return err
	}
	if exists {
		if old.Wallet != provider.Wallet {
			return errors.New("WQPU peer id already belongs to another wallet")
		}
		if provider.HeartbeatHeight <= old.HeartbeatHeight {
			return errors.New("WQPU provider heartbeat must strictly increase")
		}
	}
	if err := WriteBlob(state, "peer-provider-record", peerProviderKey(provider.PeerID), encoded); err != nil {
		return err
	}
	_, _, err = AddIndexedHash(state, providerPeerIndex, provider.PeerID)
	return err
}

func LoadPeerProvider(state WordState, peerID common.Hash) (ProviderRecord, bool, error) {
	if state == nil || peerID == (common.Hash{}) {
		return ProviderRecord{}, false, errors.New("valid state and peer id required")
	}
	encoded, err := ReadBlob(state, "peer-provider-record", peerProviderKey(peerID))
	if err != nil {
		return ProviderRecord{}, false, err
	}
	if len(encoded) == 0 {
		return ProviderRecord{}, false, nil
	}
	provider, err := DecodeProvider(encoded)
	if err != nil {
		return ProviderRecord{}, false, err
	}
	if provider.PeerID != peerID {
		return ProviderRecord{}, false, errors.New("WQPU provider stored under wrong peer id")
	}
	return provider, true, nil
}

func DeletePeerProvider(state WordState, peerID common.Hash) error {
	if state == nil || peerID == (common.Hash{}) {
		return errors.New("valid state and peer id required")
	}
	if err := DeleteBlob(state, "peer-provider-record", peerProviderKey(peerID)); err != nil {
		return err
	}
	_, err := RemoveIndexedHash(state, providerPeerIndex, peerID)
	return err
}

func ProviderPeerIDs(state WordState) ([]common.Hash, error) {
	return IndexedHashes(state, providerPeerIndex)
}
