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
	chainID       = "wqpu-llama-multipeer-smoke"
	apiPort       = 8082
	tinyRepo      = "ggml-org/models"
	tinyModelFile = "tinyllamas/stories260K.gguf"
)

type registry map[common.Hash]chainregistry.Peer

func (r registry) ResolvePeer(_ context.Context, id common.Hash) (chainregistry.Peer, error) {
	peer, ok := r[id]
	if !ok { return chainregistry.Peer{}, errors.New("unknown WQPU multipeer-smoke peer") }
	return peer, nil
}

func peerID(last byte) common.Hash {
	var id common.Hash
	id[31] = last
	return id
}

func registryPeer(id common.Hash, session, endpoint string) chainregistry.Peer {
	return chainregistry.Peer{Provider: chainregistry.Provider{
		Wallet: common.HexToAddress("0x1000000000000000000000000000000000000001"),
		PeerID: id, Endpoints: []string{endpoint}, ProtocolVersion: chainregistry.ProtocolVersion,
	}, ControlSession: common.HexToAddress(session)}
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
			if err := process.Wait(); err != nil { return fmt.Errorf("llama-server exited while loading split model: %w", err) }
			return errors.New("llama-server exited while loading split model")
		case <-ctx.Done(): return ctx.Err()
		case <-ticker.C:
		}
	}
}

func completion(ctx context.Context, endpoint string) (string, error) {
	payload, err := json.Marshal(map[string]any{"prompt": "Once upon a time", "n_predict": 8, "temperature": 0})
	if err != nil { return "", err }
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil { return "", err }
	request.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 60 * time.Second}).Do(request)
	if err != nil { return "", err }
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil { return "", err }
	if response.StatusCode != http.StatusOK { return "", fmt.Errorf("completion HTTP %d: %s", response.StatusCode, body) }
	var decoded struct { Content string `json:"content"` }
	if err := json.Unmarshal(body, &decoded); err != nil { return "", err }
	if strings.TrimSpace(decoded.Content) == "" { return "", fmt.Errorf("empty completion: %s", body) }
	return decoded.Content, nil
}

type providerRuntime struct {
	id       common.Hash
	key      *sessionkey.Key
	listener net.Listener
	backend  *llamaruntime.ManagedProcess
	logFile  *os.File
	logPath  string
	port     int
}

func closeProvider(p *providerRuntime) {
	if p == nil { return }
	if p.listener != nil { _ = p.listener.Close() }
	if p.backend != nil { _ = p.backend.Close() }
	if p.logFile != nil { _ = p.logFile.Close() }
}

