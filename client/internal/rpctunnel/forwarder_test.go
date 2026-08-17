package rpctunnel

import (
	"context"
	"io"
	"net"
	"testing"
	"time"
)

func TestLocalForwarderPresentsRemoteStreamOnLoopback(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	forwarder, err := StartLocalForwarder(ctx, func(context.Context) (io.ReadWriteCloser, error) {
		client, server := net.Pipe()
		go func() {
			defer server.Close()
			buf := make([]byte, 4)
			if _, err := io.ReadFull(server, buf); err != nil { return }
			if string(buf) == "ping" { _, _ = server.Write([]byte("pong")) }
		}()
		return client, nil
	})
	if err != nil { t.Fatal(err) }
	defer forwarder.Close()

	host, _, err := net.SplitHostPort(forwarder.Address())
	if err != nil { t.Fatal(err) }
	if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() { t.Fatalf("forwarder is not loopback: %s", forwarder.Address()) }

	conn, err := net.DialTimeout("tcp", forwarder.Address(), 2*time.Second)
	if err != nil { t.Fatal(err) }
	defer conn.Close()
	if _, err := conn.Write([]byte("ping")); err != nil { t.Fatal(err) }
	buf := make([]byte, 4)
	if _, err := io.ReadFull(conn, buf); err != nil { t.Fatal(err) }
	if string(buf) != "pong" { t.Fatalf("got %q", buf) }
}

func TestLocalForwarderStopsOnClose(t *testing.T) {
	ctx := context.Background()
	forwarder, err := StartLocalForwarder(ctx, func(context.Context) (io.ReadWriteCloser, error) { return nil, io.EOF })
	if err != nil { t.Fatal(err) }
	address := forwarder.Address()
	if err := forwarder.Close(); err != nil { t.Fatal(err) }
	select {
	case err := <-forwarder.Done():
		if err != nil { t.Fatal(err) }
	case <-time.After(2 * time.Second):
		t.Fatal("forwarder did not stop")
	}
	if conn, err := net.DialTimeout("tcp", address, 200*time.Millisecond); err == nil { conn.Close(); t.Fatal("closed forwarder still accepts TCP") }
}
