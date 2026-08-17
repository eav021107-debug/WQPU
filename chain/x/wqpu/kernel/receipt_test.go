package kernel

import "testing"

func receiptJob() JobReservation {
	return JobReservation{
		JobID:                   "job-receipts",
		RequesterWallet:         "requester",
		RequesterSessionAddress: "0x1111111111111111111111111111111111111111",
		ModelHash:                testModel,
		PriceEpoch:               3,
		PricePerMillionUnits:     1_000_000,
		MaxComputeUnits:          500,
		MaxChargeUnits:           500,
		CreatedHeight:            10,
		ExpiresHeight:            20,
		Providers: []ProviderReservation{
			{ProviderWallet: "wallet-a", ProviderPeerID: "peer-a", ReservedComputeUnits: 50, AssignedModelBytes: 1000},
			{ProviderWallet: "wallet-b", ProviderPeerID: "peer-b", ReservedComputeUnits: 50, AssignedModelBytes: 1000},
		},
	}
}

func receipt(peer, wallet string, sequence, delta, cumulative uint64) WorkReceipt {
	return WorkReceipt{
		JobID:                 "job-receipts",
		ProviderWallet:         wallet,
		ProviderPeerID:         peer,
		Sequence:               sequence,
		ComputeUnits:           delta,
		CumulativeComputeUnits: cumulative,
		CumulativePaymentUnits: cumulative,
		ResultCommitment:       "sha256:result",
	}
}

func TestReceiptMustAdvanceMonotonically(t *testing.T) {
	job := receiptJob()
	first := receipt("peer-a", "wallet-a", 1, 100, 100)
	if err := ValidateReceipt(job, nil, first); err != nil { t.Fatal(err) }
	second := receipt("peer-a", "wallet-a", 2, 50, 150)
	if err := ValidateReceipt(job, &first, second); err != nil { t.Fatal(err) }
	if err := ValidateReceipt(job, &second, second); err == nil { t.Fatal("replayed receipt should be rejected") }
}

func TestReceiptCannotChooseItsOwnPrice(t *testing.T) {
	job := receiptJob()
	r := receipt("peer-a", "wallet-a", 1, 100, 100)
	r.CumulativePaymentUnits = 101
	if err := ValidateReceipt(job, nil, r); err == nil { t.Fatal("receipt with provider-chosen price should be rejected") }
}

func TestReceiptCannotClaimUnreservedProvider(t *testing.T) {
	job := receiptJob()
	r := receipt("peer-x", "wallet-x", 1, 100, 100)
	if err := ValidateReceipt(job, nil, r); err == nil { t.Fatal("unreserved provider should be rejected") }
}

func TestSettlementPaysEveryReservedProviderAtGlobalPrice(t *testing.T) {
	job := receiptJob()
	latest := map[string]WorkReceipt{
		"peer-a": receipt("peer-a", "wallet-a", 2, 50, 150),
		"peer-b": receipt("peer-b", "wallet-b", 1, 200, 200),
	}
	settlement, err := BuildSettlement(job, latest)
	if err != nil { t.Fatal(err) }
	if settlement.TotalCharge != 350 { t.Fatalf("total charge=%d", settlement.TotalCharge) }
	if settlement.Payouts["wallet-a"] != 150 || settlement.Payouts["wallet-b"] != 200 { t.Fatalf("payouts=%+v", settlement.Payouts) }
}

func TestSettlementRequiresAllReservedProviders(t *testing.T) {
	job := receiptJob()
	_, err := BuildSettlement(job, map[string]WorkReceipt{"peer-a": receipt("peer-a", "wallet-a", 1, 100, 100)})
	if err == nil { t.Fatal("missing provider receipt should fail final settlement") }
}

func TestTimeoutSettlementPaysOnlyProvidersWithAcceptedReceipts(t *testing.T) {
	job := receiptJob()
	settlement, err := BuildTimeoutSettlement(job, map[string]WorkReceipt{"peer-a": receipt("peer-a", "wallet-a", 1, 125, 125)})
	if err != nil { t.Fatal(err) }
	if settlement.TotalCharge != 125 || settlement.Payouts["wallet-a"] != 125 { t.Fatalf("timeout settlement=%+v", settlement) }
	if _, exists := settlement.Payouts["wallet-b"]; exists { t.Fatal("provider without accepted receipt must not be paid") }
}

func TestTimeoutSettlementRejectsInjectedProvider(t *testing.T) {
	job := receiptJob()
	_, err := BuildTimeoutSettlement(job, map[string]WorkReceipt{"peer-x": receipt("peer-x", "wallet-x", 1, 10, 10)})
	if err == nil { t.Fatal("timeout settlement must reject unreserved provider") }
}

func TestSettlementCannotExceedTotalJobCompute(t *testing.T) {
	job := receiptJob()
	latest := map[string]WorkReceipt{
		"peer-a": receipt("peer-a", "wallet-a", 1, 300, 300),
		"peer-b": receipt("peer-b", "wallet-b", 1, 300, 300),
	}
	if _, err := BuildSettlement(job, latest); err == nil { t.Fatal("aggregate compute above job maximum should fail") }
}
