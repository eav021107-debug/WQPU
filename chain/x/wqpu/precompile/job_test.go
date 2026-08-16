package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

func publishTestPeer(t *testing.T, state *memoryState, delegation SessionDelegation, nonce uint64, label, endpoint string, height uint64) ProviderRecord {
	t.Helper()
	envelope := providerEnvelope(t, state, delegation, nonce, label)
	envelope.Announcement.Endpoints = []string{endpoint}
	payloadHash, err := ProviderAnnouncementHash(envelope.Announcement)
	if err != nil { t.Fatal(err) }
	action := SessionAction{WQPUChainID: delegation.WQPUChainID, Wallet: delegation.Wallet, Session: delegation.Session, ActionKind: ActionPublishProvider, ActionNonce: nonce, Permission: SessionPermProvider, PayloadHash: payloadHash, ProtocolVersion: uint32(ProtocolVersion)}
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil { t.Fatal(err) }
	digest, err := ActionDigest(action, DevNetworkConfig.EVMChainID)
	if err != nil { t.Fatal(err) }
	envelope.Signature, err = crypto.Sign(digest, sessionKey)
	if err != nil { t.Fatal(err) }
	if err := CommitProviderPublish(state, envelope, DevNetworkConfig, height); err != nil { t.Fatal(err) }
	provider, exists, err := LoadPeerProvider(state, envelope.Announcement.PeerID)
	if err != nil || !exists { t.Fatalf("provider exists=%v err=%v", exists, err) }
	return provider
}

func jobRequestFor(t *testing.T, state *memoryState, wallet common.Address, model common.Hash, providers []JobProviderReservation, modelBytes uint64) JobRequest {
	t.Helper()
	epoch, price, err := CurrentPriceState(state)
	if err != nil { t.Fatal(err) }
	var compute uint64
	for _, provider := range providers { compute += provider.ReservedComputeUnits }
	charge, err := ChargeForUnits(price, compute)
	if err != nil { t.Fatal(err) }
	return JobRequest{
		JobID: crypto.Keccak256Hash([]byte("job-" + string(rune(len(providers)+'0')))),
		RequesterWallet: wallet,
		ModelHash: model,
		PromptCommitment: crypto.Keccak256Hash([]byte("private prompt commitment")),
		PriceEpoch: epoch,
		PricePerMillionUnits: price,
		MaxComputeUnits: compute,
		MaxChargeUnits: charge,
		ModelBytes: modelBytes,
		Providers: providers,
		ProtocolVersion: uint32(ProtocolVersion),
	}
}

func TestJobReservationLocksSpendAndPeerCapacityBeforeWork(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	request := jobRequestFor(t, state, delegation.Wallet, provider.ModelHashes[0], []JobProviderReservation{{
		ProviderWallet: provider.Wallet, ProviderPeerID: provider.PeerID, ReservedComputeUnits: 100, AssignedModelBytes: 1_000_000_000,
	}}, 1_000_000_000)
	job, err := CommitJobReservation(state, request, delegation.Session, 122)
	if err != nil { t.Fatal(err) }
	if job.CreatedHeight != 122 || job.ExpiresHeight != 122+JobTTLBlocks { t.Fatalf("job=%+v", job) }
	reserved, err := ReservedPeerUnits(state, provider.PeerID)
	if err != nil || reserved != 100 { t.Fatalf("peer reserved=%d err=%v", reserved, err) }
	session, _, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil || session.ReservedUnits != request.MaxChargeUnits { t.Fatalf("session=%+v err=%v", session, err) }
}

func TestSecondJobCannotOverbookSamePeerAndDoesNotReserveMoreSpend(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	reservation := []JobProviderReservation{{ProviderWallet: provider.Wallet, ProviderPeerID: provider.PeerID, ReservedComputeUnits: 100, AssignedModelBytes: 1000}}
	first := jobRequestFor(t, state, delegation.Wallet, provider.ModelHashes[0], reservation, 1000)
	first.JobID = crypto.Keccak256Hash([]byte("job-first"))
	if _, err := CommitJobReservation(state, first, delegation.Session, 122); err != nil { t.Fatal(err) }
	session, _, _ := LoadSession(state, delegation.Wallet, delegation.Session)
	before := session.ReservedUnits
	second := first
	second.JobID = crypto.Keccak256Hash([]byte("job-second"))
	if _, err := CommitJobReservation(state, second, delegation.Session, 122); err == nil { t.Fatal("overbooked peer should reject second job") }
	session, _, _ = LoadSession(state, delegation.Wallet, delegation.Session)
	if session.ReservedUnits != before { t.Fatal("failed job changed requester spend reservation") }
}

