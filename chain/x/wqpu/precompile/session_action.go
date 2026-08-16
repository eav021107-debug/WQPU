package precompile

import (
	"errors"
	"strconv"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/signer/core/apitypes"
)

const (
	ActionPublishProvider uint32 = 1
	ActionReserveJob      uint32 = 2
	ActionSubmitReceipt   uint32 = 3
	ActionFinalizeJob     uint32 = 4
)

type SessionAction struct {
	WQPUChainID    string
	Wallet         common.Address
	Session        common.Address
	ActionKind     uint32
	ActionNonce    uint64
	Permission     uint64
	PayloadHash    common.Hash
	ProtocolVersion uint32
}

func (a SessionAction) Validate() error {
	if a.WQPUChainID == "" || a.Wallet == (common.Address{}) || a.Session == (common.Address{}) || a.PayloadHash == (common.Hash{}) {
		return errors.New("WQPU action identity and payload hash are required")
	}
	if a.ActionKind < ActionPublishProvider || a.ActionKind > ActionFinalizeJob {
		return errors.New("unknown WQPU action kind")
	}
	if !singlePermission(a.Permission) {
		return errors.New("WQPU action must request one known permission")
	}
	if a.ProtocolVersion != uint32(ProtocolVersion) {
		return errors.New("unsupported WQPU action protocol version")
	}
	return nil
}

func singlePermission(permission uint64) bool {
	return permission != 0 && permission&(permission-1) == 0 && permission&SessionAllPermissions != 0
}

func ActionTypedData(action SessionAction, evmChainID uint64) (apitypes.TypedData, error) {
	if err := action.Validate(); err != nil {
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
			"WQPUAction": {
				{Name: "wallet", Type: "address"},
				{Name: "sessionAddress", Type: "address"},
				{Name: "wqpuChainId", Type: "string"},
				{Name: "actionKind", Type: "uint32"},
				{Name: "actionNonce", Type: "uint64"},
				{Name: "permission", Type: "uint64"},
				{Name: "payloadHash", Type: "bytes32"},
				{Name: "protocolVersion", Type: "uint32"},
			},
		},
		PrimaryType: "WQPUAction",
		Domain: apitypes.TypedDataDomain{Name: "WQPU Action", Version: "1", ChainId: decimal256(evmChainID)},
		Message: apitypes.TypedDataMessage{
			"wallet":          action.Wallet.Hex(),
			"sessionAddress":  action.Session.Hex(),
			"wqpuChainId":     action.WQPUChainID,
			"actionKind":      strconv.FormatUint(uint64(action.ActionKind), 10),
			"actionNonce":     strconv.FormatUint(action.ActionNonce, 10),
			"permission":      strconv.FormatUint(action.Permission, 10),
			"payloadHash":     action.PayloadHash.Hex(),
			"protocolVersion": strconv.FormatUint(uint64(action.ProtocolVersion), 10),
		},
	}, nil
}

func ActionDigest(action SessionAction, evmChainID uint64) ([]byte, error) {
	typed, err := ActionTypedData(action, evmChainID)
	if err != nil {
		return nil, err
	}
	digest, _, err := apitypes.TypedDataAndHash(typed)
	if err != nil {
		return nil, err
	}
	if len(digest) != 32 {
		return nil, errors.New("unexpected WQPU action digest length")
	}
	return digest, nil
}

func VerifySessionAction(
	state WordState,
	action SessionAction,
	requiredPermission uint64,
	evmChainID uint64,
	height uint64,
	signatureHex string,
) (SessionState, error) {
	if err := action.Validate(); err != nil {
		return SessionState{}, err
	}
	if !singlePermission(requiredPermission) || action.Permission != requiredPermission {
		return SessionState{}, errors.New("WQPU action permission does not match operation")
	}
	session, exists, err := LoadSession(state, action.Wallet, action.Session)
	if err != nil {
		return SessionState{}, err
	}
	if !exists {
		return SessionState{}, errors.New("unknown WQPU session")
	}
	if session.Revoked || height < session.Delegation.IssuedHeight || height >= session.Delegation.ExpiresHeight {
		return SessionState{}, errors.New("WQPU session is not active")
	}
	if session.Delegation.WQPUChainID != action.WQPUChainID || session.Delegation.ProtocolVersion != action.ProtocolVersion {
		return SessionState{}, errors.New("WQPU action belongs to another protocol/chain")
	}
	walletNonce, err := WalletRevocationNonce(state, action.Wallet)
	if err != nil {
		return SessionState{}, err
	}
	if walletNonce != session.Delegation.RevocationNonce {
		return SessionState{}, errors.New("WQPU session was revoked by wallet nonce")
	}
	if session.Delegation.Permissions&requiredPermission == 0 {
		return SessionState{}, errors.New("WQPU session permission denied")
	}
	if action.ActionNonce != session.ActionNonce {
		return SessionState{}, errors.New("stale or future WQPU action nonce")
	}

	digest, err := ActionDigest(action, evmChainID)
	if err != nil {
		return SessionState{}, err
	}
	sig, err := decodeSignature(signatureHex)
	if err != nil {
		return SessionState{}, err
	}
	pub, err := crypto.SigToPub(digest, sig)
	if err != nil {
		return SessionState{}, errors.New("cannot recover WQPU session action signature")
	}
	if crypto.PubkeyToAddress(*pub) != action.Session {
		return SessionState{}, errors.New("WQPU action was signed by another session")
	}
	return session, nil
}

// AdvanceSessionActionNonce must be called inside the same EVM snapshot as the
// state mutation authorized by the action. If the mutation fails, the caller
// reverts the snapshot and this nonce advance disappears with it.
func AdvanceSessionActionNonce(state WordState, wallet, sessionAddress common.Address, expected uint64) error {
	session, exists, err := LoadSession(state, wallet, sessionAddress)
	if err != nil {
		return err
	}
	if !exists {
		return errors.New("unknown WQPU session")
	}
	if session.ActionNonce != expected {
		return errors.New("WQPU action nonce changed before commit")
	}
	if session.ActionNonce == ^uint64(0) {
		return errors.New("WQPU action nonce overflow")
	}
	session.ActionNonce++
	return StoreSession(state, session)
}
