package peertransport

import (
	"context"
	"errors"
	"io"
	"net"
	"testing"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

type fakeRegistry map[common.Hash]chainregistry.Peer

func (f fakeRegistry) ResolvePeer(_ context.Context, id common.Hash) (chainregistry.Peer, error) {
	peer, ok := f[id]
	if !ok { return chainregistry.Peer{}, errors.New("unknown peer") }
	return peer, nil
}

func peerID(last byte) common.Hash { var id common.Hash; id[31] = last; return id }

func registryPeer(id common.Hash, session common.Address, endpoint string) chainregistry.Peer {
	return chainregistry.Peer{
		Provider: chainregistry.Provider{PeerID: id, Wallet: common.HexToAddress("0x1000000000000000000000000000000000000001"), Endpoints: []string{endpoint}, ProtocolVersion: chainregistry.ProtocolVersion},
		ControlSession: session,
	}
}

type peerResult struct { conn *Connection; err error }

func TestDialAndAcceptUseChainResolvedControlSessions(t *testing.T) {
	leftRaw, rightRaw := net.Pipe()
	leftKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	rightKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	leftID, rightID := peerID(1), peerID(2)
	leftRegistry := fakeRegistry{rightID: registryPeer(rightID, common.HexToAddress(rightKey.Address()), "wqpu://right:7443")}
	rightRegistry := fakeRegistry{leftID: registryPeer(leftID, common.HexToAddress(leftKey.Address()), "wqpu://left:7443")}

	accepted := make(chan peerResult, 1)
	go func() { conn, err := Accept(context.Background(), rightRaw, rightKey, "wqpu-dev-1", rightID, rightRegistry); accepted <- peerResult{conn, err} }()
	dialed, err := Dial(context.Background(), leftRaw, leftKey, "wqpu-dev-1", leftID, rightID, leftRegistry)
	if err != nil { t.Fatal(err) }
	defer dialed.Stream.Close()
	remote := <-accepted
	if remote.err != nil { t.Fatal(remote.err) }
	defer remote.conn.Stream.Close()
	if dialed.Peer.Provider.PeerID != rightID || remote.conn.Peer.Provider.PeerID != leftID { t.Fatal("resolved peer identity mismatch") }

	writeDone := make(chan error, 1)
	go func() { _, err := dialed.Stream.Write([]byte("llama-rpc")); writeDone <- err }()
	buf := make([]byte, len("llama-rpc"))
	if _, err := io.ReadFull(remote.conn.Stream, buf); err != nil { t.Fatal(err) }
	if string(buf) != "llama-rpc" { t.Fatalf("payload=%q", buf) }
	if err := <-writeDone; err != nil { t.Fatal(err) }
}

func TestDialRejectsRegistrySessionThatDoesNotMatchRemoteSigner(t *testing.T) {
	leftRaw, rightRaw := net.Pipe()
	leftKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	rightKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	wrong, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	leftID, rightID := peerID(1), peerID(2)
	leftRegistry := fakeRegistry{rightID: registryPeer(rightID, common.HexToAddress(wrong.Address()), "wqpu://right:7443")}
	rightRegistry := fakeRegistry{leftID: registryPeer(leftID, common.HexToAddress(leftKey.Address()), "wqpu://left:7443")}

	accepted := make(chan peerResult, 1)
	go func() { conn, err := Accept(context.Background(), rightRaw, rightKey, "wqpu-dev-1", rightID, rightRegistry); accepted <- peerResult{conn, err} }()
	if _, err := Dial(context.Background(), leftRaw, leftKey, "wqpu-dev-1", leftID, rightID, leftRegistry); err == nil {
		t.Fatal("dial should reject signer that differs from chain control session")
	}

	// The responder may have authenticated the initiator and returned just before
	// the initiator discovers that the responder session disagrees with its own
	// chain snapshot. That is safe: the rejecting initiator closes the carrier,
	// so no application bytes can flow on the responder's one-sided stream.
	remote := <-accepted
	if remote.err == nil {
		defer remote.conn.Stream.Close()
		buf := make([]byte, 1)
		if _, err := remote.conn.Stream.Read(buf); err == nil {
			t.Fatal("one-sided responder stream must close before application data")
		}
	}
}

func TestAcceptRejectsUnknownClaimedPeerBeforeReply(t *testing.T) {
	leftRaw, rightRaw := net.Pipe()
	leftKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	rightKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	leftID, rightID := peerID(1), peerID(2)

	accepted := make(chan error, 1)
	go func() { _, err := Accept(context.Background(), rightRaw, rightKey, "wqpu-dev-1", rightID, fakeRegistry{}); accepted <- err }()
	if _, err := Dial(context.Background(), leftRaw, leftKey, "wqpu-dev-1", leftID, rightID, fakeRegistry{rightID: registryPeer(rightID, common.HexToAddress(rightKey.Address()), "wqpu://right:7443")}); err == nil {
		t.Fatal("initiator should fail when responder rejects unknown chain peer")
	}
	if err := <-accepted; err == nil { t.Fatal("unknown inbound peer should fail") }
}
