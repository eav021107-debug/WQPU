package sessionkey

import (
	"crypto/ecdsa"
	"errors"
	"strings"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

// Key is a short-lived local EVM account used only as an authorized WQPU
// session. It is not the user's wallet and its private key never leaves memory.
type Key struct {
	private *ecdsa.PrivateKey
	address common.Address
}

func fromPrivate(priv *ecdsa.PrivateKey) (*Key, error) {
	if priv == nil || priv.D == nil || priv.D.Sign() <= 0 {
		return nil, errors.New("valid WQPU session private key is required")
	}
	// Re-parse a 32-byte scalar so Key owns an independent in-memory copy and
	// callers cannot mutate the original ecdsa.PrivateKey after construction.
	raw := crypto.FromECDSA(priv)
	if len(raw) != 32 {
		return nil, errors.New("WQPU session private key must be secp256k1")
	}
	copyPriv, err := crypto.ToECDSA(append([]byte(nil), raw...))
	if err != nil {
		return nil, err
	}
	return &Key{private: copyPriv, address: crypto.PubkeyToAddress(copyPriv.PublicKey)}, nil
}

func Generate() (*Key, error) {
	priv, err := crypto.GenerateKey()
	if err != nil {
		return nil, err
	}
	return fromPrivate(priv)
}

// FromPrivateKey constructs a session key from an already-authorized in-memory
// secp256k1 key. It exists for session handoff/devnet determinism; this API must
// never be used with a user's wallet seed or wallet private key.
func FromPrivateKey(priv *ecdsa.PrivateKey) (*Key, error) {
	return fromPrivate(priv)
}

func (k *Key) Address() string {
	if k == nil || k.private == nil {
		return ""
	}
	return strings.ToLower(k.address.Hex())
}

func (k *Key) SignDigest(digest []byte) ([]byte, error) {
	if k == nil || k.private == nil {
		return nil, errors.New("session key is unavailable")
	}
	if len(digest) != 32 {
		return nil, errors.New("EVM session signatures require a 32-byte digest")
	}
	sig, err := crypto.Sign(digest, k.private)
	if err != nil {
		return nil, err
	}
	return append([]byte(nil), sig...), nil
}

func RecoverAddress(digest, signature []byte) (string, error) {
	if len(digest) != 32 || len(signature) != crypto.SignatureLength {
		return "", errors.New("invalid digest or signature length")
	}
	copySig := append([]byte(nil), signature...)
	if copySig[64] == 27 || copySig[64] == 28 {
		copySig[64] -= 27
	}
	if copySig[64] > 1 {
		return "", errors.New("invalid recovery id")
	}
	pub, err := crypto.SigToPub(digest, copySig)
	if err != nil {
		return "", err
	}
	return strings.ToLower(crypto.PubkeyToAddress(*pub).Hex()), nil
}
