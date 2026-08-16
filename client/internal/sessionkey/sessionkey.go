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

func Generate() (*Key, error) {
	priv, err := crypto.GenerateKey()
	if err != nil {
		return nil, err
	}
	return &Key{private: priv, address: crypto.PubkeyToAddress(priv.PublicKey)}, nil
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
