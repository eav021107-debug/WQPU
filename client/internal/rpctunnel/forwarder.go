package rpctunnel

import (
	"context"
	"errors"
	"io"
	"net"
	"sync"
)

type RemoteDial func(context.Context) (io.ReadWriteCloser, error)

type Forwarder struct {
	listener net.Listener
	cancel context.CancelFunc
	done chan error
	once sync.Once
}

func bridgeStreams(ctx context.Context, left, right io.ReadWriteCloser) error {
	if left == nil || right == nil { return errors.New("WQPU RPC bridge requires both streams") }
	var once sync.Once
	closeBoth := func() { once.Do(func() { _ = left.Close(); _ = right.Close() }) }
	defer closeBoth()
	errCh := make(chan error, 2)
	go func() { _, err := io.Copy(left, right); errCh <- normalizeCopyError(err) }()
	go func() { _, err := io.Copy(right, left); errCh <- normalizeCopyError(err) }()
	select {
	case <-ctx.Done():
		closeBoth(); <-errCh; return ctx.Err()
	case err := <-errCh:
		closeBoth(); <-errCh; return err
	}
}

func StartLocalForwarder(parent context.Context, dial RemoteDial) (*Forwarder, error) {
	if dial == nil { return nil, errors.New("WQPU remote RPC dialer is required") }
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { return nil, err }
	ctx, cancel := context.WithCancel(parent)
	f := &Forwarder{listener: listener, cancel: cancel, done: make(chan error, 1)}
	go f.serve(ctx, dial)
	return f, nil
}

func (f *Forwarder) serve(ctx context.Context, dial RemoteDial) {
	for {
		local, err := f.listener.Accept()
		if err != nil {
			if ctx.Err() != nil || errors.Is(err, net.ErrClosed) { f.done <- nil } else { f.done <- err }
			return
		}
		go func() {
			remote, err := dial(ctx)
			if err != nil { _ = local.Close(); return }
			_ = bridgeStreams(ctx, local, remote)
		}()
	}
}

func (f *Forwarder) Address() string {
	if f == nil || f.listener == nil { return "" }
	return f.listener.Addr().String()
}

func (f *Forwarder) Done() <-chan error {
	if f == nil { ch := make(chan error); close(ch); return ch }
	return f.done
}

func (f *Forwarder) Close() error {
	if f == nil { return nil }
	var err error
	f.once.Do(func() {
		f.cancel()
		err = f.listener.Close()
	})
	return err
}
