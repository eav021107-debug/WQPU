package rpctunnel

import (
	"context"
	"io"
	"net"
	"testing"
	"time"
)

func TestValidateLoopbackTarget(t *testing.T) {
	for _, target := range []string{"127.0.0.1:50052", "[::1]:50052"} {
		if err := ValidateLoopbackTarget(target); err != nil { t.Fatalf("%s: %v", target, err) }
	}
	for _, target := range []string{"0.0.0.0:50052", "192.0.2.10:50052", "localhost:50052", "127.0.0.1:0", "127.0.0.1"} {
		if err := ValidateLoopbackTarget(target); err == nil { t.Fatalf("unsafe target accepted: %s", target) }
	}
}

func TestBridgeToLoopbackCarriesBidirectionalRPCBytes(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil { t.Fatal(err) }
	defer listener.Close()

	serverDone := make(chan error, 1)
	go func() {
		conn, err := listener.Accept()
		if err != nil { serverDone <- err; return }
		defer conn.Close()
		buf := make([]byte, 4)
		if _, err := io.ReadFull(conn, buf); err != nil { serverDone <- err; return }
		if string(buf) != "ping" { serverDone <- io.ErrUnexpectedEOF; return }
		_, err = conn.Write([]byte("pong"))
		serverDone <- err
	}()

	clientSide, bridgeSide := net.Pipe()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	bridgeDone := make(chan error, 1)
	go func() { bridgeDone <- BridgeToLoopback(ctx, bridgeSide, listener.Addr().String()) }()

	if _, err := clientSide.Write([]byte("ping")); err != nil { t.Fatal(err) }
	buf := make([]byte, 4)
	if _, err := io.ReadFull(clientSide, buf); err != nil { t.Fatal(err) }
	if string(buf) != "pong" { t.Fatalf("got %q", buf) }
	_ = clientSide.Close()
	if err := <-serverDone; err != nil { t.Fatal(err) }
	select {
	case err := <-bridgeDone:
		if err != nil && err != context.Canceled { t.Fatal(err) }
	case <-time.After(2 * time.Second):
		t.Fatal("RPC bridge did not terminate")
	}
}

func TestBridgeRejectsNonLoopbackWithoutDialing(t *testing.T) {
	left, right := net.Pipe()
	defer left.Close()
	if err := BridgeToLoopback(context.Background(), right, "192.0.2.10:50052"); err == nil {
		t.Fatal("non-loopback RPC target should fail")
	}
}
