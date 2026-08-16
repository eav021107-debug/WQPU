package auth

import (
	"encoding/hex"
	"errors"
	"math/big"
	"strconv"
	"strings"

	"github.com/ethereum/go-ethereum/common"
	ethmath "github.com/ethereum/go-ethereum/common/math"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/signer/core/apitypes"

	"github.com/eav021107-debug/WQPU/chain/x/wqpu/kernel"
)

func uint256(value uint64) *ethmath.HexOrDecimal256 {
	var out ethmath.HexOrDecimal256
	if err := out.UnmarshalText([]byte(strconv.FormatUint(value, 10))); err != nil {
		panic(err)
	}
	return &out
}

func sessionPubkeyHex(key [32]byte) string {
	return "0x" + hex.EncodeToString(key[:])
}

// SessionTypedData must stay byte-for-byte semantically equivalent to the
// object shown by the WQPU wallet connector.
func SessionTypedData(d kernel.SessionDelegation, evmChainID uint64) (apitypes.TypedData, error) {
	if err := d.Validate(); err != nil {
		return apitypes.TypedData{}, err
	}
	if evmChainID == 0 {
		return apitypes.TypedData{}, errors.New("EVM chain id must be positive")
	}
	if !common.IsHexAddress(d.Wallet) {
		return apitypes.TypedData{}, errors.New("wallet is not a valid EVM address")
	}

	return apitypes.TypedData{
		Types: apitypes.Types{
			"EIP712Domain": {
				{Name: "name", Type: "string"},
				{Name: "version", Type: "string"},
				{Name: "chainId", Type: "uint256"},
			},
			"WQPUSession": {
				{Name: "wallet", Type: "address"},
				{Name: "sessionPubkey", Type: "bytes32"},
				{Name: "wqpuChainId", Type: "string"},
				{Name: "issuedHeight", Type: "uint64"},
				{Name: "expiresHeight", Type: "uint64"},
				{Name: "maxSpendUnits", Type: "uint256"},
				{Name: "maxJobUnits", Type: "uint256"},
				{Name: "revocationNonce", Type: "uint64"},
				{Name: "permissions", Type: "uint64"},
				{Name: "protocolVersion", Type: "uint32"},
			},
		},
		PrimaryType: "WQPUSession",
		Domain: apitypes.TypedDataDomain{
			Name:    "WQPU Session",
			Version: "1",
			ChainId: uint256(evmChainID),
		},
		Message: apitypes.TypedDataMessage{
			"wallet":          d.Wallet,
			"sessionPubkey":   sessionPubkeyHex(d.SessionPubkey),
			"wqpuChainId":     d.ChainID,
			"issuedHeight":    strconv.FormatUint(d.IssuedHeight, 10),
			"expiresHeight":   strconv.FormatUint(d.ExpiresHeight, 10),
			"maxSpendUnits":   strconv.FormatUint(d.MaxSpendUnits, 10),
			"maxJobUnits":     strconv.FormatUint(d.MaxJobUnits, 10),
			"revocationNonce": strconv.FormatUint(d.RevocationNonce, 10),
			"permissions":     strconv.FormatUint(d.Permissions, 10),
			"protocolVersion": strconv.FormatUint(uint64(d.ProtocolVersion), 10),
		},
	}, nil
}

func SessionDigest(d kernel.SessionDelegation, evmChainID uint64) ([]byte, error) {
	typed, err := SessionTypedData(d, evmChainID)
	if err != nil {
		return nil, err
	}
	digest, _, err := apitypes.TypedDataAndHash(typed)
	if err != nil {
		return nil, err
	}
	if len(digest) != 32 {
		return nil, errors.New("unexpected EIP-712 digest length")
	}
	return digest, nil
}

func decodeEthereumSignature(signatureHex string) ([]byte, error) {
	if !strings.HasPrefix(signatureHex, "0x") {
		return nil, errors.New("signature must be 0x-prefixed")
	}
	sig, err := hex.DecodeString(signatureHex[2:])
	if err != nil || len(sig) != crypto.SignatureLength {
		return nil, errors.New("signature must contain exactly 65 bytes")
	}
	// Browser wallets may return V as 27/28 while go-ethereum expects 0/1.
	if sig[64] == 27 || sig[64] == 28 {
		sig[64] -= 27
	}
	if sig[64] > 1 {
		return nil, errors.New("invalid signature recovery id")
	}
	r := new(big.Int).SetBytes(sig[:32])
	s := new(big.Int).SetBytes(sig[32:64])
	if !crypto.ValidateSignatureValues(sig[64], r, s, true) {
		return nil, errors.New("non-canonical EVM signature")
	}
	return sig, nil
}

// VerifySessionSignature proves that the wallet itself authorized exactly this
// bounded WQPU session. The wallet private key is never available to WQPU.
func VerifySessionSignature(d kernel.SessionDelegation, evmChainID uint64, signatureHex string) error {
	digest, err := SessionDigest(d, evmChainID)
	if err != nil {
		return err
	}
	sig, err := decodeEthereumSignature(signatureHex)
	if err != nil {
		return err
	}
	pub, err := crypto.SigToPub(digest, sig)
	if err != nil {
		return errors.New("cannot recover wallet from signature")
	}
	recovered := crypto.PubkeyToAddress(*pub)
	expected := common.HexToAddress(d.Wallet)
	if recovered != expected {
		return errors.New("session signature was made by a different wallet")
	}
	return nil
}
