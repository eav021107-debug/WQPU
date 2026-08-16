package kernel

import (
	"encoding/hex"
	"errors"
	"strings"
)

const (
	SessionPermProvider uint64 = 1 << iota
	SessionPermJob
	SessionPermSettle
	SessionAllPermissions = SessionPermProvider | SessionPermJob | SessionPermSettle
)

func validSessionAddress(value string) bool {
	if len(value) != 42 || !strings.HasPrefix(value, "0x") {
		return false
	}
	decoded, err := hex.DecodeString(value[2:])
	return err == nil && len(decoded) == 20
}

func CanonicalSessionAddress(value string) (string, error) {
	if !validSessionAddress(value) {
		return "", errors.New("session address must be a 20-byte 0x-prefixed address")
	}
	return strings.ToLower(value), nil
}

type SessionDelegation struct {
	ChainID           string
	Wallet            string
	SessionAddress    string
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
	canonical, err := CanonicalSessionAddress(d.SessionAddress)
	if err != nil {
		return err
	}
	if canonical != d.SessionAddress {
		return errors.New("session address must use canonical lowercase hex")
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
	Delegation    SessionDelegation
	SpentUnits    uint64
	ReservedUnits uint64
	Revoked       bool
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
	max := ^uint64(0)
	if s.ReservedUnits > max-s.SpentUnits {
		return errors.New("session accounting overflow")
	}
	committed := s.SpentUnits + s.ReservedUnits
	if jobCharge > max-committed {
		return errors.New("session spend overflow")
	}
	if committed+jobCharge > s.Delegation.MaxSpendUnits {
		return errors.New("session total spend limit exceeded")
	}
	return nil
}

func (s *SessionState) Reserve(height, permission, amount uint64) error {
	if s == nil {
		return errors.New("nil session")
	}
	if err := s.CanAuthorize(height, permission, amount); err != nil {
		return err
	}
	s.ReservedUnits += amount
	return nil
}

func (s SessionState) CanRelease(amount uint64) error {
	if amount > s.ReservedUnits {
		return errors.New("cannot release more session spend than reserved")
	}
	return nil
}

func (s *SessionState) Release(amount uint64) error {
	if s == nil {
		return errors.New("nil session")
	}
	if err := s.CanRelease(amount); err != nil {
		return err
	}
	s.ReservedUnits -= amount
	return nil
}

func (s SessionState) CanSettle(reservedAmount, actualAmount uint64) error {
	if actualAmount > reservedAmount {
		return errors.New("settlement exceeds reserved job amount")
	}
	if reservedAmount > s.ReservedUnits {
		return errors.New("settlement exceeds session reservation")
	}
	if actualAmount > ^uint64(0)-s.SpentUnits {
		return errors.New("session spend overflow")
	}
	newReserved := s.ReservedUnits - reservedAmount
	newSpent := s.SpentUnits + actualAmount
	if newReserved > ^uint64(0)-newSpent || newSpent+newReserved > s.Delegation.MaxSpendUnits {
		return errors.New("session total spend limit exceeded")
	}
	return nil
}

func (s *SessionState) Settle(reservedAmount, actualAmount uint64) error {
	if s == nil {
		return errors.New("nil session")
	}
	if err := s.CanSettle(reservedAmount, actualAmount); err != nil {
		return err
	}
	s.ReservedUnits -= reservedAmount
	s.SpentUnits += actualAmount
	return nil
}

func (s *SessionState) Revoke() {
	if s != nil {
		s.Revoked = true
	}
}
