package kernel

import "testing"

func stateSession(wallet string, permissions uint64) SessionDelegation {
	var key [32]byte
	key[0] = byte(len(wallet) + 1)
	return SessionDelegation{
		ChainID:         "wqpu-dev-1",
		Wallet:          wallet,
		SessionPubkey:   key,
		IssuedHeight:    0,
		ExpiresHeight:   100,
		MaxSpendUnits:   10_000,
		MaxJobUnits:     2_000,
		RevocationNonce: 0,
		Permissions:     permissions,
		ProtocolVersion: ProtocolVersion,
	}
}

func stateProvider(wallet, peer string, height, expires uint64) Provider {
	return Provider{
		Wallet:          wallet,
		PeerID:          peer,
		Endpoints:       []string{"wqpu://" + peer + ":7443"},
		ModelHashes:     []string{testModel},
		CapacityUnits:   100,
		BusyUnits:       0,
		FreeMemoryBytes: 10_000,
		HeartbeatHeight: height,
		ExpiresHeight:   expires,
		CapabilityHash:  "cap-" + peer,
		ProtocolVersion: ProtocolVersion,
	}
}

func preparedState(t *testing.T) (*State, SessionDelegation, SessionDelegation) {
	t.Helper()
	s, err := NewState("wqpu-dev-1", 1000)
	if err != nil {
		t.Fatal(err)
	}
	requester := stateSession("requester", SessionPermJob|SessionPermSettle)
	providerSession := stateSession("provider", SessionPermProvider)
	if err := s.AuthorizeSession(requester); err != nil {
		t.Fatal(err)
	}
	if err := s.AuthorizeSession(providerSession); err != nil {
		t.Fatal(err)
	}
	if err := s.PublishProvider("provider", providerSession.SessionPubkey, stateProvider("provider", "peer-1", 0, 20)); err != nil {
		t.Fatal(err)
	}
	return s, requester, providerSession
}

func jobFor(s *State, requester SessionDelegation, id string, reserve, charge, expires uint64) JobReservation {
	return JobReservation{
		JobID:                  id,
		RequesterWallet:        requester.Wallet,
		RequesterSessionPubkey: requester.SessionPubkey,
		ModelHash:               testModel,
		PriceEpoch:              s.Epoch,
		PricePerMillionUnits:    s.PricePerMillionUnits,
		MaxComputeUnits:         reserve,
		MaxChargeUnits:          charge,
		CreatedHeight:           s.Height,
		ExpiresHeight:           expires,
		Providers: []ProviderReservation{{
			ProviderWallet:       "provider",
			ProviderPeerID:       "peer-1",
			ReservedComputeUnits: reserve,
			AssignedModelBytes:   1000,
		}},
	}
}

func TestStateReservesComputeAndSpendBeforeWork(t *testing.T) {
	s, requester, _ := preparedState(t)
	j := jobFor(s, requester, "job-1", 70, 500, 10)
	if err := s.ReserveJob(j); err != nil {
		t.Fatal(err)
	}
	if s.ReservedByPeer["peer-1"] != 70 {
		t.Fatalf("reserved compute=%d", s.ReservedByPeer["peer-1"])
	}
	session, _ := s.session(requester.Wallet, requester.SessionPubkey)
	if session.ReservedUnits != 500 || session.SpentUnits != 0 {
		t.Fatalf("session reserved=%d spent=%d", session.ReservedUnits, session.SpentUnits)
	}
}

func TestFailedSecondJobDoesNotPartiallyReserveAnything(t *testing.T) {
	s, requester, _ := preparedState(t)
	if err := s.ReserveJob(jobFor(s, requester, "job-1", 80, 500, 10)); err != nil {
		t.Fatal(err)
	}
	session, _ := s.session(requester.Wallet, requester.SessionPubkey)
	beforeSpend := session.ReservedUnits
	beforeCompute := s.ReservedByPeer["peer-1"]
	if err := s.ReserveJob(jobFor(s, requester, "job-2", 30, 600, 10)); err == nil {
		t.Fatal("second job should exceed provider capacity")
	}
	if session.ReservedUnits != beforeSpend || s.ReservedByPeer["peer-1"] != beforeCompute {
		t.Fatal("failed job changed accounting")
	}
	if _, exists := s.Jobs["job-2"]; exists {
		t.Fatal("failed job was persisted")
	}
}

