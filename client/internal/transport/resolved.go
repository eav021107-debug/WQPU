package transport

import (
	"context"
	"errors"
	"io"
)

type SessionResolver func(context.Context, [32]byte) (string, error)

// AcceptResolved authenticates an inbound initiator against identity resolved
// from trusted chain state after learning only the claimed peer_id from its
// signed hello. The remote-provided session is never treated as authoritative.
func AcceptResolved(
	ctx context.Context,
	raw io.ReadWriteCloser,
	signer Signer,
	chainID string,
	localPeerID [32]byte,
	resolve SessionResolver,
) (*SecureStream, [32]byte, error) {
	var zero [32]byte
	fail := func(err error) (*SecureStream, [32]byte, error) {
		if raw != nil {
			_ = raw.Close()
		}
		return nil, zero, err
	}
	if raw == nil {
		return nil, zero, errors.New("WQPU carrier stream is required")
	}
	if resolve == nil {
		return fail(errors.New("WQPU chain session resolver is required"))
	}
	select {
	case <-ctx.Done():
		return fail(ctx.Err())
	default:
	}

	remote, err := readHello(raw)
	if err != nil {
		return fail(err)
	}
	expectedSession, err := resolve(ctx, remote.PeerID)
	if err != nil {
		return fail(err)
	}
	if expectedSession == "" {
		return fail(errors.New("WQPU chain returned an empty control session"))
	}

	handshake, err := NewHandshake(RoleResponder, signer, chainID, localPeerID)
	if err != nil {
		return fail(err)
	}
	channel, err := handshake.Establish(remote, remote.PeerID, expectedSession)
	if err != nil {
		return fail(err)
	}
	if err := writeHello(raw, handshake.Bytes()); err != nil {
		return fail(err)
	}
	return &SecureStream{raw: raw, channel: channel}, remote.PeerID, nil
}
