package rpcmesh

import (
	"context"
	"errors"
	"fmt"
	"net"
	"sync"
	"sync/atomic"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/carrier"
	"github.com/eav021107-debug/WQPU/client/internal/peertransport"
	"github.com/eav021107-debug/WQPU/client/internal/rpctunnel"
	"github.com/eav021107-debug/WQPU/client/internal/transport"
)

const (
	DefaultMaxConnections = 32
	MaxConnectionLimit     = 1024
	providerErrorBuffer    = 64
)

type ProviderConfig struct {
	Signer         transport.Signer
	ChainID        string
	LocalPeerID    common.Hash
	Registry       peertransport.Resolver
	RPCTarget      string
	MaxConnections int
}

type ProviderService struct {
	listener net.Listener
	config   ProviderConfig
	ctx      context.Context
	cancel   context.CancelFunc
	sem      chan struct{}
	errors   chan error
	done     chan struct{}
	once     sync.Once
	workers  sync.WaitGroup
	active   atomic.Int64
}

func validateProviderConfig(config ProviderConfig) (ProviderConfig, error) {
	if config.Signer == nil { return ProviderConfig{}, errors.New("WQPU RPC provider signer is required") }
	if config.ChainID == "" { return ProviderConfig{}, errors.New("WQPU RPC provider chain id is required") }
	if config.LocalPeerID == (common.Hash{}) { return ProviderConfig{}, errors.New("WQPU RPC provider peer id is required") }
	if config.Registry == nil { return ProviderConfig{}, errors.New("WQPU RPC provider chain registry is required") }
	if err := rpctunnel.ValidateLoopbackTarget(config.RPCTarget); err != nil { return ProviderConfig{}, fmt.Errorf("WQPU RPC provider target: %w", err) }
	if config.MaxConnections == 0 { config.MaxConnections = DefaultMaxConnections }
	if config.MaxConnections < 1 || config.MaxConnections > MaxConnectionLimit {
		return ProviderConfig{}, fmt.Errorf("WQPU RPC provider connection limit must be within 1..%d", MaxConnectionLimit)
	}
	return config, nil
}

func StartProvider(parent context.Context, endpoint string, config ProviderConfig) (*ProviderService, error) {
	if parent == nil { return nil, errors.New("WQPU RPC provider context is required") }
	validated, err := validateProviderConfig(config)
	if err != nil { return nil, err }
	listener, err := carrier.Listen(parent, endpoint)
	if err != nil { return nil, err }
	service, err := StartProviderOnListener(parent, listener, validated)
	if err != nil { _ = listener.Close(); return nil, err }
	return service, nil
}

func StartProviderOnListener(parent context.Context, listener net.Listener, config ProviderConfig) (*ProviderService, error) {
	if parent == nil { return nil, errors.New("WQPU RPC provider context is required") }
	if listener == nil { return nil, errors.New("WQPU RPC provider listener is required") }
	validated, err := validateProviderConfig(config)
	if err != nil { return nil, err }
	select {
	case <-parent.Done(): return nil, parent.Err()
	default:
	}
	ctx, cancel := context.WithCancel(parent)
	service := &ProviderService{
		listener: listener,
		config: validated,
		ctx: ctx,
		cancel: cancel,
		sem: make(chan struct{}, validated.MaxConnections),
		errors: make(chan error, providerErrorBuffer),
		done: make(chan struct{}),
	}
	go service.serve()
	return service, nil
}

func (s *ProviderService) report(err error) {
	if err == nil { return }
	select {
	case s.errors <- err:
	default:
	}
}

func (s *ProviderService) serve() {
	defer func() {
		s.workers.Wait()
		close(s.errors)
		close(s.done)
	}()
	for {
		raw, err := s.listener.Accept()
		if err != nil {
			if s.ctx.Err() != nil || errors.Is(err, net.ErrClosed) { return }
			s.report(fmt.Errorf("WQPU RPC provider accept: %w", err))
			return
		}
		select {
		case s.sem <- struct{}{}:
			s.active.Add(1)
			s.workers.Add(1)
			go s.handle(raw)
		default:
			_ = raw.Close()
			s.report(errors.New("WQPU RPC provider rejected connection: active tunnel limit reached"))
		}
	}
}

func (s *ProviderService) handle(raw net.Conn) {
	defer func() {
		<-s.sem
		s.active.Add(-1)
		s.workers.Done()
	}()
	connection, err := peertransport.Accept(s.ctx, raw, s.config.Signer, s.config.ChainID, s.config.LocalPeerID, s.config.Registry)
	if err != nil {
		if s.ctx.Err() == nil { s.report(fmt.Errorf("WQPU RPC provider authenticate: %w", err)) }
		return
	}
	defer connection.Stream.Close()
	if err := rpctunnel.BridgeToLoopback(s.ctx, connection.Stream, s.config.RPCTarget); err != nil && s.ctx.Err() == nil {
		s.report(fmt.Errorf("WQPU RPC provider bridge: %w", err))
	}
}

func (s *ProviderService) Endpoint() string {
	if s == nil || s.listener == nil { return "" }
	return "wqpu://" + s.listener.Addr().String()
}

func (s *ProviderService) ActiveConnections() int {
	if s == nil { return 0 }
	return int(s.active.Load())
}

func (s *ProviderService) Errors() <-chan error {
	if s == nil {
		ch := make(chan error)
		close(ch)
		return ch
	}
	return s.errors
}

func (s *ProviderService) Done() <-chan struct{} {
	if s == nil {
		ch := make(chan struct{})
		close(ch)
		return ch
	}
	return s.done
}

func (s *ProviderService) Close() error {
	if s == nil { return nil }
	var closeErr error
	s.once.Do(func() {
		s.cancel()
		closeErr = s.listener.Close()
	})
	<-s.done
	if errors.Is(closeErr, net.ErrClosed) { return nil }
	return closeErr
}
