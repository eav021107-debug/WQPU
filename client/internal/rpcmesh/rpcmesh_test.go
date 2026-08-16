package rpcmesh

import (
	"context"
	"errors"
	"io"
	"net"
	"strings"
	"testing"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/carrier"
	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

type fakeRegistry map[common.Hash]chainregistry.Peer

func (f fakeRegistry) ResolvePeer(_ context.Context, id common.Hash) (chainregistry.Peer, error) {
	peer, ok := f[id]
	if !ok { return chainregistry.Peer{}, errors.New("unknown peer") }
	return peer, nil
}

func meshPeerID(last byte) common.Hash {
	var id common.Hash
	id[31] = last
	return id
}

func meshPeer(id common.Hash, session, endpoint string) chainregistry.Peer {
	return chainregistry.Peer{Provider: chainregistry.Provider{
		Wallet: common.HexToAddress("0x1000000000000000000000000000000000000001"),
		PeerID: id,
		Endpoints: []string{endpoint},
		ProtocolVersion: chainregistry.ProtocolVersion,
	}, ControlSession: common.HexToAddress(session)}
}

func startEchoBackend(t *testing.T) (string, func()) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil { return }
			go func(conn net.Conn) {
				defer conn.Close()
				buffer := make([]byte, 4096)
				for {
					n, err := conn.Read(buffer)
					if n > 0 {
						if _, writeErr := conn.Write(buffer[:n]); writeErr != nil { return }
					}
					if err != nil { return }
					select { case <-ctx.Done(): return; default: }
				}
			}(conn)
		}
	}()
	return listener.Addr().String(), func() { cancel(); _ = listener.Close() }
}

func waitActive(t *testing.T, service *ProviderService, want int) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if service.ActiveConnections() == want { return }
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("active connections=%d, want %d", service.ActiveConnections(), want)
}

func TestProviderAndForwarderCarryAuthenticatedRPCBytes(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	backend, closeBackend := startEchoBackend(t)
	defer closeBackend()

	requesterKey, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	providerKey, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	requesterID, providerID := meshPeerID(1), meshPeerID(2)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	providerRegistry := fakeRegistry{requesterID: meshPeer(requesterID, requesterKey.Address(), "wqpu://127.0.0.1:1")}
	provider, err := StartProviderOnListener(ctx, listener, ProviderConfig{
		Signer: providerKey, ChainID: "wqpu-rpcmesh-test", LocalPeerID: providerID,
		Registry: providerRegistry, RPCTarget: backend, MaxConnections: 4,
	})
	if err != nil { t.Fatal(err) }
	defer provider.Close()

	requesterRegistry := fakeRegistry{providerID: meshPeer(providerID, providerKey.Address(), provider.Endpoint())}
	forwarder, err := OpenForwarder(ctx, ForwarderConfig{
		Signer: requesterKey, ChainID: "wqpu-rpcmesh-test", LocalPeerID: requesterID,
		RemotePeerID: providerID, Registry: requesterRegistry,
		Dialer: carrier.TCPDialer{Timeout: time.Second},
	})
	if err != nil { t.Fatal(err) }
	defer forwarder.Close()

	local, err := net.DialTimeout("tcp", forwarder.Address(), time.Second)
	if err != nil { t.Fatal(err) }
	defer local.Close()
	payload := []byte("real llama rpc bytes")
	if _, err := local.Write(payload); err != nil { t.Fatal(err) }
	got := make([]byte, len(payload))
	if _, err := io.ReadFull(local, got); err != nil { t.Fatal(err) }
	if string(got) != string(payload) { t.Fatalf("echo=%q want=%q", got, payload) }
	waitActive(t, provider, 1)
}

func TestProviderConnectionLimitRejectsExtraUnauthenticatedCarrier(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	backend, closeBackend := startEchoBackend(t)
	defer closeBackend()
	providerKey, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	providerID := meshPeerID(2)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	service, err := StartProviderOnListener(ctx, listener, ProviderConfig{
		Signer: providerKey, ChainID: "wqpu-rpcmesh-limit", LocalPeerID: providerID,
		Registry: fakeRegistry{}, RPCTarget: backend, MaxConnections: 1,
	})
	if err != nil { t.Fatal(err) }
	defer service.Close()

	first, err := net.DialTimeout("tcp", listener.Addr().String(), time.Second)
	if err != nil { t.Fatal(err) }
	defer first.Close()
	waitActive(t, service, 1)

	second, err := net.DialTimeout("tcp", listener.Addr().String(), time.Second)
	if err != nil { t.Fatal(err) }
	defer second.Close()
	_ = second.SetReadDeadline(time.Now().Add(2 * time.Second))
	buffer := make([]byte, 1)
	if _, err := second.Read(buffer); err == nil { t.Fatal("over-limit carrier remained open") }

	select {
	case err := <-service.Errors():
		if err == nil || !strings.Contains(err.Error(), "limit reached") { t.Fatalf("provider error=%v", err) }
	case <-time.After(2 * time.Second):
		t.Fatal("provider did not report connection limit rejection")
	}
}

func TestProviderCloseTerminatesStalledHandshake(t *testing.T) {
	ctx := context.Background()
	backend, closeBackend := startEchoBackend(t)
	defer closeBackend()
	providerKey, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	service, err := StartProviderOnListener(ctx, listener, ProviderConfig{
		Signer: providerKey, ChainID: "wqpu-rpcmesh-close", LocalPeerID: meshPeerID(2),
		Registry: fakeRegistry{}, RPCTarget: backend, MaxConnections: 1,
	})
	if err != nil { t.Fatal(err) }
	stalled, err := net.DialTimeout("tcp", listener.Addr().String(), time.Second)
	if err != nil { t.Fatal(err) }
	defer stalled.Close()
	waitActive(t, service, 1)

	done := make(chan error, 1)
	go func() { done <- service.Close() }()
	select {
	case err := <-done:
		if err != nil { t.Fatal(err) }
	case <-time.After(3 * time.Second):
		t.Fatal("provider shutdown blocked on stalled unauthenticated handshake")
	}
	if service.ActiveConnections() != 0 { t.Fatalf("active after close=%d", service.ActiveConnections()) }
}

func TestMeshRejectsUnsafeConfiguration(t *testing.T) {
	key, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	id1, id2 := meshPeerID(1), meshPeerID(2)
	if _, err := StartProviderOnListener(context.Background(), nil, ProviderConfig{}); err == nil { t.Fatal("nil listener should fail") }
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()
	if _, err := StartProviderOnListener(context.Background(), listener, ProviderConfig{Signer: key, ChainID: "x", LocalPeerID: id1, Registry: fakeRegistry{}, RPCTarget: "192.168.1.1:50052"}); err == nil {
		t.Fatal("non-loopback RPC target should fail")
	}
	if _, err := OpenForwarder(context.Background(), ForwarderConfig{Signer: key, ChainID: "x", LocalPeerID: id1, RemotePeerID: id1, Registry: fakeRegistry{}, Dialer: carrier.TCPDialer{}}); err == nil {
		t.Fatal("self-forwarding should fail")
	}
	_ = id2
}
