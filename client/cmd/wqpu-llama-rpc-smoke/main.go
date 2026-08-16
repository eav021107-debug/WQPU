package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	goruntime "runtime"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/carrier"
	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
	"github.com/eav021107-debug/WQPU/client/internal/llamaruntime"
	"github.com/eav021107-debug/WQPU/client/internal/peertransport"
	"github.com/eav021107-debug/WQPU/client/internal/rpctunnel"
	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

const chainID = "wqpu-llama-rpc-smoke"

type registry map[common.Hash]chainregistry.Peer

func (r registry) ResolvePeer(_ context.Context, id common.Hash) (chainregistry.Peer, error) {
	peer, ok := r[id]
	if !ok { return chainregistry.Peer{}, errors.New("unknown WQPU smoke peer") }
	return peer, nil
}

func peerID(last byte) common.Hash {
	var id common.Hash
	id[31] = last
	return id
}

func peer(id common.Hash, session, endpoint string) chainregistry.Peer {
	return chainregistry.Peer{
		Provider: chainregistry.Provider{
			Wallet: common.HexToAddress("0x1000000000000000000000000000000000000001"),
			PeerID: id,
			Endpoints: []string{endpoint},
			ProtocolVersion: chainregistry.ProtocolVersion,
		},
		ControlSession: common.HexToAddress(session),
	}
}

func serveProvider(ctx context.Context, listener net.Listener, signer *sessionkey.Key, localID common.Hash, peers registry, rpcTarget string, accepted chan<- struct{}, errCh chan<- error) {
	for {
		raw, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) { return }
			errCh <- err
			return
		}
		go func(conn net.Conn) {
			secure, err := peertransport.Accept(ctx, conn, signer, chainID, localID, peers)
			if err != nil { errCh <- fmt.Errorf("secure provider accept: %w", err); return }
			select { case accepted <- struct{}{}: default: }
			if err := rpctunnel.BridgeToLoopback(ctx, secure.Stream, rpcTarget); err != nil && ctx.Err() == nil {
				errCh <- fmt.Errorf("provider RPC bridge: %w", err)
			}
		}(raw)
	}
}

func run(cliPath, rpcTarget string) error {
	if cliPath == "" { return errors.New("llama-cli path is required") }
	if err := rpctunnel.ValidateLoopbackTarget(rpcTarget); err != nil { return err }

	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()

	requesterKey, err := sessionkey.Generate()
	if err != nil { return err }
	providerKey, err := sessionkey.Generate()
	if err != nil { return err }
	requesterID, providerID := peerID(1), peerID(2)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { return err }
	defer listener.Close()
	providerEndpoint := "wqpu://" + listener.Addr().String()

	requesterRegistry := registry{providerID: peer(providerID, providerKey.Address(), providerEndpoint)}
	providerRegistry := registry{requesterID: peer(requesterID, requesterKey.Address(), "wqpu://127.0.0.1:1")}

	accepted := make(chan struct{}, 1)
	errCh := make(chan error, 8)
	go serveProvider(ctx, listener, providerKey, providerID, providerRegistry, rpcTarget, accepted, errCh)

	forwarder, err := rpctunnel.StartLocalForwarder(ctx, func(ctx context.Context) (io.ReadWriteCloser, error) {
		connection, err := peertransport.DialRegistered(ctx, carrier.TCPDialer{Timeout: 5 * time.Second}, requesterKey, chainID, requesterID, providerID, requesterRegistry)
		if err != nil { return nil, err }
		return connection.Stream, nil
	})
	if err != nil { return err }
	defer forwarder.Close()

	cmd := exec.CommandContext(ctx, cliPath, "--rpc", forwarder.Address(), "--list-devices")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("llama-cli RPC device discovery failed: %w\n%s", err, output)
	}
	select {
	case <-accepted:
	case err := <-errCh:
		return err
	case <-ctx.Done():
		return errors.New("llama-cli never opened the WQPU RPC tunnel")
	}
	if !strings.Contains(strings.ToUpper(string(output)), "RPC") {
		return fmt.Errorf("llama-cli output did not expose an RPC device:\n%s", output)
	}
	select {
	case err := <-errCh:
		return err
	default:
	}
	fmt.Printf("real llama.cpp RPC crossed WQPU SecureStream: forwarder=%s target=%s\n%s", forwarder.Address(), rpcTarget, output)
	return nil
}

func runManagedRuntime(baseDir string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	installed, err := (llamaruntime.Installer{}).InstallCPU(ctx, baseDir, goruntime.GOOS, goruntime.GOARCH)
	if err != nil { return err }
	const rpcPort = 50053
	backend, err := llamaruntime.StartRPCServer(ctx, installed, rpcPort, 1, nil, false, os.Stderr, 30*time.Second)
	if err != nil { return err }
	defer backend.Close()
	return run(installed.LlamaCLI, fmt.Sprintf("127.0.0.1:%d", rpcPort))
}

func main() {
	var err error
	switch {
	case len(os.Args) == 3 && os.Args[1] == "--runtime-base":
		err = runManagedRuntime(os.Args[2])
	case len(os.Args) == 3:
		err = run(os.Args[1], os.Args[2])
	default:
		fmt.Fprintln(os.Stderr, "usage: wqpu-llama-rpc-smoke --runtime-base BASE_DIR | /path/to/llama-cli 127.0.0.1:50053")
		os.Exit(2)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