func startProvider(ctx context.Context, baseDir string, installed llamaruntime.Runtime, index, port int) (*providerRuntime, error) {
	key, err := sessionkey.Generate()
	if err != nil { return nil, err }
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { return nil, err }
	logPath := filepath.Join(baseDir, fmt.Sprintf("multipeer-rpc-%d.log", index))
	logFile, err := os.OpenFile(logPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil { _ = listener.Close(); return nil, err }
	backend, err := llamaruntime.StartRPCServer(ctx, installed, port, 1, nil, false, logFile, 30*time.Second)
	if err != nil { _ = listener.Close(); _ = logFile.Close(); return nil, err }
	return &providerRuntime{id: peerID(byte(index + 2)), key: key, listener: listener, backend: backend, logFile: logFile, logPath: logPath, port: port}, nil
}

func run(baseDir string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	installed, err := (llamaruntime.Installer{}).InstallCPU(ctx, baseDir, goruntime.GOOS, goruntime.GOARCH)
	if err != nil { return err }

	previousDebug, hadDebug := os.LookupEnv("GGML_RPC_DEBUG")
	if err := os.Setenv("GGML_RPC_DEBUG", "1"); err != nil { return err }
	provider0, err := startProvider(ctx, baseDir, installed, 0, 50053)
	if err != nil { if hadDebug { _ = os.Setenv("GGML_RPC_DEBUG", previousDebug) } else { _ = os.Unsetenv("GGML_RPC_DEBUG") }; return err }
	provider1, err := startProvider(ctx, baseDir, installed, 1, 50054)
	if hadDebug { _ = os.Setenv("GGML_RPC_DEBUG", previousDebug) } else { _ = os.Unsetenv("GGML_RPC_DEBUG") }
	if err != nil { closeProvider(provider0); return err }
	defer closeProvider(provider0)
	defer closeProvider(provider1)

	requesterKey, err := sessionkey.Generate()
	if err != nil { return err }
	requesterID := peerID(1)
	requesterRegistry := registry{
		provider0.id: registryPeer(provider0.id, provider0.key.Address(), "wqpu://"+provider0.listener.Addr().String()),
		provider1.id: registryPeer(provider1.id, provider1.key.Address(), "wqpu://"+provider1.listener.Addr().String()),
	}
	providerRegistry := registry{requesterID: registryPeer(requesterID, requesterKey.Address(), "wqpu://127.0.0.1:1")}
	errCh := make(chan error, 32)
	go serveProvider(ctx, provider0.listener, provider0.key, provider0.id, providerRegistry, fmt.Sprintf("127.0.0.1:%d", provider0.port), errCh)
	go serveProvider(ctx, provider1.listener, provider1.key, provider1.id, providerRegistry, fmt.Sprintf("127.0.0.1:%d", provider1.port), errCh)

	startForwarder := func(remote common.Hash) (*rpctunnel.LocalForwarder, error) {
		return rpctunnel.StartLocalForwarder(ctx, func(ctx context.Context) (io.ReadWriteCloser, error) {
			connection, err := peertransport.DialRegistered(ctx, carrier.TCPDialer{Timeout: 5 * time.Second}, requesterKey, chainID, requesterID, remote, requesterRegistry)
			if err != nil { return nil, err }
			return connection.Stream, nil
		})
	}
	forwarder0, err := startForwarder(provider0.id)
	if err != nil { return err }
	defer forwarder0.Close()
	forwarder1, err := startForwarder(provider1.id)
	if err != nil { return err }
	defer forwarder1.Close()

	serverLogPath := filepath.Join(baseDir, "multipeer-server.log")
	serverLog, err := os.OpenFile(serverLogPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil { return err }
	server, err := llamaruntime.StartLlamaServerForHFFile(ctx, installed, apiPort, []string{forwarder0.Address(), forwarder1.Address()}, tinyRepo, tinyModelFile, llamaruntime.ServerTuning{
		Devices: []string{"RPC0", "RPC1"}, SplitMode: "layer", TensorSplit: []uint64{1, 1}, GPULayers: 99, ContextSize: 128, Parallel: 1, Threads: 1,
	}, serverLog, 45*time.Second)
	if err != nil { _ = serverLog.Close(); return err }
	defer func() { _ = server.Close(); _ = serverLog.Close() }()

	baseURL := fmt.Sprintf("http://127.0.0.1:%d", apiPort)
	if err := waitHealth(ctx, server, baseURL+"/health"); err != nil {
		_ = serverLog.Sync(); log, _ := os.ReadFile(serverLogPath)
		return fmt.Errorf("multipeer llama-server health: %w\n%s", err, log)
	}
	text, err := completion(ctx, baseURL+"/completion")
	if err != nil { return err }

	for index, p := range []*providerRuntime{provider0, provider1} {
		if err := p.logFile.Sync(); err != nil { return err }
		log, err := os.ReadFile(p.logPath)
		if err != nil { return err }
		if !strings.Contains(string(log), "[alloc_buffer]") {
			return fmt.Errorf("RPC%d never received remote model allocation:\n%s", index, log)
		}
	}
	select { case err := <-errCh: return err; default: }
	fmt.Printf("two-peer WQPU model inference allocated on RPC0 and RPC1: %q\n", text)
	return nil
}

func main() {
	if len(os.Args) != 3 || os.Args[1] != "--runtime-base" {
		fmt.Fprintln(os.Stderr, "usage: wqpu-llama-multipeer-smoke --runtime-base BASE_DIR")
		os.Exit(2)
	}
	if err := run(os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
