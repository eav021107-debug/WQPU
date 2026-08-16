package wallet

import (
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
)

const ProtocolVersion uint32 = 1

const (
	SessionPermProvider uint64 = 1 << iota
	SessionPermJob
	SessionPermSettle
	SessionAllPermissions = SessionPermProvider | SessionPermJob | SessionPermSettle
)

type SessionRequest struct {
	WQPUChainID     string
	EVMChainID      uint64
	Wallet          string
	SessionPubkey   string
	IssuedHeight    uint64
	ExpiresHeight   uint64
	MaxSpendUnits   uint64
	MaxJobUnits     uint64
	RevocationNonce uint64
	Permissions     uint64
}

type TypeField struct {
	Name string `json:"name"`
	Type string `json:"type"`
}

type TypedData struct {
	Types       map[string][]TypeField `json:"types"`
	PrimaryType string                 `json:"primaryType"`
	Domain      map[string]any         `json:"domain"`
	Message     map[string]any         `json:"message"`
}

func validHex(value string, bytes int) bool {
	if !strings.HasPrefix(value, "0x") || len(value) != 2+bytes*2 {
		return false
	}
	decoded, err := hex.DecodeString(value[2:])
	return err == nil && len(decoded) == bytes
}

func (r SessionRequest) Validate() error {
	if r.WQPUChainID == "" || r.EVMChainID == 0 {
		return errors.New("WQPU and EVM chain ids are required")
	}
	if !validHex(r.Wallet, 20) {
		return errors.New("wallet must be a 20-byte 0x-prefixed address")
	}
	if !validHex(r.SessionPubkey, 32) {
		return errors.New("session public key must be 32-byte 0x-prefixed hex")
	}
	if r.ExpiresHeight <= r.IssuedHeight {
		return errors.New("session expiry must follow issue height")
	}
	if r.Permissions&^SessionAllPermissions != 0 {
		return errors.New("unknown WQPU session permission bit")
	}
	return nil
}

func BuildSessionTypedData(r SessionRequest) (TypedData, error) {
	if err := r.Validate(); err != nil {
		return TypedData{}, err
	}
	return TypedData{
		Types: map[string][]TypeField{
			"EIP712Domain": []TypeField{
				{Name: "name", Type: "string"},
				{Name: "version", Type: "string"},
				{Name: "chainId", Type: "uint256"},
			},
			"WQPUSession": []TypeField{
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
		Domain: map[string]any{
			"name":    "WQPU Session",
			"version": "1",
			"chainId": r.EVMChainID,
		},
		Message: map[string]any{
			"wallet":          r.Wallet,
			"sessionPubkey":   r.SessionPubkey,
			"wqpuChainId":     r.WQPUChainID,
			"issuedHeight":    r.IssuedHeight,
			"expiresHeight":   r.ExpiresHeight,
			"maxSpendUnits":   r.MaxSpendUnits,
			"maxJobUnits":     r.MaxJobUnits,
			"revocationNonce": r.RevocationNonce,
			"permissions":     r.Permissions,
			"protocolVersion": ProtocolVersion,
		},
	}, nil
}

func BuildSignRequest(r SessionRequest) (map[string]any, error) {
	typed, err := BuildSessionTypedData(r)
	if err != nil {
		return nil, err
	}
	encoded, err := json.Marshal(typed)
	if err != nil {
		return nil, err
	}
	return map[string]any{
		"method": "eth_signTypedData_v4",
		"params": []any{r.Wallet, string(encoded)},
	}, nil
}
