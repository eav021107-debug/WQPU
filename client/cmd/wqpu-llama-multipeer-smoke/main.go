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
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
	"github.com/eav021107-debug/WQPU/client/internal/computenode"
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

func peerID(last byte) common.Hash { var id common.Hash; id[31] = last; return id }

func registryPeer(id common.Hash, session, endpoint string) chainregistry.Peer {
	return chainregistry.Peer{Provider: chainregistry.Provider{
		Wallet: common.HexToAddress("0x1000000000000000000000000000000000000001"),
		PeerID: id, Endpoints: []string{endpoint}, ProtocolVersion: chainregistry.ProtocolVersion,
	}, ControlSession: common.HexToAddress(session)}
}

func waitHealth(ctx context.Context, done <-chan struct{}, url string) error {
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
		case <-done: return errors.New("distributed llama-server exited while loading model")
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

type smokeNode struct {
	id      common.Hash
	key     *sessionkey.Key
	node    *computenode.Node
	logFile *os.File
	logPath string
}

func startNode(ctx context.Context, baseDir string, reg registry, id common.Hash, key *sessionkey.Key, rpcPort int, logPath string, output io.Writer) (*smokeNode, error) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { return nil, err }
	var logFile *os.File
	if logPath != "" {
		logFile, err = os.OpenFile(logPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
		if err != nil { _ = listener.Close(); return nil, err }
		output = logFile
	}
	node, err := computenode.Start(ctx, computenode.Config{
		RuntimeBase: baseDir,
		Signer: key,
		ChainID: chainID,
		LocalPeerID: id,
		Registry: reg,
		Listener: listener,
		MaxConnections: 8,
		RPCPort: rpcPort,
		RPCThreads: 1,
		RPCOutput: output,
		BackendReady: 30 * time.Second,
	})
	if err != nil {
		_ = listener.Close()
		if logFile != nil { _ = logFile.Close() }
		return nil, err
	}
	return &smokeNode{id: id, key: key, node: node, logFile: logFile, logPath: logPath}, nil
}

func closeNode(n *smokeNode) {
	if n == nil { return }
	if n.node != nil { _ = n.node.Close() }
	if n.logFile != nil { _ = n.logFile.Close() }
}

func providerError(node *computenode.Node) error {
	if node == nil { return nil }
	select {
	case err, ok := <-node.ProviderErrors():
		if ok { return err }
		return nil
	default:
		return nil
	}
}

func restoreEnv(name, previous string, existed bool) {
	if existed { _ = os.Setenv(name, previous) } else { _ = os.Unsetenv(name) }
}

func run(baseDir string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	reg := registry{}

	requesterKey, err := sessionkey.Generate(); if err != nil { return err }
	provider0Key, err := sessionkey.Generate(); if err != nil { return err }
	provider1Key, err := sessionkey.Generate(); if err != nil { return err }
	requesterID, provider0ID, provider1ID := peerID(1), peerID(2), peerID(3)

	requester, err := startNode(ctx, baseDir, reg, requesterID, requesterKey, 50052, "", io.Discard)
	if err != nil { return err }
	defer closeNode(requester)

	previousDebug, hadDebug := os.LookupEnv("GGML_RPC_DEBUG")
	if err := os.Setenv("GGML_RPC_DEBUG", "1"); err != nil { return err }
	provider0Path := filepath.Join(baseDir, "multipeer-rpc-0.log")
	provider0, err := startNode(ctx, baseDir, reg, provider0ID, provider0Key, 50053, provider0Path, nil)
	if err != nil { restoreEnv("GGML_RPC_DEBUG", previousDebug, hadDebug); return err }
	provider1Path := filepath.Join(baseDir, "multipeer-rpc-1.log")
	provider1, err := startNode(ctx, baseDir, reg, provider1ID, provider1Key, 50054, provider1Path, nil)
	restoreEnv("GGML_RPC_DEBUG", previousDebug, hadDebug)
	if err != nil { closeNode(provider0); return err }
	defer closeNode(provider0)
	defer closeNode(provider1)

	// The smoke registry stands in for already-proven live chain resolution. All
	// three equal compute nodes share one registry view before any RPC connection.
	reg[requesterID] = registryPeer(requesterID, requesterKey.Address(), requester.node.ProviderEndpoint())
	reg[provider0ID] = registryPeer(provider0ID, provider0Key.Address(), provider0.node.ProviderEndpoint())
	reg[provider1ID] = registryPeer(provider1ID, provider1Key.Address(), provider1.node.ProviderEndpoint())

	serverLogPath := filepath.Join(baseDir, "multipeer-server.log")
	serverLog, err := os.OpenFile(serverLogPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil { return err }
	defer serverLog.Close()
	inference, err := requester.node.StartHFFileInference(ctx, []common.Hash{provider0ID, provider1ID}, apiPort, tinyRepo, tinyModelFile, computenode.InferenceTuning{
		GPULayers: 99,
		ContextSize: 128,
		Parallel: 1,
		Threads: 1,
		SplitMode: "layer",
		TensorSplit: []uint64{1, 1},
	}, serverLog, 45*time.Second)
	if err != nil { return err }
	defer inference.Close()

	if err := waitHealth(ctx, inference.Done(), inference.APIURL()+"/health"); err != nil {
		_ = serverLog.Sync(); log, _ := os.ReadFile(serverLogPath)
		return fmt.Errorf("compute-node llama-server health: %w\n%s", err, log)
	}
	text, err := completion(ctx, inference.APIURL()+"/completion")
	if err != nil { return err }

	for index, provider := range []*smokeNode{provider0, provider1} {
		if provider.logFile == nil { return errors.New("missing provider RPC debug log") }
		if err := provider.logFile.Sync(); err != nil { return err }
		log, err := os.ReadFile(provider.logPath)
		if err != nil { return err }
		if !strings.Contains(string(log), "[alloc_buffer]") {
			return fmt.Errorf("RPC%d compute node never received remote model allocation:\n%s", index, log)
		}
		if err := providerError(provider.node); err != nil { return fmt.Errorf("RPC%d compute node mesh error: %w", index, err) }
	}
	fmt.Printf("three equal WQPU compute nodes split one model across RPC0 and RPC1: %q\n", text)
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
