package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
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

const (
	chainID       = "wqpu-llama-model-smoke"
	rpcPort       = 50053
	apiPort       = 8081
	tinyRepo      = "ggml-org/models"
	tinyModelFile = "tinyllamas/stories260K.gguf"
)

type registry map[common.Hash]chainregistry.Peer

func (r registry) ResolvePeer(_ context.Context, id common.Hash) (chainregistry.Peer, error) {
	peer, ok := r[id]
	if !ok { return chainregistry.Peer{}, errors.New("unknown WQPU model-smoke peer") }
	return peer, nil
}

func peerID(last byte) common.Hash {
	var id common.Hash
	id[31] = last
	return id
}

func registryPeer(id common.Hash, session, endpoint string) chainregistry.Peer {
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

func serveProvider(ctx context.Context, listener net.Listener, signer *sessionkey.Key, localID common.Hash, peers registry, rpcTarget string, errCh chan<- error) {
	for {
		raw, err := listener.Accept()
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) { return }
			select { case errCh <- err: default: }
			return
		}
		go func(conn net.Conn) {
			secure, err := peertransport.Accept(ctx, conn, signer, chainID, localID, peers)
			if err != nil {
				select { case errCh <- fmt.Errorf("secure provider accept: %w", err): default: }
				return
			}
			if err := rpctunnel.BridgeToLoopback(ctx, secure.Stream, rpcTarget); err != nil && ctx.Err() == nil {
				select { case errCh <- fmt.Errorf("provider RPC bridge: %w", err): default: }
			}
		}(raw)
	}
}

func waitHealth(ctx context.Context, process *llamaruntime.ManagedProcess, url string) error {
	client := &http.Client{Timeout: 2 * time.Second}
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	for {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil { return err }
		response, err := client.Do(request)
		if err == nil {
			_ = response.Body.Close()
			if response.StatusCode == http.StatusOK { return nil }
		}
		select {
		case <-process.Done():
			if err := process.Wait(); err != nil { return fmt.Errorf("llama-server exited while loading model: %w", err) }
			return errors.New("llama-server exited while loading model")
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func completion(ctx context.Context, endpoint string) (string, error) {
	payload, err := json.Marshal(map[string]any{
		"prompt": "Once upon a time",
		"n_predict": 8,
		"temperature": 0,
	})
	if err != nil { return "", err }
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil { return "", err }
	request.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 60 * time.Second}).Do(request)
	if err != nil { return "", err }
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil { return "", err }
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("completion HTTP %d: %s", response.StatusCode, body)
	}
	var decoded struct {
		Content string `json:"content"`
	}
	if err := json.Unmarshal(body, &decoded); err != nil { return "", fmt.Errorf("decode completion: %w: %s", err, body) }
	if strings.TrimSpace(decoded.Content) == "" { return "", fmt.Errorf("empty completion: %s", body) }
	return decoded.Content, nil
}

func run(baseDir string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	installed, err := (llamaruntime.Installer{}).InstallCPU(ctx, baseDir, goruntime.GOOS, goruntime.GOARCH)
	if err != nil { return err }

	backendLogPath := filepath.Join(baseDir, "model-smoke-rpc.log")
	backendLog, err := os.OpenFile(backendLogPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil { return err }
	backend, err := llamaruntime.StartRPCServer(ctx, installed, rpcPort, 1, nil, false, backendLog, 30*time.Second)
	if err != nil { _ = backendLog.Close(); return err }
	defer func() { _ = backend.Close(); _ = backendLog.Close() }()

	requesterKey, err := sessionkey.Generate()
	if err != nil { return err }
	providerKey, err := sessionkey.Generate()
	if err != nil { return err }
	requesterID, providerID := peerID(1), peerID(2)

	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { return err }
	defer listener.Close()
	providerEndpoint := "wqpu://" + listener.Addr().String()
	requesterRegistry := registry{providerID: registryPeer(providerID, providerKey.Address(), providerEndpoint)}
	providerRegistry := registry{requesterID: registryPeer(requesterID, requesterKey.Address(), "wqpu://127.0.0.1:1")}
	errCh := make(chan error, 16)
	go serveProvider(ctx, listener, providerKey, providerID, providerRegistry, fmt.Sprintf("127.0.0.1:%d", rpcPort), errCh)

	forwarder, err := rpctunnel.StartLocalForwarder(ctx, func(ctx context.Context) (io.ReadWriteCloser, error) {
		connection, err := peertransport.DialRegistered(ctx, carrier.TCPDialer{Timeout: 5 * time.Second}, requesterKey, chainID, requesterID, providerID, requesterRegistry)
		if err != nil { return nil, err }
		return connection.Stream, nil
	})
	if err != nil { return err }
	defer forwarder.Close()

	serverLogPath := filepath.Join(baseDir, "model-smoke-server.log")
	serverLog, err := os.OpenFile(serverLogPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil { return err }
	server, err := llamaruntime.StartLlamaServerForHFFile(
		ctx,
		installed,
		apiPort,
		[]string{forwarder.Address()},
		tinyRepo,
		tinyModelFile,
		llamaruntime.ServerTuning{Devices: []string{"RPC0"}, GPULayers: 99, ContextSize: 128, Parallel: 1, Threads: 1},
		serverLog,
		45*time.Second,
	)
	if err != nil { _ = serverLog.Close(); return err }
	defer func() { _ = server.Close(); _ = serverLog.Close() }()

	baseURL := fmt.Sprintf("http://127.0.0.1:%d", apiPort)
	if err := waitHealth(ctx, server, baseURL+"/health"); err != nil {
		_ = serverLog.Sync()
		log, _ := os.ReadFile(serverLogPath)
		return fmt.Errorf("llama-server health: %w\n%s", err, log)
	}
	text, err := completion(ctx, baseURL+"/completion")
	if err != nil {
		_ = serverLog.Sync()
		log, _ := os.ReadFile(serverLogPath)
		return fmt.Errorf("llama-server completion: %w\n%s", err, log)
	}

	_ = serverLog.Sync()
	log, err := os.ReadFile(serverLogPath)
	if err != nil { return err }
	if !strings.Contains(string(log), "RPC0") {
		return fmt.Errorf("llama-server log contains no RPC0 evidence:\n%s", log)
	}
	select {
	case err := <-errCh:
		return err
	default:
	}
	fmt.Printf("tiny model inference crossed remote WQPU RPC0: %q\n", text)
	return nil
}

func main() {
	if len(os.Args) != 3 || os.Args[1] != "--runtime-base" {
		fmt.Fprintln(os.Stderr, "usage: wqpu-llama-model-smoke --runtime-base BASE_DIR")
		os.Exit(2)
	}
	if err := run(os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
