package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"

	"github.com/eav021107-debug/WQPU/client/internal/chainclient"
	"github.com/eav021107-debug/WQPU/client/internal/computenode"
	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

const (
	tinyRepo      = "ggml-org/models"
	tinyModelFile = "tinyllamas/stories260K.gguf"
	apiPort       = 8083
)

var sessionKeyHexes = []string{
	"0000000000000000000000000000000000000000000000000000000000000001",
	"0000000000000000000000000000000000000000000000000000000000000002",
	"0000000000000000000000000000000000000000000000000000000000000003",
}

var endpoints = []string{
	"wqpu://127.0.0.1:17443",
	"wqpu://127.0.0.1:17444",
	"wqpu://127.0.0.1:17445",
}

func peerID(index int) common.Hash {
	return crypto.Keccak256Hash([]byte(fmt.Sprintf("wqpu-live-compute-peer-%d", index+1)))
}

func sessionKey(index int) (*sessionkey.Key, error) {
	if index < 0 || index >= len(sessionKeyHexes) { return nil, errors.New("invalid WQPU live session index") }
	private, err := crypto.HexToECDSA(sessionKeyHexes[index])
	if err != nil { return nil, err }
	return sessionkey.FromPrivateKey(private)
}

func expectedAddress(index int) (common.Address, error) {
	private, err := crypto.HexToECDSA(sessionKeyHexes[index])
	if err != nil { return common.Address{}, err }
	return crypto.PubkeyToAddress(private.PublicKey), nil
}

func verifyPublishedPeers(ctx context.Context, chain *chainclient.Client) error {
	if chain == nil || chain.Registry() == nil { return errors.New("verified live WQPU registry is required") }
	for index := range sessionKeyHexes {
		id := peerID(index)
		peer, err := chain.Registry().ResolvePeer(ctx, id)
		if err != nil { return fmt.Errorf("resolve live compute peer %d: %w", index, err) }
		expectedSession, err := expectedAddress(index)
		if err != nil { return err }
		if peer.Provider.PeerID != id { return fmt.Errorf("peer %d id=%s want %s", index, peer.Provider.PeerID.Hex(), id.Hex()) }
		if peer.ControlSession != expectedSession { return fmt.Errorf("peer %d session=%s want %s", index, peer.ControlSession.Hex(), expectedSession.Hex()) }
		if len(peer.Provider.Endpoints) != 1 || peer.Provider.Endpoints[0] != endpoints[index] { return fmt.Errorf("peer %d endpoints=%v want %s", index, peer.Provider.Endpoints, endpoints[index]) }
		if peer.Provider.ProtocolVersion != uint32(chainclient.ProtocolVersion) { return fmt.Errorf("peer %d protocol=%d", index, peer.Provider.ProtocolVersion) }
	}
	return nil
}

type liveNode struct {
	index   int
	key     *sessionkey.Key
	node    *computenode.Node
	logFile *os.File
	logPath string
}

func closeNode(node *liveNode) {
	if node == nil { return }
	if node.node != nil { _ = node.node.Close() }
	if node.logFile != nil { _ = node.logFile.Close() }
}

