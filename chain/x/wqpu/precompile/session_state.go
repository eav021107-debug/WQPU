package precompile

import (
	"encoding/binary"
	"encoding/hex"
	"errors"
	"math/big"
	"strconv"
	"strings"

	"github.com/ethereum/go-ethereum/common"
	ethmath "github.com/ethereum/go-ethereum/common/math"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/signer/core/apitypes"
)

const (
	sessionCodecVersion byte = 1
	SessionPermProvider uint64 = 1 << iota
	SessionPermJob
	SessionPermSettle
	SessionAllPermissions = SessionPermProvider | SessionPermJob | SessionPermSettle
)

type SessionDelegation struct {
	WQPUChainID     string
	Wallet          common.Address
	Session         common.Address
	IssuedHeight    uint64
	ExpiresHeight   uint64
	MaxSpendUnits   uint64
	MaxJobUnits     uint64
	RevocationNonce uint64
	Permissions     uint64
	ProtocolVersion uint32
}

func (d SessionDelegation) Validate() error {
	if d.WQPUChainID == "" || d.Wallet == (common.Address{}) || d.Session == (common.Address{}) {
		return errors.New("WQPU chain, wallet and session are required")
	}
	if d.ExpiresHeight <= d.IssuedHeight {
		return errors.New("session expiry must follow issue height")
	}
	if d.Permissions&^SessionAllPermissions != 0 {
		return errors.New("unknown WQPU session permission bit")
	}
	if d.ProtocolVersion != uint32(ProtocolVersion) {
		return errors.New("unsupported WQPU session protocol version")
	}
	return nil
}

type SessionState struct {
	Delegation    SessionDelegation
	SpentUnits    uint64
	ReservedUnits uint64
	ActionNonce   uint64
	Revoked       bool
}

func decimal256(value uint64) *ethmath.HexOrDecimal256 {
	var out ethmath.HexOrDecimal256
	if err := out.UnmarshalText([]byte(strconv.FormatUint(value, 10))); err != nil {
		panic(err)
	}
	return &out
}

