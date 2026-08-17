package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func TestFinalizePaysAcceptedWorkAndReleasesUnusedReservations(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	receipt := signedReceiptEnvelope(t, state, delegation, provider, job, 1, 20, 20)
	if err := CommitAcceptedReceipt(state, receipt, DevNetworkConfig, 123); err != nil { t.Fatal(err) }
	settlement, err := FinalizeJobAccounting(state, job.Request.JobID, 124, false)
	if err != nil { t.Fatal(err) }
	if settlement.TotalCharge != receipt.Receipt.CumulativePaymentUnits || len(settlement.Payouts) != 1 {
		t.Fatalf("settlement=%+v", settlement)
	}
	session, exists, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil || !exists { t.Fatalf("session exists=%v err=%v", exists, err) }
	if session.ReservedUnits != 0 || session.SpentUnits != settlement.TotalCharge {
		t.Fatalf("session reserved=%d spent=%d", session.ReservedUnits, session.SpentUnits)
	}
	reserved, err := ReservedPeerUnits(state, provider.PeerID)
	if err != nil || reserved != 0 { t.Fatalf("peer reserved=%d err=%v", reserved, err) }
	if _, exists, err := LoadJob(state, job.Request.JobID); err != nil || exists { t.Fatalf("job exists=%v err=%v", exists, err) }
	completed, err := JobCompleted(state, job.Request.JobID)
	if err != nil || !completed { t.Fatalf("completed=%v err=%v", completed, err) }
	stored, exists, err := LoadJobSettlement(state, job.Request.JobID)
	if err != nil || !exists || stored.TotalCharge != settlement.TotalCharge { t.Fatalf("stored=%+v exists=%v err=%v", stored, exists, err) }
}

func TestTimeoutWithNoAcceptedReceiptsChargesZero(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	settlement, err := FinalizeJobAccounting(state, job.Request.JobID, job.ExpiresHeight, true)
	if err != nil { t.Fatal(err) }
	if settlement.TotalCharge != 0 || len(settlement.Payouts) != 0 { t.Fatalf("settlement=%+v", settlement) }
	session, _, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil { t.Fatal(err) }
	if session.ReservedUnits != 0 || session.SpentUnits != 0 { t.Fatalf("session=%+v", session) }
	reserved, err := ReservedPeerUnits(state, provider.PeerID)
	if err != nil || reserved != 0 { t.Fatalf("peer reserved=%d err=%v", reserved, err) }
}

func TestTimeoutBeforeExpiryFailsWithoutChangingReservations(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	beforeSession, _, _ := LoadSession(state, delegation.Wallet, delegation.Session)
	beforePeer, _ := ReservedPeerUnits(state, provider.PeerID)
	if _, err := FinalizeJobAccounting(state, job.Request.JobID, job.ExpiresHeight-1, true); err == nil { t.Fatal("early timeout should fail") }
	afterSession, _, _ := LoadSession(state, delegation.Wallet, delegation.Session)
	afterPeer, _ := ReservedPeerUnits(state, provider.PeerID)
	if afterSession.ReservedUnits != beforeSession.ReservedUnits || afterPeer != beforePeer { t.Fatal("failed timeout changed reservation state") }
}

func TestCompletedJobIDCannotBeReservedAgainThroughV2(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	if _, err := FinalizeJobAccounting(state, job.Request.JobID, job.ExpiresHeight, true); err != nil { t.Fatal(err) }
	request := jobRequestFor(t, state, delegation.Wallet, provider.ModelHashes[0], []JobProviderReservation{{ProviderWallet: provider.Wallet, ProviderPeerID: provider.PeerID, ReservedComputeUnits: 10, AssignedModelBytes: 1000}}, 1000)
	request.JobID = job.Request.JobID
	if _, err := CommitJobReservationV2(state, request, delegation.Session, job.ExpiresHeight+1); err == nil { t.Fatal("completed job id should never be reusable") }
}

func TestPerProviderRoundedPayoutsNeverExceedReservedMax(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	firstEnvelope := providerEnvelope(t, state, delegation, 0, "round-a")
	if err := CommitProviderPublishV2(state, firstEnvelope, DevNetworkConfig, 121); err != nil { t.Fatal(err) }
	first, _, _ := LoadPeerProvider(state, firstEnvelope.Announcement.PeerID)
	secondEnvelope := providerEnvelope(t, state, delegation, 1, "round-b")
	secondEnvelope.Announcement.Endpoints = []string{"wqpu://192.0.2.12:7443"}
	payloadHash, _ := ProviderAnnouncementHash(secondEnvelope.Announcement)
	action := SessionAction{WQPUChainID: delegation.WQPUChainID, Wallet: delegation.Wallet, Session: delegation.Session, ActionKind: ActionPublishProvider, ActionNonce: 1, Permission: SessionPermProvider, PayloadHash: payloadHash, ProtocolVersion: uint32(ProtocolVersion)}
	sessionKey, _ := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	digest, _ := ActionDigest(action, DevNetworkConfig.EVMChainID)
	secondEnvelope.Signature, _ = crypto.Sign(digest, sessionKey)
	if err := CommitProviderPublishV2(state, secondEnvelope, DevNetworkConfig, 122); err != nil { t.Fatal(err) }
	second, _, _ := LoadPeerProvider(state, secondEnvelope.Announcement.PeerID)

	price := InitialGlobalPrice
	partA, _ := ChargeForUnits(price, 1)
	partB, _ := ChargeForUnits(price, 1)
	request := JobRequest{
		JobID: crypto.Keccak256Hash([]byte("rounding-job")), RequesterWallet: delegation.Wallet,
		ModelHash: first.ModelHashes[0], PromptCommitment: crypto.Keccak256Hash([]byte("prompt")), PriceEpoch: 0, PricePerMillionUnits: price,
		MaxComputeUnits: 2, MaxChargeUnits: partA + partB, ModelBytes: 2,
		Providers: []JobProviderReservation{
			{ProviderWallet: first.Wallet, ProviderPeerID: first.PeerID, ReservedComputeUnits: 1, AssignedModelBytes: 1},
			{ProviderWallet: second.Wallet, ProviderPeerID: second.PeerID, ReservedComputeUnits: 1, AssignedModelBytes: 1},
		}, ProtocolVersion: uint32(ProtocolVersion),
	}
	if err := request.Validate(); err != nil { t.Fatal(err) }
}
