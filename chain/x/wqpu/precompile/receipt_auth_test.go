package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func receiptFixture(t *testing.T) (*memoryState, SessionDelegation, ProviderRecord, JobReservation) {
	t.Helper()
	state, delegation := providerAuthorizedFixture(t)
	envelope := providerEnvelope(t, state, delegation, 0, "receipt-peer")
	if err := CommitProviderPublishV2(state, envelope, DevNetworkConfig, 121); err != nil { t.Fatal(err) }
	provider, exists, err := LoadPeerProvider(state, envelope.Announcement.PeerID)
	if err != nil || !exists { t.Fatalf("provider exists=%v err=%v", exists, err) }
	jobEnvelope := signedJobEnvelope(t, state, delegation, provider, 1, "receipt-job")
	job, err := CommitSignedJobReservation(state, jobEnvelope, DevNetworkConfig, 122)
	if err != nil { t.Fatal(err) }
	return state, delegation, provider, job
}

func signedReceiptEnvelope(t *testing.T, state *memoryState, delegation SessionDelegation, provider ProviderRecord, job JobReservation, sequence, delta, cumulative uint64) ReceiptEnvelope {
	t.Helper()
	payment, err := ChargeForUnits(job.Request.PricePerMillionUnits, cumulative)
	if err != nil { t.Fatal(err) }
	receipt := WorkReceipt{
		JobID: job.Request.JobID,
		ProviderWallet: provider.Wallet,
		ProviderPeerID: provider.PeerID,
		Sequence: sequence,
		ComputeUnits: delta,
		CumulativeComputeUnits: cumulative,
		CumulativePaymentUnits: payment,
		ResultCommitment: crypto.Keccak256Hash([]byte("accepted-result")),
		ProtocolVersion: uint32(ProtocolVersion),
	}
	providerSession, exists, err := LoadPeerControlSession(state, provider.PeerID)
	if err != nil || !exists { t.Fatalf("provider session exists=%v err=%v", exists, err) }
	digest, err := ReceiptDigest(receipt, job.RequesterSession, providerSession, DevNetworkConfig)
	if err != nil { t.Fatal(err) }
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil { t.Fatal(err) }
	requesterSig, err := crypto.Sign(digest, sessionKey)
	if err != nil { t.Fatal(err) }
	providerSig, err := crypto.Sign(digest, sessionKey)
	if err != nil { t.Fatal(err) }
	return ReceiptEnvelope{Receipt: receipt, RequesterSignature: requesterSig, ProviderSignature: providerSig}
}

func TestDualSignedReceiptIsAcceptedAndStored(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	envelope := signedReceiptEnvelope(t, state, delegation, provider, job, 1, 20, 20)
	if err := CommitAcceptedReceipt(state, envelope, DevNetworkConfig, 123); err != nil { t.Fatal(err) }
	stored, exists, err := LoadLatestReceipt(state, job.Request.JobID, provider.PeerID)
	if err != nil || !exists || stored.Sequence != 1 || stored.CumulativeComputeUnits != 20 {
		t.Fatalf("stored=%+v exists=%v err=%v", stored, exists, err)
	}
}

func TestReceiptReplayFails(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	envelope := signedReceiptEnvelope(t, state, delegation, provider, job, 1, 20, 20)
	if err := CommitAcceptedReceipt(state, envelope, DevNetworkConfig, 123); err != nil { t.Fatal(err) }
	if err := CommitAcceptedReceipt(state, envelope, DevNetworkConfig, 124); err == nil { t.Fatal("replayed receipt should fail") }
}

func TestReceiptPayloadTamperingInvalidatesBothPartyAgreement(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	envelope := signedReceiptEnvelope(t, state, delegation, provider, job, 1, 20, 20)
	envelope.Receipt.ResultCommitment = crypto.Keccak256Hash([]byte("different-result"))
	if err := CommitAcceptedReceipt(state, envelope, DevNetworkConfig, 123); err == nil { t.Fatal("tampered receipt should fail") }
}

func TestReceiptCannotExceedReservedProviderWork(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	reserved := job.Request.Providers[0].ReservedComputeUnits
	envelope := signedReceiptEnvelope(t, state, delegation, provider, job, 1, reserved+1, reserved+1)
	if err := CommitAcceptedReceipt(state, envelope, DevNetworkConfig, 123); err == nil { t.Fatal("receipt above provider reservation should fail") }
}

func TestReceiptEnvelopeCodecRoundTrip(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	envelope := signedReceiptEnvelope(t, state, delegation, provider, job, 1, 20, 20)
	encoded, err := EncodeReceiptEnvelope(envelope)
	if err != nil { t.Fatal(err) }
	decoded, err := DecodeReceiptEnvelope(encoded)
	if err != nil { t.Fatal(err) }
	if decoded.Receipt.JobID != envelope.Receipt.JobID || decoded.Receipt.ProviderPeerID != envelope.Receipt.ProviderPeerID || string(decoded.RequesterSignature) != string(envelope.RequesterSignature) || string(decoded.ProviderSignature) != string(envelope.ProviderSignature) {
		t.Fatalf("decoded=%+v", decoded)
	}
	if _, err := DecodeReceiptEnvelope(append(encoded, 0)); err == nil { t.Fatal("trailing receipt envelope bytes should fail") }
}

func TestReceiptNeedsPeerControlSession(t *testing.T) {
	state, delegation, provider, job := receiptFixture(t)
	envelope := signedReceiptEnvelope(t, state, delegation, provider, job, 1, 20, 20)
	if err := DeletePeerControlSession(state, provider.PeerID); err != nil { t.Fatal(err) }
	if err := CommitAcceptedReceipt(state, envelope, DevNetworkConfig, 123); err == nil { t.Fatal("receipt without peer control session should fail") }
}
