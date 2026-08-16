package transport

import (
	"bytes"
	"encoding/binary"
	"io"
	"net"
	"testing"
	"time"

	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

type acceptResult struct {
	stream *SecureStream
	err error
}

func securePipe(t *testing.T) (*SecureStream, *SecureStream) {
	t.Helper()
	leftRaw, rightRaw := net.Pipe()
	leftKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	rightKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	result := make(chan acceptResult, 1)
	go func() {
		stream, err := Accept(rightRaw, rightKey, "wqpu-dev-1", testPeer(2), testPeer(1), leftKey.Address())
		result <- acceptResult{stream: stream, err: err}
	}()
	left, err := Initiate(leftRaw, leftKey, "wqpu-dev-1", testPeer(1), testPeer(2), rightKey.Address())
	if err != nil { t.Fatal(err) }
	select {
	case got := <-result:
		if got.err != nil { t.Fatal(got.err) }
		return left, got.stream
	case <-time.After(5 * time.Second):
		t.Fatal("secure stream accept timed out")
		return nil, nil
	}
}

func TestSecureStreamBidirectional(t *testing.T) {
	left, right := securePipe(t)
	defer left.Close()
	defer right.Close()

	writeDone := make(chan error, 1)
	go func() {
		_, err := left.Write([]byte("hello encrypted peer"))
		writeDone <- err
	}()
	buf := make([]byte, len("hello encrypted peer"))
	if _, err := io.ReadFull(right, buf); err != nil { t.Fatal(err) }
	if string(buf) != "hello encrypted peer" { t.Fatalf("right got %q", buf) }
	if err := <-writeDone; err != nil { t.Fatal(err) }

	replyDone := make(chan error, 1)
	go func() {
		_, err := right.Write([]byte("encrypted reply"))
		replyDone <- err
	}()
	buf = make([]byte, len("encrypted reply"))
	if _, err := io.ReadFull(left, buf); err != nil { t.Fatal(err) }
	if string(buf) != "encrypted reply" { t.Fatalf("left got %q", buf) }
	if err := <-replyDone; err != nil { t.Fatal(err) }
}

func TestSecureStreamChunksLargeWrites(t *testing.T) {
	left, right := securePipe(t)
	defer left.Close()
	defer right.Close()

	payload := bytes.Repeat([]byte{0x5a}, MaxPlaintextBytes*2+12345)
	writeDone := make(chan error, 1)
	go func() {
		n, err := left.Write(payload)
		if err == nil && n != len(payload) { err = io.ErrShortWrite }
		writeDone <- err
	}()
	got := make([]byte, len(payload))
	if _, err := io.ReadFull(right, got); err != nil { t.Fatal(err) }
	if err := <-writeDone; err != nil { t.Fatal(err) }
	if !bytes.Equal(got, payload) { t.Fatal("large secure stream payload changed") }
}

func TestSecureStreamRejectsWrongChainAuthorizedSession(t *testing.T) {
	leftRaw, rightRaw := net.Pipe()
	defer leftRaw.Close()
	defer rightRaw.Close()
	leftKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	rightKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	wrongKey, err := sessionkey.Generate(); if err != nil { t.Fatal(err) }
	result := make(chan error, 1)
	go func() {
		_, err := Accept(rightRaw, rightKey, "wqpu-dev-1", testPeer(2), testPeer(1), wrongKey.Address())
		result <- err
	}()
	_, initErr := Initiate(leftRaw, leftKey, "wqpu-dev-1", testPeer(1), testPeer(2), rightKey.Address())
	if initErr == nil {
		// Responder should close/fail before replying. If the in-memory pipe happens
		// to leave the initiator blocked until close, the deferred close handles it.
		t.Fatal("initiator unexpectedly established against responder that rejected its session")
	}
	select {
	case err := <-result:
		if err == nil { t.Fatal("responder accepted unauthorized control session") }
	case <-time.After(5 * time.Second):
		t.Fatal("responder rejection timed out")
	}
}

func TestSecureStreamRejectsOversizedWireFrameBeforeAllocation(t *testing.T) {
	left, right := securePipe(t)
	defer left.Close()
	defer right.Close()

	// Bypass the sender wrapper only to emulate a malicious carrier peer. The
	// receiver must reject the length prefix without allocating attacker size.
	var prefix [4]byte
	binary.BigEndian.PutUint32(prefix[:], uint32(maxEncryptedFrameBytes+1))
	writeDone := make(chan error, 1)
	go func() { writeDone <- writeAll(left.raw, prefix[:]) }()
	buf := make([]byte, 1)
	if _, err := right.Read(buf); err == nil { t.Fatal("oversized encrypted wire frame should fail") }
	if err := <-writeDone; err != nil { t.Fatal(err) }
}