func TestCloseJobPaysActualAndReturnsUnusedReservation(t *testing.T) {
	s, requester, _ := preparedState(t)
	if err := s.ReserveJob(jobFor(s, requester, "job-1", 70, 500, 10)); err != nil {
		t.Fatal(err)
	}
	if err := s.CloseJob("job-1", 320); err != nil {
		t.Fatal(err)
	}
	session, _ := s.session(requester.Wallet, requester.SessionPubkey)
	if session.ReservedUnits != 0 || session.SpentUnits != 320 {
		t.Fatalf("session reserved=%d spent=%d", session.ReservedUnits, session.SpentUnits)
	}
	if s.ReservedByPeer["peer-1"] != 0 {
		t.Fatal("compute reservation was not released")
	}
}

func TestExpiredJobReleasesSpendAndComputeWithoutCharging(t *testing.T) {
	s, requester, _ := preparedState(t)
	if err := s.ReserveJob(jobFor(s, requester, "job-1", 70, 500, 2)); err != nil {
		t.Fatal(err)
	}
	if err := s.AdvanceHeight(2); err != nil {
		t.Fatal(err)
	}
	session, _ := s.session(requester.Wallet, requester.SessionPubkey)
	if session.ReservedUnits != 0 || session.SpentUnits != 0 {
		t.Fatal("expired unused job should not charge")
	}
	if len(s.Jobs) != 0 || len(s.ReservedByPeer) != 0 {
		t.Fatal("expired job resources were not cleared")
	}
}

func TestGlobalPriceUsesConfirmedReservationNotReportedBusy(t *testing.T) {
	s, requester, _ := preparedState(t)
	if err := s.ReserveJob(jobFor(s, requester, "job-1", 95, 500, 10)); err != nil {
		t.Fatal(err)
	}
	price, err := s.ClosePriceEpoch()
	if err != nil {
		t.Fatal(err)
	}
	if price.AggregateBusyUnits != 95 || price.PricePerMillionUnits != 1050 {
		t.Fatalf("price state=%+v", price)
	}
}

func TestStaleGlobalPriceIsRejected(t *testing.T) {
	s, requester, _ := preparedState(t)
	j := jobFor(s, requester, "job-1", 10, 100, 10)
	j.PricePerMillionUnits--
	if err := s.ReserveJob(j); err == nil {
		t.Fatal("stale price should be rejected")
	}
}

func TestWalletNonceRevokesOldSessions(t *testing.T) {
	s, requester, _ := preparedState(t)
	if err := s.RevokeWalletSessions(requester.Wallet, 1); err != nil {
		t.Fatal(err)
	}
	if err := s.ReserveJob(jobFor(s, requester, "job-1", 10, 100, 10)); err == nil {
		t.Fatal("revoked session should not authorize a new job")
	}
	newSession := stateSession(requester.Wallet, SessionPermJob)
	newSession.SessionPubkey[1] = 9
	newSession.RevocationNonce = 1
	if err := s.AuthorizeSession(newSession); err != nil {
		t.Fatal(err)
	}
}

func TestDuplicatePeerIdentityCannotBeClaimedByAnotherWallet(t *testing.T) {
	s, _, _ := preparedState(t)
	other := stateSession("other-provider", SessionPermProvider)
	if err := s.AuthorizeSession(other); err != nil {
		t.Fatal(err)
	}
	if err := s.PublishProvider("other-provider", other.SessionPubkey, stateProvider("other-provider", "peer-1", 0, 20)); err == nil {
		t.Fatal("duplicate peer id should be rejected")
	}
}
