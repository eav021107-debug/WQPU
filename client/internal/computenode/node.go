package computenode

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	goruntime "runtime"
	"sync"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/carrier"
	"github.com/eav021107-debug/WQPU/client/internal/llamaruntime"
	"github.com/eav021107-debug/WQPU/client/internal/peertransport"
	"github.com/eav021107-debug/WQPU/client/internal/rpcmesh"
	"github.com/eav021107-debug/WQPU/client/internal/transport"
)

const (
	DefaultRPCPort       = llamaruntime.DefaultRPCPort
	DefaultRPCThreads    = 1
	DefaultDialTimeout   = 10 * time.Second
	DefaultBackendReady  = 30 * time.Second
)

type RuntimeInstaller interface {
	InstallCPU(context.Context, string, string, string) (llamaruntime.Runtime, error)
}

type Config struct {
	RuntimeBase string
	Installer   RuntimeInstaller

	Signer      transport.Signer
	ChainID     string
	LocalPeerID common.Hash
	Registry    peertransport.Resolver

	ListenEndpoint string
	Listener       net.Listener
	MaxConnections int

	RPCPort    int
	RPCThreads int
	RPCDevices []string
	RPCCache   bool
	RPCOutput  io.Writer

	Dialer       carrier.Dialer
	DialTimeout  time.Duration
	BackendReady time.Duration
}

type Node struct {
	ctx     context.Context
	cancel  context.CancelFunc
	config  Config
	runtime llamaruntime.Runtime
	backend *llamaruntime.ManagedProcess
	mesh    *rpcmesh.ProviderService
	once    sync.Once
}

func validateConfig(config Config) (Config, error) {
	if config.RuntimeBase == "" { return Config{}, errors.New("WQPU runtime base directory is required") }
	if config.Signer == nil { return Config{}, errors.New("WQPU compute node session signer is required") }
	if config.ChainID == "" { return Config{}, errors.New("WQPU compute node chain id is required") }
	if config.LocalPeerID == (common.Hash{}) { return Config{}, errors.New("WQPU compute node peer id is required") }
	if config.Registry == nil { return Config{}, errors.New("WQPU compute node chain registry is required") }
	if config.Listener == nil && config.ListenEndpoint == "" { return Config{}, errors.New("WQPU compute node listener or endpoint is required") }
	if config.RPCPort == 0 { config.RPCPort = DefaultRPCPort }
	if config.RPCPort < 1 || config.RPCPort > 65535 { return Config{}, errors.New("WQPU compute node RPC port is invalid") }
	if config.RPCThreads == 0 { config.RPCThreads = DefaultRPCThreads }
	if config.RPCThreads < 1 { return Config{}, errors.New("WQPU compute node RPC thread count is invalid") }
	if config.BackendReady <= 0 { config.BackendReady = DefaultBackendReady }
	if config.Dialer == nil {
		timeout := config.DialTimeout
		if timeout <= 0 { timeout = DefaultDialTimeout }
		config.Dialer = carrier.TCPDialer{Timeout: timeout}
	}
	if config.Installer == nil { config.Installer = llamaruntime.Installer{} }
	return config, nil
}

func Start(parent context.Context, config Config) (*Node, error) {
	if parent == nil { return nil, errors.New("WQPU compute node context is required") }
	validated, err := validateConfig(config)
	if err != nil { return nil, err }
	select {
	case <-parent.Done(): return nil, parent.Err()
	default:
	}
	ctx, cancel := context.WithCancel(parent)
	installed, err := validated.Installer.InstallCPU(ctx, validated.RuntimeBase, goruntime.GOOS, goruntime.GOARCH)
	if err != nil { cancel(); return nil, fmt.Errorf("install WQPU llama runtime: %w", err) }
	backend, err := llamaruntime.StartRPCServer(ctx, installed, validated.RPCPort, validated.RPCThreads, validated.RPCDevices, validated.RPCCache, validated.RPCOutput, validated.BackendReady)
	if err != nil { cancel(); return nil, fmt.Errorf("start WQPU llama RPC backend: %w", err) }

	providerConfig := rpcmesh.ProviderConfig{
		Signer: validated.Signer,
		ChainID: validated.ChainID,
		LocalPeerID: validated.LocalPeerID,
		Registry: validated.Registry,
		RPCTarget: fmt.Sprintf("127.0.0.1:%d", validated.RPCPort),
		MaxConnections: validated.MaxConnections,
	}
	var mesh *rpcmesh.ProviderService
	if validated.Listener != nil {
		mesh, err = rpcmesh.StartProviderOnListener(ctx, validated.Listener, providerConfig)
	} else {
		mesh, err = rpcmesh.StartProvider(ctx, validated.ListenEndpoint, providerConfig)
	}
	if err != nil {
		_ = backend.Close()
		cancel()
		return nil, fmt.Errorf("start WQPU RPC mesh: %w", err)
	}
	node := &Node{ctx: ctx, cancel: cancel, config: validated, runtime: installed, backend: backend, mesh: mesh}
	return node, nil
}

func (n *Node) PeerID() common.Hash {
	if n == nil { return common.Hash{} }
	return n.config.LocalPeerID
}

func (n *Node) ProviderEndpoint() string {
	if n == nil || n.mesh == nil { return "" }
	return n.mesh.Endpoint()
}

func (n *Node) Runtime() llamaruntime.Runtime {
	if n == nil { return llamaruntime.Runtime{} }
	return n.runtime
}

func (n *Node) ProviderErrors() <-chan error {
	if n == nil || n.mesh == nil {
		ch := make(chan error)
		close(ch)
		return ch
	}
	return n.mesh.Errors()
}

func (n *Node) OpenRemote(parent context.Context, remotePeerID common.Hash) (*rpcmesh.Forwarder, error) {
	if n == nil || n.mesh == nil { return nil, errors.New("WQPU compute node is not running") }
	if parent == nil { parent = n.ctx }
	return rpcmesh.OpenForwarder(parent, rpcmesh.ForwarderConfig{
		Signer: n.config.Signer,
		ChainID: n.config.ChainID,
		LocalPeerID: n.config.LocalPeerID,
		RemotePeerID: remotePeerID,
		Registry: n.config.Registry,
		Dialer: n.config.Dialer,
	})
}

func (n *Node) Close() error {
	if n == nil { return nil }
	var first error
	n.once.Do(func() {
		n.cancel()
		if n.mesh != nil {
			if err := n.mesh.Close(); err != nil && first == nil { first = err }
		}
		if n.backend != nil {
			if err := n.backend.Close(); err != nil && first == nil { first = err }
		}
	})
	return first
}
