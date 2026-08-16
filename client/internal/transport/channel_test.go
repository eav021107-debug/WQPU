package transport

import (
	"bytes"
	"testing"

	"github.com/eav021107-debug/WQPU/client/internal/sessionkey"
)

func testPeer(label byte) [32]byte {
	var id [32]byte
	id[31] = label
	return id
}

func testPair(t *testing.T) (*Handshake, *Handshake, *sessionkey.Key, *sessionkey.Key) {
	t.Helper()
	leftKey, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	rightKey, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	left, err := NewHandshake(RoleInitiator, leftKey, "wqpu-dev-1", testPeer(1))
	if err != nil { t.Fatal(err) }
	right, err := NewHandshake(RoleResponder, rightKey, "wqpu-dev-1", testPeer(2))
	if err != nil { t.Fatal(err) }
	return left, right, leftKey, rightKey
}

func TestHandshakeAndEncryptedFramesBothDirections(t *testing.T) {
	left, right, leftKey, rightKey := testPair(t)
	leftChannel, err := left.Establish(right.Hello(), testPeer(2), rightKey.Address())
	if err != nil { t.Fatal(err) }
	rightChannel, err := right.Establish(left.Hello(), testPeer(1), leftKey.Address())
	if err != nil { t.Fatal(err) }

	frame, err := leftChannel.Seal([]byte("rpc request chunk"))
	if err != nil { t.Fatal(err) }
	plain, err := rightChannel.Open(frame)
	if err != nil { t.Fatal(err) }
	if string(plain) != "rpc request chunk" { t.Fatalf("plain=%q", plain) }

	reply, err := rightChannel.Seal([]byte("rpc response chunk"))
	if err != nil { t.Fatal(err) }
	plain, err = leftChannel.Open(reply)
	if err != nil { t.Fatal(err) }
	if string(plain) != "rpc response chunk" { t.Fatalf("plain=%q", plain) }
}

func TestHelloBinaryRoundTrip(t *testing.T) {
	left, _, _, _ := testPair(t)
	encoded := left.Bytes()
	decoded, err := ParseHello(encoded)
	if err != nil { t.Fatal(err) }
	if decoded.ChainID != left.Hello().ChainID || decoded.PeerID != left.Hello().PeerID || decoded.Session != left.Hello().Session || decoded.Role != RoleInitiator {
		t.Fatalf("decoded=%+v", decoded)
	}
	if !bytes.Equal(decoded.Signature[:], left.Hello().Signature[:]) { t.Fatal("signature changed during hello round trip") }
	if _, err := ParseHello(append(encoded, 0)); err == nil { t.Fatal("trailing hello bytes should fail") }
}

func TestTamperedHelloSignatureFails(t *testing.T) {
	left, right, _, rightKey := testPair(t)
	tampered := right.Hello()
	tampered.Nonce[0] ^= 0x80
	if _, err := left.Establish(tampered, testPeer(2), rightKey.Address()); err == nil {
		t.Fatal("tampered signed hello should fail")
	}
}

func TestWrongExpectedControlSessionFails(t *testing.T) {
	left, right, _, _ := testPair(t)
	other, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	if _, err := left.Establish(right.Hello(), testPeer(2), other.Address()); err == nil {
		t.Fatal("remote hello should be bound to chain-authorized control session")
	}
}

func TestWrongExpectedPeerFails(t *testing.T) {
	left, right, _, rightKey := testPair(t)
	if _, err := left.Establish(right.Hello(), testPeer(3), rightKey.Address()); err == nil {
		t.Fatal("remote hello should be bound to expected peer id")
	}
}

func TestHandshakeRoleReflectionFails(t *testing.T) {
	leftKey, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	rightKey, err := sessionkey.Generate()
	if err != nil { t.Fatal(err) }
	left, err := NewHandshake(RoleInitiator, leftKey, "wqpu-dev-1", testPeer(1))
	if err != nil { t.Fatal(err) }
	reflected, err := NewHandshake(RoleInitiator, rightKey, "wqpu-dev-1", testPeer(2))
	if err != nil { t.Fatal(err) }
	if _, err := left.Establish(reflected.Hello(), testPeer(2), rightKey.Address()); err == nil {
		t.Fatal("initiator hello must not be accepted as responder hello")
	}
}

func TestReplayAndOutOfOrderFramesFail(t *testing.T) {
	left, right, leftKey, rightKey := testPair(t)
	leftChannel, err := left.Establish(right.Hello(), testPeer(2), rightKey.Address())
	if err != nil { t.Fatal(err) }
	rightChannel, err := right.Establish(left.Hello(), testPeer(1), leftKey.Address())
	if err != nil { t.Fatal(err) }

	first, err := leftChannel.Seal([]byte("one")); if err != nil { t.Fatal(err) }
	second, err := leftChannel.Seal([]byte("two")); if err != nil { t.Fatal(err) }
	if _, err := rightChannel.Open(second); err == nil { t.Fatal("out-of-order frame should fail") }
	plain, err := rightChannel.Open(first); if err != nil { t.Fatal(err) }
	if string(plain) != "one" { t.Fatalf("plain=%q", plain) }
	if _, err := rightChannel.Open(first); err == nil { t.Fatal("replayed frame should fail") }
	plain, err = rightChannel.Open(second); if err != nil { t.Fatal(err) }
	if string(plain) != "two" { t.Fatalf("plain=%q", plain) }
}

func TestCiphertextTamperDoesNotAdvanceReceiveSequence(t *testing.T) {
	left, right, leftKey, rightKey := testPair(t)
	leftChannel, err := left.Establish(right.Hello(), testPeer(2), rightKey.Address())
	if err != nil { t.Fatal(err) }
	rightChannel, err := right.Establish(left.Hello(), testPeer(1), leftKey.Address())
	if err != nil { t.Fatal(err) }
	frame, err := leftChannel.Seal([]byte("auth me")); if err != nil { t.Fatal(err) }
	tampered := append([]byte(nil), frame...)
	tampered[len(tampered)-1] ^= 1
	if _, err := rightChannel.Open(tampered); err == nil { t.Fatal("tampered ciphertext should fail") }
	plain, err := rightChannel.Open(frame)
	if err != nil { t.Fatal(err) }
	if string(plain) != "auth me" { t.Fatalf("plain=%q", plain) }
}

func TestFrameSizeLimit(t *testing.T) {
	left, right, _, rightKey := testPair(t)
	channel, err := left.Establish(right.Hello(), testPeer(2), rightKey.Address())
	if err != nil { t.Fatal(err) }
	if _, err := channel.Seal(make([]byte, MaxPlaintextBytes+1)); err == nil {
		t.Fatal("oversized plaintext should fail")
	}
}
