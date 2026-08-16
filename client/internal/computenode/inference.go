package computenode

import (
	"context"
	"errors"
	"fmt"
	"io"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/llamaruntime"
	"github.com/eav021107-debug/WQPU/client/internal/rpcmesh"
)

const MaxInferencePeers = 8

type InferenceTuning struct {
	GPULayers   int
	ContextSize int
	Parallel    int
	Threads     int
	SplitMode   string
	TensorSplit []uint64
}

type InferenceSession struct {
	cancel     context.CancelFunc
	server     *llamaruntime.ManagedProcess
	forwarders []*rpcmesh.Forwarder
	apiURL     string
	once       sync.Once
}

func validateRemotePeers(local common.Hash, remotes []common.Hash) error {
	if len(remotes) == 0 || len(remotes) > MaxInferencePeers {
		return fmt.Errorf("WQPU inference requires 1..%d remote peers", MaxInferencePeers)
	}
	seen := make(map[common.Hash]struct{}, len(remotes))
	for _, remote := range remotes {
		if remote == (common.Hash{}) { return errors.New("WQPU inference remote peer id is required") }
		if remote == local { return errors.New("WQPU inference remote list cannot contain the local peer") }
		if _, exists := seen[remote]; exists { return errors.New("WQPU inference remote peers must be unique") }
		seen[remote] = struct{}{}
	}
	return nil
}

func remoteDeviceNames(count int) []string {
	devices := make([]string, count)
	for index := range devices { devices[index] = fmt.Sprintf("RPC%d", index) }
	return devices
}

func runtimeTuning(remoteCount int, tuning InferenceTuning) llamaruntime.ServerTuning {
	splitMode := tuning.SplitMode
	if splitMode == "" && remoteCount > 1 { splitMode = "layer" }
	return llamaruntime.ServerTuning{
		Devices: remoteDeviceNames(remoteCount),
		GPULayers: tuning.GPULayers,
		ContextSize: tuning.ContextSize,
		Parallel: tuning.Parallel,
		Threads: tuning.Threads,
		SplitMode: splitMode,
		TensorSplit: append([]uint64(nil), tuning.TensorSplit...),
	}
}

func inferenceContext(node context.Context, parent context.Context) (context.Context, context.CancelFunc) {
	ctx, cancel := context.WithCancel(node)
	if parent != nil && parent != node {
		go func() {
			select {
			case <-parent.Done(): cancel()
			case <-ctx.Done():
			}
		}()
	}
	return ctx, cancel
}

func (n *Node) StartHFFileInference(parent context.Context, remotes []common.Hash, apiPort int, repo, file string, tuning InferenceTuning, output io.Writer, readiness time.Duration) (*InferenceSession, error) {
	if n == nil || n.mesh == nil { return nil, errors.New("WQPU compute node is not running") }
	if err := validateRemotePeers(n.config.LocalPeerID, remotes); err != nil { return nil, err }
	ctx, cancel := inferenceContext(n.ctx, parent)
	forwarders := make([]*rpcmesh.Forwarder, 0, len(remotes))
	closeForwarders := func() {
		for index := len(forwarders) - 1; index >= 0; index-- { _ = forwarders[index].Close() }
	}
	for _, remote := range remotes {
		forwarder, err := n.OpenRemote(ctx, remote)
		if err != nil {
			closeForwarders()
			cancel()
			return nil, fmt.Errorf("open WQPU RPC forwarder for %s: %w", remote.Hex(), err)
		}
		forwarders = append(forwarders, forwarder)
	}
	endpoints := make([]string, len(forwarders))
	for index, forwarder := range forwarders { endpoints[index] = forwarder.Address() }
	server, err := llamaruntime.StartLlamaServerForHFFile(ctx, n.runtime, apiPort, endpoints, repo, file, runtimeTuning(len(remotes), tuning), output, readiness)
	if err != nil {
		closeForwarders()
		cancel()
		return nil, fmt.Errorf("start WQPU distributed llama-server: %w", err)
	}
	return &InferenceSession{
		cancel: cancel,
		server: server,
		forwarders: forwarders,
		apiURL: fmt.Sprintf("http://127.0.0.1:%d", apiPort),
	}, nil
}

func (s *InferenceSession) APIURL() string {
	if s == nil { return "" }
	return s.apiURL
}

func (s *InferenceSession) Done() <-chan struct{} {
	if s == nil || s.server == nil {
		ch := make(chan struct{})
		close(ch)
		return ch
	}
	return s.server.Done()
}

func (s *InferenceSession) Close() error {
	if s == nil { return nil }
	var first error
	s.once.Do(func() {
		s.cancel()
		if s.server != nil {
			if err := s.server.Close(); err != nil && first == nil { first = err }
		}
		for index := len(s.forwarders) - 1; index >= 0; index-- {
			if err := s.forwarders[index].Close(); err != nil && first == nil { first = err }
		}
	})
	return first
}