func SessionTypedData(d SessionDelegation, evmChainID uint64) (apitypes.TypedData, error) {
	if err := d.Validate(); err != nil {
		return apitypes.TypedData{}, err
	}
	if evmChainID == 0 {
		return apitypes.TypedData{}, errors.New("EVM chain id must be positive")
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
				{Name: "sessionAddress", Type: "address"},
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
		Domain: apitypes.TypedDataDomain{Name: "WQPU Session", Version: "1", ChainId: decimal256(evmChainID)},
		Message: apitypes.TypedDataMessage{
			"wallet":          d.Wallet.Hex(),
			"sessionAddress":  d.Session.Hex(),
			"wqpuChainId":     d.WQPUChainID,
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

func SessionDigest(d SessionDelegation, evmChainID uint64) ([]byte, error) {
	typed, err := SessionTypedData(d, evmChainID)
	if err != nil {
		return nil, err
	}
	digest, _, err := apitypes.TypedDataAndHash(typed)
	if err != nil {
		return nil, err
	}
	if len(digest) != 32 {
		return nil, errors.New("unexpected WQPU session digest length")
	}
	return digest, nil
}

func decodeSignature(signatureHex string) ([]byte, error) {
	if !strings.HasPrefix(signatureHex, "0x") {
		return nil, errors.New("signature must be 0x-prefixed")
	}
	sig, err := hex.DecodeString(signatureHex[2:])
	if err != nil || len(sig) != crypto.SignatureLength {
		return nil, errors.New("signature must contain exactly 65 bytes")
	}
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

func VerifySessionWalletSignature(d SessionDelegation, evmChainID uint64, signatureHex string) error {
	digest, err := SessionDigest(d, evmChainID)
	if err != nil {
		return err
	}
	sig, err := decodeSignature(signatureHex)
	if err != nil {
		return err
	}
	pub, err := crypto.SigToPub(digest, sig)
	if err != nil {
		return errors.New("cannot recover WQPU wallet signature")
	}
	if crypto.PubkeyToAddress(*pub) != d.Wallet {
		return errors.New("WQPU session was signed by another wallet")
	}
	return nil
}

func sessionStorageKey(wallet, session common.Address) []byte {
	out := make([]byte, 0, common.AddressLength*2)
	out = append(out, wallet.Bytes()...)
	out = append(out, session.Bytes()...)
	return out
}

func walletNonceKey(wallet common.Address) []byte { return wallet.Bytes() }

func EncodeSession(state SessionState) ([]byte, error) {
	if err := state.Delegation.Validate(); err != nil {
		return nil, err
	}
	out := make([]byte, 0, 160)
	out = append(out, sessionCodecVersion)
	chainRaw := []byte(state.Delegation.WQPUChainID)
	if len(chainRaw) == 0 || len(chainRaw) > 255 {
		return nil, errors.New("WQPU chain id length is outside session codec bounds")
	}
	out = append(out, byte(len(chainRaw)))
	out = append(out, chainRaw...)
	out = append(out, state.Delegation.Wallet.Bytes()...)
	out = append(out, state.Delegation.Session.Bytes()...)
	out = appendUint64(out, state.Delegation.IssuedHeight)
	out = appendUint64(out, state.Delegation.ExpiresHeight)
	out = appendUint64(out, state.Delegation.MaxSpendUnits)
	out = appendUint64(out, state.Delegation.MaxJobUnits)
	out = appendUint64(out, state.Delegation.RevocationNonce)
	out = appendUint64(out, state.Delegation.Permissions)
	out = appendUint32(out, state.Delegation.ProtocolVersion)
	out = appendUint64(out, state.SpentUnits)
	out = appendUint64(out, state.ReservedUnits)
	out = appendUint64(out, state.ActionNonce)
	if state.Revoked {
		out = append(out, 1)
	} else {
		out = append(out, 0)
	}
	return out, nil
}

func DecodeSession(data []byte) (SessionState, error) {
	if len(data) < 2 || data[0] != sessionCodecVersion {
		return SessionState{}, errors.New("unsupported WQPU session codec")
	}
	pos := 1
	take := func(n int) ([]byte, error) {
		if n < 0 || pos > len(data)-n {
			return nil, errors.New("truncated WQPU session state")
		}
		out := data[pos : pos+n]
		pos += n
		return out, nil
	}
	chainLenRaw, err := take(1)
	if err != nil {
		return SessionState{}, err
	}
	chainRaw, err := take(int(chainLenRaw[0]))
	if err != nil || len(chainRaw) == 0 {
		return SessionState{}, errors.New("invalid WQPU session chain id")
	}
	walletRaw, err := take(common.AddressLength)
	if err != nil { return SessionState{}, err }
	sessionRaw, err := take(common.AddressLength)
	if err != nil { return SessionState{}, err }
	read64 := func() (uint64, error) {
		raw, err := take(8)
		if err != nil { return 0, err }
		return binary.BigEndian.Uint64(raw), nil
	}
	issued, err := read64(); if err != nil { return SessionState{}, err }
	expires, err := read64(); if err != nil { return SessionState{}, err }
	maxSpend, err := read64(); if err != nil { return SessionState{}, err }
	maxJob, err := read64(); if err != nil { return SessionState{}, err }
	revocation, err := read64(); if err != nil { return SessionState{}, err }
	permissions, err := read64(); if err != nil { return SessionState{}, err }
	protocolRaw, err := take(4); if err != nil { return SessionState{}, err }
	spent, err := read64(); if err != nil { return SessionState{}, err }
	reserved, err := read64(); if err != nil { return SessionState{}, err }
	actionNonce, err := read64(); if err != nil { return SessionState{}, err }
	revokedRaw, err := take(1); if err != nil { return SessionState{}, err }
	if revokedRaw[0] > 1 || pos != len(data) {
		return SessionState{}, errors.New("invalid trailing WQPU session state")
	}
	out := SessionState{
		Delegation: SessionDelegation{
			WQPUChainID: string(chainRaw), Wallet: common.BytesToAddress(walletRaw), Session: common.BytesToAddress(sessionRaw),
			IssuedHeight: issued, ExpiresHeight: expires, MaxSpendUnits: maxSpend, MaxJobUnits: maxJob,
			RevocationNonce: revocation, Permissions: permissions, ProtocolVersion: binary.BigEndian.Uint32(protocolRaw),
		},
		SpentUnits: spent, ReservedUnits: reserved, ActionNonce: actionNonce, Revoked: revokedRaw[0] == 1,
	}
	if err := out.Delegation.Validate(); err != nil {
		return SessionState{}, err
	}
	if out.ReservedUnits > ^uint64(0)-out.SpentUnits || out.SpentUnits+out.ReservedUnits > out.Delegation.MaxSpendUnits {
		return SessionState{}, errors.New("stored WQPU session spend exceeds delegation")
	}
	return out, nil
}

func StoreSession(state WordState, session SessionState) error {
	encoded, err := EncodeSession(session)
	if err != nil {
		return err
	}
	return WriteBlob(state, "session", sessionStorageKey(session.Delegation.Wallet, session.Delegation.Session), encoded)
}

func LoadSession(state WordState, wallet, session common.Address) (SessionState, bool, error) {
	if state == nil || wallet == (common.Address{}) || session == (common.Address{}) {
		return SessionState{}, false, errors.New("valid state, wallet and session required")
	}
	encoded, err := ReadBlob(state, "session", sessionStorageKey(wallet, session))
	if err != nil {
		return SessionState{}, false, err
	}
	if len(encoded) == 0 {
		return SessionState{}, false, nil
	}
	out, err := DecodeSession(encoded)
	if err != nil {
		return SessionState{}, false, err
	}
	if out.Delegation.Wallet != wallet || out.Delegation.Session != session {
		return SessionState{}, false, errors.New("WQPU session stored under wrong identity")
	}
	return out, true, nil
}

func WalletRevocationNonce(state WordState, wallet common.Address) (uint64, error) {
	return GetUint64(state, "wallet-revocation", walletNonceKey(wallet))
}

func SetWalletRevocationNonce(state WordState, wallet common.Address, nonce uint64) error {
	if wallet == (common.Address{}) {
		return errors.New("wallet is required")
	}
	current, err := WalletRevocationNonce(state, wallet)
	if err != nil {
		return err
	}
	if nonce <= current {
		return errors.New("wallet revocation nonce must increase")
	}
	return SetUint64(state, "wallet-revocation", walletNonceKey(wallet), nonce)
}

// AuthorizeSession verifies the external wallet signature before any session
// state is created. Any relayer may submit the proof; it gets no session rights.
func AuthorizeSession(state WordState, delegation SessionDelegation, evmChainID, height uint64, signatureHex string) error {
	if err := delegation.Validate(); err != nil {
		return err
	}
	if height < delegation.IssuedHeight || height >= delegation.ExpiresHeight {
		return errors.New("WQPU session is not active at current height")
	}
	if err := VerifySessionWalletSignature(delegation, evmChainID, signatureHex); err != nil {
		return err
	}
	currentNonce, err := WalletRevocationNonce(state, delegation.Wallet)
	if err != nil {
		return err
	}
	if delegation.RevocationNonce != currentNonce {
		return errors.New("stale or future WQPU wallet revocation nonce")
	}
	_, exists, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil {
		return err
	}
	if exists {
		return errors.New("WQPU session already authorized")
	}
	return StoreSession(state, SessionState{Delegation: delegation})
}
