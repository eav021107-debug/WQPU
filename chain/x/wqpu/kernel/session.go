package kernel

import (
	"errors"
	"math"
)

const (
	SessionPermProvider uint64 = 1 << iota
	SessionPermJob
	SessionPermSettle
	SessionAllPermissions = SessionPermProvider | SessionPermJob | SessionPermSettle
)

type SessionDelegation struct {
	ChainID           string
	Wallet            string
	SessionPubkey     [32]byte
	IssuedHeight      uint64
	ExpiresHeight     uint64
	MaxSpendUnits     uint64
	MaxJobUnits       uint64
	RevocationNonce   uint64
	Permissions       uint64
	ProtocolVersion   uint32
}

func (d SessionDelegation) Validate() error {
	if d.ChainID == "" || d.Wallet == "" {
		return errors.New("session chain and wallet must be non-empty")
	}
	var zero [32]byte
	if d.SessionPubkey == zero {
		return errors.New("session public key must be non-zero")
	}
	if d.ExpiresHeight <= d.IssuedHeight {
		return errors.New("session expiry must follow issue height")
	}
	if d.Permissions&^SessionAllPermissions != 0 {
		return errors.New("unknown session permission bit")
	}
	if d.ProtocolVersion != ProtocolVersion {
		return errors.New("unsupported protocol version")
	}
	return nil
}

type SessionState struct {
	Delegation SessionDelegation
	SpentUnits uint64
	Revoked    bool
}

func singlePermission(permission uint64) bool {
	return permission != 0 && permission&(permission-1) == 0 && permission&SessionAllPermissions != 0
}

func (s SessionState) CanAuthorize(height, permission, jobCharge uint64) error {
	if err := s.Delegation.Validate(); err != nil {
		return err
	}
	if s.Revoked {
		return errors.New("session revoked")
	}
	if height < s.Delegation.IssuedHeight || height >= s.Delegation.ExpiresHeight {
		return errors.New("session is not active at this height")
	}
	if !singlePermission(permission) || s.Delegation.Permissions&permission == 0 {
		return errors.New("session permission denied")
	}
	if jobCharge > s.Delegation.MaxJobUnits {
		return errors.New("job exceeds session per-job limit")
	}
	if jobCharge > math.MaxUint64-s.SpentUnits {
		return errors.New("session spend overflow")
	}
	if s.SpentUnits+jobCharge > s.Delegation.MaxSpendUnits {
		return errors.New("session total spend limit exceeded")
	}
	return nil
}

func (s *SessionState) Consume(height, permission, amount uint64) error {
	if s == nil {
		return errors.New("nil session")
	}
	if err := s.CanAuthorize(height, permission, amount); err != nil {
		return err
	}
	s.SpentUnits += amount
	return nil
}

func (s *SessionState) Revoke() {
	if s != nil {
		s.Revoked = true
	}
}
