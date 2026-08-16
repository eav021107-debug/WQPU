package sessionkey

import "testing"

func TestGenerateSignVerify(t *testing.T) {
	key, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	message := []byte("wqpu-session-test")
	sig, err := key.Sign(message)
	if err != nil {
		t.Fatal(err)
	}
	if !Verify(key.Public(), message, sig) {
		t.Fatal("valid session signature did not verify")
	}
	if Verify(key.Public(), []byte("different"), sig) {
		t.Fatal("signature verified for different message")
	}
}

func TestGeneratedKeysAreDifferent(t *testing.T) {
	a, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	b, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	if a.PublicHex() == b.PublicHex() {
		t.Fatal("independent sessions reused the same key")
	}
}

func TestPrivateKeyIsNotExposedByAPI(t *testing.T) {
	key, err := Generate()
	if err != nil {
		t.Fatal(err)
	}
	if len(key.Public()) != 32 {
		t.Fatalf("public key length=%d", len(key.Public()))
	}
	if len(key.PublicHex()) != 66 {
		t.Fatalf("public hex length=%d", len(key.PublicHex()))
	}
}
