package precompile

import "testing"

func TestNativePaymentUnitConversion(t *testing.T) {
	value, err := PaymentUnitsToNative(7)
	if err != nil { t.Fatal(err) }
	if value.Uint64() != 7*NativeUnitsPerPaymentUnit { t.Fatalf("native=%s", value.String()) }
}

func TestFundedEscrowBacksReservations(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	if err := CreditSessionEscrow(state, delegation.Wallet, delegation.Session, 100_000); err != nil { t.Fatal(err) }
	if err := ReserveSessionSpendV2(state, delegation.Wallet, delegation.Session, 121, 100_000); err != nil { t.Fatal(err) }
	if err := ReserveSessionSpendV2(state, delegation.Wallet, delegation.Session, 121, 1); err == nil { t.Fatal("unfunded extra reservation should fail") }
}

func TestWithdrawableEscrowExcludesReservedFunds(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	if err := CreditSessionEscrow(state, delegation.Wallet, delegation.Session, 100_000); err != nil { t.Fatal(err) }
	if err := ReserveSessionSpendV2(state, delegation.Wallet, delegation.Session, 121, 25_000); err != nil { t.Fatal(err) }
	available, err := WithdrawableSessionEscrow(state, delegation.Wallet, delegation.Session)
	if err != nil { t.Fatal(err) }
	if available != 75_000 { t.Fatalf("withdrawable=%d", available) }
}

func TestSettlementDebitsOnlyActualAcceptedWork(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	if err := CreditSessionEscrow(state, delegation.Wallet, delegation.Session, 100_000); err != nil { t.Fatal(err) }
	if err := ReserveSessionSpendV2(state, delegation.Wallet, delegation.Session, 121, 100_000); err != nil { t.Fatal(err) }
	if err := SettleSessionEscrow(state, delegation.Wallet, delegation.Session, 100_000, 40_000); err != nil { t.Fatal(err) }
	escrow, err := SessionEscrowUnits(state, delegation.Wallet, delegation.Session)
	if err != nil { t.Fatal(err) }
	if escrow != 60_000 { t.Fatalf("escrow=%d", escrow) }
	session, _, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil { t.Fatal(err) }
	if session.ReservedUnits != 0 || session.SpentUnits != 40_000 { t.Fatalf("session=%+v", session) }
}

func TestEscrowCannotExceedDelegatedLifetimeSpend(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	if err := CreditSessionEscrow(state, delegation.Wallet, delegation.Session, delegation.MaxSpendUnits); err != nil { t.Fatal(err) }
	if err := CreditSessionEscrow(state, delegation.Wallet, delegation.Session, 1); err == nil { t.Fatal("escrow above lifetime delegation should fail") }
}
