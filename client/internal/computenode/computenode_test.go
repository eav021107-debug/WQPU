package computenode

import (
	"context"
	"errors"
	"net"
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

func computePeer(last byte) common.Hash { var id common.Hash; id[31] = last; return id }

func TestValidateConfigSetsSafeDefaults(t *testing.T) {
	key, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	listener, err := net.Listen("tcp", "127.0.0.1:0"); if err != nil { t.Fatal(err) }
	defer listener.Close()
	config, err := validateConfig(Config{
		RuntimeBase: t.TempDir(), Signer: key, ChainID: "wqpu-test", LocalPeerID: computePeer(1),
		Registry: fakeRegistry{}, Listener: listener,
	})
	if err != nil { t.Fatal(err) }
	if config.RPCPort != DefaultRPCPort || config.RPCThreads != DefaultRPCThreads || config.BackendReady != DefaultBackendReady {
		t.Fatalf("defaults=%+v", config)
	}
	if config.Dialer == nil || config.Installer == nil { t.Fatal("default dialer/installer missing") }
	if _, ok := config.Dialer.(carrier.TCPDialer); !ok { t.Fatalf("dialer=%T", config.Dialer) }
}

func TestValidateConfigRejectsMissingIdentityAndBadRPC(t *testing.T) {
	key, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	base := Config{RuntimeBase: t.TempDir(), Signer: key, ChainID: "wqpu-test", LocalPeerID: computePeer(1), Registry: fakeRegistry{}, ListenEndpoint: "wqpu://127.0.0.1:7443"}
	bad := base; bad.RuntimeBase = ""; if _, err := validateConfig(bad); err == nil { t.Fatal("missing runtime base should fail") }
	bad = base; bad.Signer = nil; if _, err := validateConfig(bad); err == nil { t.Fatal("missing signer should fail") }
	bad = base; bad.LocalPeerID = common.Hash{}; if _, err := validateConfig(bad); err == nil { t.Fatal("missing peer id should fail") }
	bad = base; bad.Registry = nil; if _, err := validateConfig(bad); err == nil { t.Fatal("missing registry should fail") }
	bad = base; bad.ListenEndpoint = ""; if _, err := validateConfig(bad); err == nil { t.Fatal("missing listener should fail") }
	bad = base; bad.RPCPort = 70000; if _, err := validateConfig(bad); err == nil { t.Fatal("bad RPC port should fail") }
	bad = base; bad.RPCThreads = -1; if _, err := validateConfig(bad); err == nil { t.Fatal("bad RPC thread count should fail") }
}

func TestRemotePeerValidationMatchesConsensusLimit(t *testing.T) {
	local := computePeer(1)
	if err := validateRemotePeers(local, []common.Hash{computePeer(2), computePeer(3)}); err != nil { t.Fatal(err) }
	if err := validateRemotePeers(local, nil); err == nil { t.Fatal("empty remote set should fail") }
	if err := validateRemotePeers(local, []common.Hash{local}); err == nil { t.Fatal("self peer should fail") }
	if err := validateRemotePeers(local, []common.Hash{computePeer(2), computePeer(2)}); err == nil { t.Fatal("duplicate peer should fail") }
	tooMany := make([]common.Hash, MaxInferencePeers+1)
	for index := range tooMany { tooMany[index] = computePeer(byte(index + 2)) }
	if err := validateRemotePeers(local, tooMany); err == nil { t.Fatal("more than consensus peer limit should fail") }
}

func TestRuntimeTuningOwnsRPCDeviceMapping(t *testing.T) {
	tuning := runtimeTuning(2, InferenceTuning{TensorSplit: []uint64{1, 1}})
	if len(tuning.Devices) != 2 || tuning.Devices[0] != "RPC0" || tuning.Devices[1] != "RPC1" { t.Fatalf("devices=%v", tuning.Devices) }
	if tuning.GPULayers != DefaultRemoteLayers { t.Fatalf("gpu layers=%d", tuning.GPULayers) }
	if tuning.SplitMode != "layer" { t.Fatalf("split mode=%q", tuning.SplitMode) }
	if len(tuning.TensorSplit) != 2 || tuning.TensorSplit[0] != 1 || tuning.TensorSplit[1] != 1 { t.Fatalf("tensor split=%v", tuning.TensorSplit) }

	tuning = runtimeTuning(1, InferenceTuning{GPULayers: 17, SplitMode: "none"})
	if tuning.Devices[0] != "RPC0" || tuning.GPULayers != 17 || tuning.SplitMode != "none" { t.Fatalf("single tuning=%+v", tuning) }
}

func TestInferenceTuningFailsBeforeNetworkUse(t *testing.T) {
	if err := validateInferenceTuning(2, 8080, InferenceTuning{TensorSplit: []uint64{1}}); err == nil { t.Fatal("tensor split mismatch should fail") }
	if err := validateInferenceTuning(2, 8080, InferenceTuning{TensorSplit: []uint64{1, 0}}); err == nil { t.Fatal("zero split should fail") }
	if err := validateInferenceTuning(2, 8080, InferenceTuning{SplitMode: "mystery"}); err == nil { t.Fatal("unknown split mode should fail") }
	if err := validateInferenceTuning(2, 0, InferenceTuning{}); err == nil { t.Fatal("invalid API port should fail") }
	if err := validateInferenceTuning(2, 8080, InferenceTuning{GPULayers: -1}); err == nil { t.Fatal("negative tuning should fail") }
}

func TestInferenceContextStopsWithCaller(t *testing.T) {
	nodeCtx, nodeCancel := context.WithCancel(context.Background())
	defer nodeCancel()
	parent, parentCancel := context.WithCancel(context.Background())
	ctx, cancel := inferenceContext(nodeCtx, parent)
	defer cancel()
	parentCancel()
	select {
	case <-ctx.Done():
	case <-time.After(time.Second): t.Fatal("inference context ignored caller cancellation")
	}
}
