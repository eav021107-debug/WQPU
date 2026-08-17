package rpctunnel

import (
	"context"
	"errors"
	"io"
	"net"
	"strconv"
	"strings"
	"sync"
)

func ValidateLoopbackTarget(target string) error {
	host, portText, err := net.SplitHostPort(target)
	if err != nil { return errors.New("WQPU RPC target must be host:port") }
	if strings.Contains(host, "%") { return errors.New("scoped loopback addresses are not allowed") }
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() { return errors.New("WQPU RPC target must be a literal loopback address") }
	port, err := strconv.ParseUint(portText, 10, 16)
	if err != nil || port == 0 { return errors.New("invalid WQPU RPC target port") }
	return nil
}

func normalizeCopyError(err error) error {
	if err == nil || errors.Is(err, io.EOF) || errors.Is(err, net.ErrClosed) { return nil }
	return err
}

// BridgeToLoopback connects one authenticated WQPU stream to exactly one fixed
// local llama.cpp RPC listener. The remote peer cannot select a hostname, port,
// file path, or service; target is local configuration owned by this node.
func BridgeToLoopback(ctx context.Context, secure io.ReadWriteCloser, target string) error {
	if secure == nil { return errors.New("WQPU secure RPC stream is required") }
	if err := ValidateLoopbackTarget(target); err != nil { _ = secure.Close(); return err }
	dialer := net.Dialer{}
	local, err := dialer.DialContext(ctx, "tcp", target)
	if err != nil { _ = secure.Close(); return err }

	var once sync.Once
	closeBoth := func() {
		once.Do(func() {
			_ = secure.Close()
			_ = local.Close()
		})
	}
	defer closeBoth()

	errCh := make(chan error, 2)
	go func() { _, err := io.Copy(local, secure); errCh <- normalizeCopyError(err) }()
	go func() { _, err := io.Copy(secure, local); errCh <- normalizeCopyError(err) }()

	select {
	case <-ctx.Done():
		closeBoth()
		<-errCh
		return ctx.Err()
	case err := <-errCh:
		closeBoth()
		// Drain the second copier after closing both sides so no goroutine leaks.
		<-errCh
		return err
	}
}
