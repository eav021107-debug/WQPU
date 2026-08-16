package peertransport

import (
	"context"
	"errors"
	"io"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
	"github.com/eav021107-debug/WQPU/client/internal/transport"
)

type Resolver interface {
	ResolvePeer(context.Context, common.Hash) (chainregistry.Peer, error)
}

type Connection struct {
	Stream *transport.SecureStream
	Peer chainregistry.Peer
}

func Dial(ctx context.Context, raw io.ReadWriteCloser, signer transport.Signer, chainID string, localPeerID, remotePeerID common.Hash, registry Resolver) (*Connection, error) {
	if registry == nil { if raw != nil { _ = raw.Close() }; return nil, errors.New("WQPU peer registry is required") }
	peer, err := registry.ResolvePeer(ctx, remotePeerID)
	if err != nil { if raw != nil { _ = raw.Close() }; return nil, err }
	stream, err := transport.Initiate(raw, signer, chainID, [32]byte(localPeerID), [32]byte(remotePeerID), peer.ControlSession.Hex())
	if err != nil { return nil, err }
	return &Connection{Stream: stream, Peer: peer}, nil
}

func Accept(ctx context.Context, raw io.ReadWriteCloser, signer transport.Signer, chainID string, localPeerID common.Hash, registry Resolver) (*Connection, error) {
	if registry == nil { if raw != nil { _ = raw.Close() }; return nil, errors.New("WQPU peer registry is required") }
	var resolved chainregistry.Peer
	stream, remoteID, err := transport.AcceptResolved(ctx, raw, signer, chainID, [32]byte(localPeerID), func(ctx context.Context, claimed [32]byte) (string, error) {
		peer, err := registry.ResolvePeer(ctx, common.Hash(claimed))
		if err != nil { return "", err }
		resolved = peer
		return peer.ControlSession.Hex(), nil
	})
	if err != nil { return nil, err }
	if resolved.Provider.PeerID != common.Hash(remoteID) {
		_ = stream.Close()
		return nil, errors.New("WQPU registry resolved a different inbound peer")
	}
	return &Connection{Stream: stream, Peer: resolved}, nil
}
