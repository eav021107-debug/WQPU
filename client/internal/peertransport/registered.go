package peertransport

import (
	"context"
	"errors"
	"fmt"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/carrier"
	"github.com/eav021107-debug/WQPU/client/internal/transport"
)

// DialRegistered resolves the remote provider once from chain state, then
// connects only to endpoints contained in that same authenticated snapshot.
// This avoids a resolve/dial/resolve race where a peer could rotate identity
// between endpoint selection and the secure handshake.
func DialRegistered(
	ctx context.Context,
	dialer carrier.Dialer,
	signer transport.Signer,
	chainID string,
	localPeerID, remotePeerID common.Hash,
	registry Resolver,
) (*Connection, error) {
	if dialer == nil {
		return nil, errors.New("WQPU carrier dialer is required")
	}
	if registry == nil {
		return nil, errors.New("WQPU peer registry is required")
	}
	peer, err := registry.ResolvePeer(ctx, remotePeerID)
	if err != nil {
		return nil, err
	}
	if peer.Provider.PeerID != remotePeerID {
		return nil, errors.New("WQPU registry resolved a different outbound peer")
	}
	if peer.ControlSession == (common.Address{}) {
		return nil, errors.New("WQPU remote peer has no control session")
	}
	if len(peer.Provider.Endpoints) == 0 {
		return nil, errors.New("WQPU remote peer has no published endpoints")
	}

	var lastErr error
	for _, endpoint := range peer.Provider.Endpoints {
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		default:
		}
		raw, err := dialer.DialContext(ctx, endpoint)
		if err != nil {
			lastErr = fmt.Errorf("dial %s: %w", endpoint, err)
			continue
		}
		stream, err := transport.Initiate(
			raw,
			signer,
			chainID,
			[32]byte(localPeerID),
			[32]byte(remotePeerID),
			peer.ControlSession.Hex(),
		)
		if err != nil {
			lastErr = fmt.Errorf("authenticate %s: %w", endpoint, err)
			continue
		}
		return &Connection{Stream: stream, Peer: peer}, nil
	}
	if lastErr == nil {
		lastErr = errors.New("WQPU remote peer has no usable endpoint")
	}
	return nil, lastErr
}
