package kernel

import "testing"

func validSession() SessionState {
	var key [32]byte
	key[0] = 1
	return SessionState{Delegation: SessionDelegation{
		ChainID:         "wqpu-dev-1",
		Wallet:          "0x1111111111111111111111111111111111111111",
		SessionPubkey:   key,
		IssuedHeight:    100,
		ExpiresHeight:   200,
		MaxSpendUnits:   1000,
		MaxJobUnits:     400,
		RevocationNonce: 7,
		Permissions:     SessionPermJob | SessionPermSettle,
		ProtocolVersion: ProtocolVersion,
	}}
}

func TestSessionEnforcesPermissionAndLimits(t *testing.T) {
	s := validSession()
	if err := s.CanAuthorize(120, SessionPermProvider, 0); err == nil {
		t.Fatal("provider permission should be denied")
	}
	if err := s.CanAuthorize(120, SessionPermJob, 401); err == nil {
		t.Fatal("per-job limit should be enforced")
	}
	if err := s.Reserve(120, SessionPermJob, 400); err != nil {
		t.Fatal(err)
	}
	if err := s.Reserve(121, SessionPermJob, 400); err != nil {
		t.Fatal(err)
	}
	if err := s.Reserve(122, SessionPermJob, 201); err == nil {
		t.Fatal("total spend limit should include reserved jobs")
	}
}

func TestSettlementConsumesOnlyActualSpend(t *testing.T) {
	s := validSession()
	if err := s.Reserve(120, SessionPermJob, 400); err != nil {
		t.Fatal(err)
	}
	if err := s.Settle(400, 250); err != nil {
		t.Fatal(err)
	}
	if s.SpentUnits != 250 || s.ReservedUnits != 0 {
		t.Fatalf("spent=%d reserved=%d", s.SpentUnits, s.ReservedUnits)
	}
}

func TestReleaseReturnsUnusedReservation(t *testing.T) {
	s := validSession()
	if err := s.Reserve(120, SessionPermJob, 300); err != nil {
		t.Fatal(err)
	}
	if err := s.Release(300); err != nil {
		t.Fatal(err)
	}
	if s.SpentUnits != 0 || s.ReservedUnits != 0 {
		t.Fatal("release must not turn unused reservation into spend")
	}
}

func TestSessionHeightAndRevocation(t *testing.T) {
	s := validSession()
	if err := s.CanAuthorize(99, SessionPermJob, 1); err == nil {
		t.Fatal("session should not work before issue height")
	}
	if err := s.CanAuthorize(200, SessionPermJob, 1); err == nil {
		t.Fatal("session should not work at expiry height")
	}
	s.Revoke()
	if err := s.CanAuthorize(120, SessionPermJob, 1); err == nil {
		t.Fatal("revoked session should be denied")
	}
}

func TestUnknownPermissionBitIsRejected(t *testing.T) {
	s := validSession()
	s.Delegation.Permissions |= 1 << 20
	if err := s.Delegation.Validate(); err == nil {
		t.Fatal("unknown permission bit should fail validation")
	}
}

func TestZeroSessionKeyRejected(t *testing.T) {
	s := validSession()
	s.Delegation.SessionPubkey = [32]byte{}
	if err := s.Delegation.Validate(); err == nil {
		t.Fatal("zero session key should be rejected")
	}
}
