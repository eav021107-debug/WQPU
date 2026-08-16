package sessionkey

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/hex"
	"errors"
)

// Key is a short-lived local WQPU session identity. It is not a user wallet.
// The private half is deliberately kept in memory and is never exported.
type Key struct {
	private ed25519.PrivateKey
	public  ed25519.PublicKey
}

func Generate() (*Key, error) {
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return nil, err
	}
	return &Key{
		private: append(ed25519.PrivateKey(nil), priv...),
		public:  append(ed25519.PublicKey(nil), pub...),
	}, nil
}

func (k *Key) Public() ed25519.PublicKey {
	if k == nil {
		return nil
	}
	return append(ed25519.PublicKey(nil), k.public...)
}

func (k *Key) PublicHex() string {
	if k == nil {
		return ""
	}
	return "0x" + hex.EncodeToString(k.public)
}

func (k *Key) Sign(message []byte) ([]byte, error) {
	if k == nil || len(k.private) != ed25519.PrivateKeySize {
		return nil, errors.New("session key is unavailable")
	}
	return ed25519.Sign(k.private, message), nil
}

func Verify(publicKey, message, signature []byte) bool {
	if len(publicKey) != ed25519.PublicKeySize || len(signature) != ed25519.SignatureSize {
		return false
	}
	return ed25519.Verify(ed25519.PublicKey(publicKey), message, signature)
}
