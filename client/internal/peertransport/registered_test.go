package peertransport

import (
	"context"
	"fmt"
	"io"
	"net"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/carrier"
	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

func TestDialRegisteredUsesPublishedEndpointAndChainSession(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	port := listener.Addr().(*net.TCPAddr).Port

	leftKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	rightKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	leftID, rightID := peerID(1), peerID(2)
	endpoint := fmt.Sprintf("wqpu://127.0.0.1:%d", port)
	leftRegistry := fakeRegistry{rightID: chainregistry.Peer{Provider: chainregistry.Provider{PeerID: rightID, Endpoints: []string{endpoint}, ProtocolVersion: 1}, ControlSession: common.HexToAddress(rightKey.Address())}}
	rightRegistry := fakeRegistry{leftID: chainregistry.Peer{Provider: chainregistry.Provider{PeerID: leftID, Endpoints: []string{"wqpu://left:7443"}, ProtocolVersion: 1}, ControlSession: common.HexToAddress(leftKey.Address())}}

	type acceptResult struct { conn *Connection; err error }
	accepted := make(chan acceptResult, 1)
	go func() {
		raw, err := listener.Accept()
		if err != nil { accepted <- acceptResult{err: err}; return }
		conn, err := Accept(context.Background(), raw, rightKey, "wqpu-dev-1", rightID, rightRegistry)
		accepted <- acceptResult{conn: conn, err: err}
	}()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	dialed, err := DialRegistered(ctx, carrier.TCPDialer{}, leftKey, "wqpu-dev-1", leftID, rightID, leftRegistry)
	if err != nil { t.Fatal(err) }
	defer dialed.Stream.Close()
	remote := <-accepted
	if remote.err != nil { t.Fatal(remote.err) }
	defer remote.conn.Stream.Close()

	writeDone := make(chan error, 1)
	go func() { _, err := dialed.Stream.Write([]byte("encrypted-llama-rpc")); writeDone <- err }()
	buf := make([]byte, len("encrypted-llama-rpc"))
	if _, err := io.ReadFull(remote.conn.Stream, buf); err != nil { t.Fatal(err) }
	if string(buf) != "encrypted-llama-rpc" { t.Fatalf("payload=%q", buf) }
	if err := <-writeDone; err != nil { t.Fatal(err) }
}
