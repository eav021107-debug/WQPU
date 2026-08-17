package precompile

import "testing"

func TestSessionSpendMustBeReservedBeforeWork(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	if err := ReserveSessionSpend(state, delegation.Wallet, delegation.Session, 121, 100_000); err != nil {
		t.Fatal(err)
	}
	session, exists, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil || !exists {
		t.Fatalf("session exists=%v err=%v", exists, err)
	}
	if session.ReservedUnits != 100_000 || session.SpentUnits != 0 {
		t.Fatalf("reserved=%d spent=%d", session.ReservedUnits, session.SpentUnits)
	}
}

func TestSessionCannotOverbookConcurrentJobs(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	for i := 0; i < 10; i++ {
		if err := ReserveSessionSpend(state, delegation.Wallet, delegation.Session, 121, 100_000); err != nil {
			t.Fatalf("reservation %d failed: %v", i, err)
		}
	}
	if err := ReserveSessionSpend(state, delegation.Wallet, delegation.Session, 121, 1); err == nil {
		t.Fatal("reservation above total session spend limit should fail")
	}
}

func TestSettlementConsumesActualAndReleasesUnused(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	if err := ReserveSessionSpend(state, delegation.Wallet, delegation.Session, 121, 100_000); err != nil {
		t.Fatal(err)
	}
	if err := SettleSessionSpend(state, delegation.Wallet, delegation.Session, 100_000, 60_000); err != nil {
		t.Fatal(err)
	}
	session, _, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil {
		t.Fatal(err)
	}
	if session.ReservedUnits != 0 || session.SpentUnits != 60_000 {
		t.Fatalf("reserved=%d spent=%d", session.ReservedUnits, session.SpentUnits)
	}
}

func TestWalletRevocationStopsNewSpendReservations(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	if err := SetWalletRevocationNonce(state, delegation.Wallet, 1); err != nil {
		t.Fatal(err)
	}
	if err := ReserveSessionSpend(state, delegation.Wallet, delegation.Session, 121, 1); err == nil {
		t.Fatal("revoked session should not reserve new spend")
	}
}
