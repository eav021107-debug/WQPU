package rpcmesh

import (
	"context"
	"errors"
	"io"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/carrier"
	"github.com/eav021107-debug/WQPU/client/internal/peertransport"
	"github.com/eav021107-debug/WQPU/client/internal/rpctunnel"
	"github.com/eav021107-debug/WQPU/client/internal/transport"
)

type ForwarderConfig struct {
	Signer       transport.Signer
	ChainID      string
	LocalPeerID  common.Hash
	RemotePeerID common.Hash
	Registry     peertransport.Resolver
	Dialer       carrier.Dialer
}

func validateForwarderConfig(config ForwarderConfig) error {
	if config.Signer == nil { return errors.New("WQPU RPC requester signer is required") }
	if config.ChainID == "" { return errors.New("WQPU RPC requester chain id is required") }
	if config.LocalPeerID == (common.Hash{}) || config.RemotePeerID == (common.Hash{}) { return errors.New("WQPU RPC requester peer ids are required") }
	if config.LocalPeerID == config.RemotePeerID { return errors.New("WQPU RPC requester cannot forward to itself") }
	if config.Registry == nil { return errors.New("WQPU RPC requester chain registry is required") }
	if config.Dialer == nil { return errors.New("WQPU RPC requester carrier dialer is required") }
	return nil
}

// OpenForwarder exposes one requester-local loopback endpoint for a registered
// remote peer. Each llama.cpp TCP connection performs a fresh chain lookup and
// a fresh authenticated WQPU SecureStream handshake before application bytes
// are forwarded.
func OpenForwarder(parent context.Context, config ForwarderConfig) (*rpctunnel.Forwarder, error) {
	if parent == nil { return nil, errors.New("WQPU RPC requester context is required") }
	if err := validateForwarderConfig(config); err != nil { return nil, err }
	select {
	case <-parent.Done(): return nil, parent.Err()
	default:
	}
	return rpctunnel.StartLocalForwarder(parent, func(ctx context.Context) (io.ReadWriteCloser, error) {
		connection, err := peertransport.DialRegistered(ctx, config.Dialer, config.Signer, config.ChainID, config.LocalPeerID, config.RemotePeerID, config.Registry)
		if err != nil { return nil, err }
		return connection.Stream, nil
	})
}
