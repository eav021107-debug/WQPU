package chainregistry

import (
	"context"
	"errors"
	"fmt"
	"math/big"
	"strings"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
)

const MaxRegistryPeers = 65536

const discoveryABIJSON = `[
  {"type":"function","name":"peerCount","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
  {"type":"function","name":"peerAt","stateMutability":"view","inputs":[{"name":"index","type":"uint256"}],"outputs":[{"type":"bytes32"}]}
]`

func (r *Registry) discoveryCall(ctx context.Context, method string, args ...any) ([]any, error) {
	if r == nil || r.caller == nil {
		return nil, errors.New("WQPU chain registry is unavailable")
	}
	parsed, err := abi.JSON(strings.NewReader(discoveryABIJSON))
	if err != nil {
		return nil, err
	}
	input, err := parsed.Pack(method, args...)
	if err != nil {
		return nil, err
	}
	out, err := r.caller.CallContract(ctx, ethereum.CallMsg{To: &PrecompileAddress, Data: input}, nil)
	if err != nil {
		return nil, fmt.Errorf("WQPU registry %s: %w", method, err)
	}
	values, err := parsed.Unpack(method, out)
	if err != nil {
		return nil, fmt.Errorf("decode WQPU registry %s: %w", method, err)
	}
	return values, nil
}

// PeerIDs enumerates the canonical provider index stored in the WQPU chain.
// Compute-peer discovery must come from this index, never from an external
// peer list or a manually supplied executor list.
func (r *Registry) PeerIDs(ctx context.Context) ([]common.Hash, error) {
	values, err := r.discoveryCall(ctx, "peerCount")
	if err != nil {
		return nil, err
	}
	if len(values) != 1 {
		return nil, errors.New("invalid peerCount output")
	}
	count, ok := values[0].(*big.Int)
	if !ok || count == nil || count.Sign() < 0 || count.BitLen() > 64 {
		return nil, errors.New("invalid peerCount value")
	}
	if count.Uint64() > MaxRegistryPeers {
		return nil, errors.New("WQPU registry peer count exceeds client safety limit")
	}

	ids := make([]common.Hash, 0, count.Uint64())
	seen := make(map[common.Hash]struct{}, count.Uint64())
	for i := uint64(1); i <= count.Uint64(); i++ {
		values, err := r.discoveryCall(ctx, "peerAt", new(big.Int).SetUint64(i))
		if err != nil {
			return nil, err
		}
		if len(values) != 1 {
			return nil, errors.New("invalid peerAt output")
		}
		var id common.Hash
		switch value := values[0].(type) {
		case [32]byte:
			id = common.Hash(value)
		case common.Hash:
			id = value
		default:
			return nil, errors.New("invalid peerAt bytes32 value")
		}
		if id == (common.Hash{}) {
			return nil, errors.New("zero peer id in WQPU registry index")
		}
		if _, exists := seen[id]; exists {
			return nil, errors.New("duplicate peer id in WQPU registry index")
		}
		seen[id] = struct{}{}
		ids = append(ids, id)
	}
	return ids, nil
}

// ActivePeers resolves every currently active compute provider from chain
// state. Endpoint, load, memory, model and control-session data are all read
// from the authenticated WQPU registry snapshot.
func (r *Registry) ActivePeers(ctx context.Context) ([]Peer, error) {
	ids, err := r.PeerIDs(ctx)
	if err != nil {
		return nil, err
	}
	peers := make([]Peer, 0, len(ids))
	for _, id := range ids {
		values, err := r.call(ctx, "providerActive", id)
		if err != nil {
			return nil, err
		}
		if len(values) != 1 {
			return nil, errors.New("invalid providerActive output")
		}
		active, ok := values[0].(bool)
		if !ok {
			return nil, errors.New("invalid providerActive bool")
		}
		if !active {
			continue
		}
		peer, err := r.ResolvePeer(ctx, id)
		if err != nil {
			return nil, err
		}
		peers = append(peers, peer)
	}
	return peers, nil
}