func startNode(ctx context.Context, baseDir string, chain *chainclient.Client, index, rpcPort int, debug bool) (*liveNode, error) {
	key, err := sessionKey(index)
	if err != nil { return nil, err }
	var output io.Writer = io.Discard
	var logFile *os.File
	var logPath string
	if debug {
		logPath = filepath.Join(baseDir, fmt.Sprintf("live-chain-rpc-%d.log", index))
		logFile, err = os.OpenFile(logPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
		if err != nil { return nil, err }
		output = logFile
	}
	node, err := computenode.Start(ctx, computenode.Config{
		RuntimeBase: baseDir,
		Signer: key,
		ChainID: chainclient.DevWQPUChainID,
		LocalPeerID: peerID(index),
		Registry: chain.Registry(),
		ListenEndpoint: endpoints[index],
		MaxConnections: 8,
		RPCPort: rpcPort,
		RPCThreads: 1,
		RPCOutput: output,
		BackendReady: 30 * time.Second,
	})
	if err != nil {
		if logFile != nil { _ = logFile.Close() }
		return nil, fmt.Errorf("start live compute node %d: %w", index, err)
	}
	if node.ProviderEndpoint() != endpoints[index] {
		_ = node.Close()
		if logFile != nil { _ = logFile.Close() }
		return nil, fmt.Errorf("node %d endpoint=%s want %s", index, node.ProviderEndpoint(), endpoints[index])
	}
	return &liveNode{index: index, key: key, node: node, logFile: logFile, logPath: logPath}, nil
}

func waitHealth(ctx context.Context, inference *computenode.InferenceSession) error {
	if inference == nil { return errors.New("WQPU live inference session is required") }
	client := &http.Client{Timeout: 2 * time.Second}
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	for {
		request, err := http.NewRequestWithContext(ctx, http.MethodGet, inference.APIURL()+"/health", nil)
		if err != nil { return err }
		response, err := client.Do(request)
		if err == nil {
			_ = response.Body.Close()
			if response.StatusCode == http.StatusOK { return nil }
		}
		select {
		case <-inference.Done(): return errors.New("live-chain distributed llama-server exited before health")
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
	if err := json.Unmarshal(body, &decoded); err != nil { return "", fmt.Errorf("decode completion: %w", err) }
	if strings.TrimSpace(decoded.Content) == "" { return "", fmt.Errorf("empty completion: %s", body) }
	return decoded.Content, nil
}

func providerError(node *liveNode) error {
	if node == nil || node.node == nil { return nil }
	select {
	case err, ok := <-node.node.ProviderErrors():
		if ok { return err }
		return nil
	default:
		return nil
	}
}

func restoreEnv(name, previous string, existed bool) {
	if existed { _ = os.Setenv(name, previous) } else { _ = os.Unsetenv(name) }
}

func run(rpcURL, baseDir string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Minute)
	defer cancel()
	chain, err := chainclient.DialDev(ctx, rpcURL)
	if err != nil { return err }
	defer chain.Close()
	if err := verifyPublishedPeers(ctx, chain); err != nil { return err }

	requester, err := startNode(ctx, baseDir, chain, 0, 50052, false)
	if err != nil { return err }
	defer closeNode(requester)

	previousDebug, hadDebug := os.LookupEnv("GGML_RPC_DEBUG")
	if err := os.Setenv("GGML_RPC_DEBUG", "1"); err != nil { return err }
	provider0, err := startNode(ctx, baseDir, chain, 1, 50053, true)
	if err != nil { restoreEnv("GGML_RPC_DEBUG", previousDebug, hadDebug); return err }
	provider1, err := startNode(ctx, baseDir, chain, 2, 50054, true)
	restoreEnv("GGML_RPC_DEBUG", previousDebug, hadDebug)
	if err != nil { closeNode(provider0); return err }
	defer closeNode(provider0)
	defer closeNode(provider1)

	serverLogPath := filepath.Join(baseDir, "live-chain-server.log")
	serverLog, err := os.OpenFile(serverLogPath, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil { return err }
	defer serverLog.Close()
	inference, err := requester.node.StartHFFileInference(ctx, []common.Hash{peerID(1), peerID(2)}, apiPort, tinyRepo, tinyModelFile, computenode.InferenceTuning{
		GPULayers: 99,
		ContextSize: 128,
		Parallel: 1,
		Threads: 1,
		SplitMode: "layer",
		TensorSplit: []uint64{1, 1},
	}, serverLog, 60*time.Second)
	if err != nil { return err }
	defer inference.Close()
	if err := waitHealth(ctx, inference); err != nil {
		_ = serverLog.Sync(); log, _ := os.ReadFile(serverLogPath)
		return fmt.Errorf("live-chain inference health: %w\n%s", err, log)
	}
	text, err := completion(ctx, inference.APIURL()+"/completion")
	if err != nil { return err }

	for rpcIndex, node := range []*liveNode{provider0, provider1} {
		if node.logFile == nil { return errors.New("missing live-chain RPC debug log") }
		if err := node.logFile.Sync(); err != nil { return err }
		log, err := os.ReadFile(node.logPath)
		if err != nil { return err }
		if !strings.Contains(string(log), "[alloc_buffer]") { return fmt.Errorf("RPC%d live-chain node never allocated remote model memory:\n%s", rpcIndex, log) }
		if err := providerError(node); err != nil { return fmt.Errorf("RPC%d live-chain provider error: %w", rpcIndex, err) }
	}
	fmt.Printf("LIVE CHAIN COMPUTE PASSED: chain=%d protocol=%d requester=%s remotes=%s,%s completion=%q\n", chain.EVMChainID(), chain.Protocol(), peerID(0).Hex(), peerID(1).Hex(), peerID(2).Hex(), text)
	return nil
}

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: wqpu-live-chain-compute-smoke RPC_URL RUNTIME_BASE")
		os.Exit(2)
	}
	if err := run(os.Args[1], os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

var _ *ecdsa.PrivateKey
