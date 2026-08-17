package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"

	"github.com/eav021107-debug/WQPU/client/internal/chainclient"
	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
	"github.com/eav021107-debug/WQPU/client/internal/computenode"
	"github.com/eav021107-debug/WQPU/client/internal/devidentity"
)

const (
	tinyRepo      = "ggml-org/models"
	tinyModelFile = "tinyllamas/stories260K.gguf"
)

var tinyModelHash = crypto.Keccak256Hash([]byte("wqpu-live-tiny-model"))

func verifySlot(ctx context.Context, chain *chainclient.Client, slot int) error {
	if chain == nil || chain.Registry() == nil { return errors.New("verified WQPU chain registry is required") }
	peer, err := chain.Registry().ResolvePeer(ctx, devidentity.PeerID(slot))
	if err != nil { return fmt.Errorf("resolve WQPU devnet slot %d: %w", slot, err) }
	expected, err := devidentity.SessionAddress(slot)
	if err != nil { return err }
	if peer.ControlSession != expected { return fmt.Errorf("WQPU slot %d control session mismatch", slot) }
	return nil
}

func waitHealth(ctx context.Context, inference *computenode.InferenceSession) error {
	if inference == nil { return errors.New("WQPU inference session is required") }
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
		case <-inference.Done(): return errors.New("cross-machine llama-server exited before health")
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
	response, err := (&http.Client{Timeout: 90 * time.Second}).Do(request)
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

func parseExpectedSlots(raw []string, coordinator int) ([]int, error) {
	seen := map[int]struct{}{}
	out := make([]int, 0, len(raw))
	for _, text := range raw {
		slot, err := strconv.Atoi(text)
		if err != nil || !devidentity.ValidSlot(slot) { return nil, fmt.Errorf("invalid expected WQPU slot %q", text) }
		if slot == coordinator { return nil, errors.New("coordinator slot cannot also be a remote provider") }
		if _, exists := seen[slot]; exists { return nil, fmt.Errorf("duplicate expected WQPU slot %d", slot) }
		seen[slot] = struct{}{}
		out = append(out, slot)
	}
	return out, nil
}

func hasModel(provider chainregistry.Provider, model common.Hash) bool {
	for _, candidate := range provider.ModelHashes {
		if candidate == model { return true }
	}
	return false
}

// discoverProviders deliberately obtains the executor set only from chain
// state. The optional devnet slot arguments are assertions for CI/backward
// compatibility; they are never used as the executor source.
func discoverProviders(ctx context.Context, chain *chainclient.Client, localPeer common.Hash, minimum int, expectedSlots []int) ([]chainregistry.Peer, error) {
	if chain == nil || chain.Registry() == nil { return nil, errors.New("verified WQPU chain registry is required") }
	if minimum < 1 { minimum = 1 }
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		peers, err := chain.Registry().ActivePeers(ctx)
		if err != nil { return nil, err }
		candidates := make([]chainregistry.Peer, 0, len(peers))
		for _, peer := range peers {
			if peer.Provider.PeerID == localPeer { continue }
			if !hasModel(peer.Provider, tinyModelHash) { continue }
			candidates = append(candidates, peer)
		}
		sort.Slice(candidates, func(i, j int) bool {
			a, b := candidates[i].Provider, candidates[j].Provider
			if a.ReportedBusyUnits != b.ReportedBusyUnits { return a.ReportedBusyUnits < b.ReportedBusyUnits }
			if a.FreeMemoryBytes != b.FreeMemoryBytes { return a.FreeMemoryBytes > b.FreeMemoryBytes }
			return strings.Compare(a.PeerID.Hex(), b.PeerID.Hex()) < 0
		})

		if len(candidates) >= minimum {
			if len(expectedSlots) > 0 {
				seen := make(map[common.Hash]struct{}, len(candidates))
				for _, peer := range candidates { seen[peer.Provider.PeerID] = struct{}{} }
				for _, slot := range expectedSlots {
					if _, ok := seen[devidentity.PeerID(slot)]; !ok {
						return nil, fmt.Errorf("expected devnet slot %d was not discovered from blockchain registry", slot)
					}
				}
			}
			return candidates, nil
		}
		select {
		case <-ctx.Done(): return nil, fmt.Errorf("waiting for %d WQPU blockchain providers: %w", minimum, ctx.Err())
		case <-ticker.C:
		}
	}
}

