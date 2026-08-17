package sessionkey

import (
	"strings"
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func TestGenerateSignRecover(t *testing.T) {
	key, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	digest := crypto.Keccak256([]byte("wqpu-session-test"))
	sig, err := key.SignDigest(digest)
	if err != nil {
		t.Fatal(err)
	}
	recovered, err := RecoverAddress(digest, sig)
	if err != nil {
		t.Fatal(err)
	}
	if recovered != key.Address() {
		t.Fatalf("recovered=%s want=%s", recovered, key.Address())
	}
}

func TestSignatureDoesNotRecoverForDifferentDigest(t *testing.T) {
	key, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	digest := crypto.Keccak256([]byte("one"))
	sig, err := key.SignDigest(digest)
	if err != nil {
		t.Fatal(err)
	}
	recovered, err := RecoverAddress(crypto.Keccak256([]byte("two")), sig)
	if err != nil {
		t.Fatal(err)
	}
	if recovered == key.Address() {
		t.Fatal("signature recovered the same session for a different digest")
	}
}

func TestGeneratedSessionAddressesAreDifferentAndCanonical(t *testing.T) {
	a, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	b, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	if a.Address() == b.Address() {
		t.Fatal("independent sessions reused the same address")
	}
	if len(a.Address()) != 42 || a.Address() != strings.ToLower(a.Address()) || !strings.HasPrefix(a.Address(), "0x") {
		t.Fatalf("non-canonical session address: %s", a.Address())
	}
}

func TestRejectsNonDigestSigning(t *testing.T) {
	key, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	if _, err := key.SignDigest([]byte("not-32-bytes")); err == nil {
		t.Fatal("non-digest input should be rejected")
	}
}