func TestOneModelCanBeSplitAcrossSeveralDevicesOfSameWallet(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	first := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	second := publishTestPeer(t, state, delegation, 1, "peer-b", "wqpu://192.0.2.11:7443", 122)
	model := first.ModelHashes[0]
	request := jobRequestFor(t, state, delegation.Wallet, model, []JobProviderReservation{
		{ProviderWallet: first.Wallet, ProviderPeerID: first.PeerID, ReservedComputeUnits: 50, AssignedModelBytes: 600_000_000},
		{ProviderWallet: second.Wallet, ProviderPeerID: second.PeerID, ReservedComputeUnits: 50, AssignedModelBytes: 400_000_000},
	}, 1_000_000_000)
	request.JobID = crypto.Keccak256Hash([]byte("distributed-job"))
	job, err := CommitJobReservation(state, request, delegation.Session, 123)
	if err != nil { t.Fatal(err) }
	if len(job.Request.Providers) != 2 { t.Fatalf("providers=%d", len(job.Request.Providers)) }
}

func TestJobRejectsStaleGlobalPriceBeforeChangingState(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	request := jobRequestFor(t, state, delegation.Wallet, provider.ModelHashes[0], []JobProviderReservation{{ProviderWallet: provider.Wallet, ProviderPeerID: provider.PeerID, ReservedComputeUnits: 10, AssignedModelBytes: 1000}}, 1000)
	request.PricePerMillionUnits++
	if _, err := CommitJobReservation(state, request, delegation.Session, 122); err == nil { t.Fatal("stale/tampered price should fail") }
	reserved, _ := ReservedPeerUnits(state, provider.PeerID)
	if reserved != 0 { t.Fatal("failed price check reserved peer capacity") }
}

func TestJobRequiresCompleteComputeAndModelAllocation(t *testing.T) {
	request := JobRequest{
		JobID: crypto.Keccak256Hash([]byte("job")), RequesterWallet: common.HexToAddress("0x1111111111111111111111111111111111111111"),
		ModelHash: crypto.Keccak256Hash([]byte("model")), PromptCommitment: crypto.Keccak256Hash([]byte("prompt")), PricePerMillionUnits: 1000,
		MaxComputeUnits: 10, MaxChargeUnits: 1, ModelBytes: 1000, ProtocolVersion: uint32(ProtocolVersion),
		Providers: []JobProviderReservation{{ProviderWallet: common.HexToAddress("0x2222222222222222222222222222222222222222"), ProviderPeerID: crypto.Keccak256Hash([]byte("peer")), ReservedComputeUnits: 9, AssignedModelBytes: 999}},
	}
	if err := request.Validate(); err == nil { t.Fatal("partial compute/model assignment should fail") }
}

func TestJobCodecRoundTrip(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	request := jobRequestFor(t, state, delegation.Wallet, provider.ModelHashes[0], []JobProviderReservation{{ProviderWallet: provider.Wallet, ProviderPeerID: provider.PeerID, ReservedComputeUnits: 10, AssignedModelBytes: 1000}}, 1000)
	job := JobReservation{Request: request, RequesterSession: delegation.Session, CreatedHeight: 122, ExpiresHeight: 132}
	encoded, err := EncodeJobReservation(job)
	if err != nil { t.Fatal(err) }
	decoded, err := DecodeJobReservation(encoded)
	if err != nil { t.Fatal(err) }
	if decoded.Request.JobID != job.Request.JobID || decoded.RequesterSession != job.RequesterSession || decoded.ExpiresHeight != job.ExpiresHeight || len(decoded.Request.Providers) != 1 {
		t.Fatalf("decoded=%+v", decoded)
	}
}