func run(rpcURL string, coordinator int, listenEndpoint, runtimeBase string, expectedSlots []int) error {
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Minute)
	defer cancel()
	chain, err := chainclient.DialDev(ctx, rpcURL)
	if err != nil { return err }
	defer chain.Close()

	if err := verifySlot(ctx, chain, coordinator); err != nil { return err }
	for _, slot := range expectedSlots {
		if err := verifySlot(ctx, chain, slot); err != nil { return err }
	}

	minimum := 2
	if len(expectedSlots) > minimum { minimum = len(expectedSlots) }
	providers, err := discoverProviders(ctx, chain, devidentity.PeerID(coordinator), minimum, expectedSlots)
	if err != nil { return err }
	fmt.Fprintf(os.Stderr, "WQPU: blockchain registry discovered %d compute providers\n", len(providers))
	for _, peer := range providers {
		fmt.Fprintf(os.Stderr, "WQPU: peer=%s busy=%d/%d memory=%d endpoints=%s\n", peer.Provider.PeerID.Hex(), peer.Provider.ReportedBusyUnits, peer.Provider.CapacityUnits, peer.Provider.FreeMemoryBytes, strings.Join(peer.Provider.Endpoints, ","))
	}

	key, err := devidentity.SessionKey(coordinator)
	if err != nil { return err }
	node, err := computenode.Start(ctx, computenode.Config{
		RuntimeBase: runtimeBase,
		Signer: key,
		ChainID: chainclient.DevWQPUChainID,
		LocalPeerID: devidentity.PeerID(coordinator),
		Registry: chain.Registry(),
		ListenEndpoint: listenEndpoint,
		MaxConnections: 32,
		RPCPort: devidentity.RPCPort(coordinator),
		RPCThreads: 1,
		RPCDevices: []string{"CPU"},
		RPCOutput: os.Stderr,
		BackendReady: 45 * time.Second,
	})
	if err != nil { return err }
	defer node.Close()

	remotePeerIDs := make([]common.Hash, len(providers))
	tensorSplit := make([]uint64, len(providers))
	parts := make([]string, len(providers))
	for index, peer := range providers {
		remotePeerIDs[index] = peer.Provider.PeerID
		tensorSplit[index] = 1
		parts[index] = peer.Provider.PeerID.Hex()
	}
	apiPort := 8083 + coordinator
	inference, err := node.StartHFFileInference(ctx, remotePeerIDs, apiPort, tinyRepo, tinyModelFile, computenode.InferenceTuning{
		GPULayers: 99,
		ContextSize: 128,
		Parallel: 1,
		Threads: 1,
		SplitMode: "layer",
		TensorSplit: tensorSplit,
	}, os.Stderr, 90*time.Second)
	if err != nil { return err }
	defer inference.Close()
	if err := waitHealth(ctx, inference); err != nil { return err }
	text, err := completion(ctx, inference.APIURL()+"/completion")
	if err != nil { return err }

	fmt.Printf("CROSS MACHINE COMPUTE PASSED: coordinator=%d source=blockchain-registry peers=%s completion=%q\n", coordinator, strings.Join(parts, ","), text)
	return nil
}

func main() {
	if len(os.Args) < 5 {
		fmt.Fprintln(os.Stderr, "usage: wqpu-dev-infer RPC_URL COORDINATOR_SLOT LISTEN_ENDPOINT RUNTIME_BASE [EXPECTED_DEV_SLOT...]")
		os.Exit(2)
	}
	coordinator, err := strconv.Atoi(os.Args[2])
	if err != nil || !devidentity.ValidSlot(coordinator) {
		fmt.Fprintln(os.Stderr, "invalid WQPU coordinator slot")
		os.Exit(2)
	}
	expectedSlots, err := parseExpectedSlots(os.Args[5:], coordinator)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if err := run(os.Args[1], coordinator, os.Args[3], os.Args[4], expectedSlots); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
