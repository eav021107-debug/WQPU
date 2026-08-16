package precompile

import (
	"testing"

	"github.com/ethereum/go-ethereum/crypto"
)

func signedJobEnvelope(t *testing.T, state *memoryState, delegation SessionDelegation, provider ProviderRecord, nonce uint64, jobLabel string) JobReserveEnvelope {
	t.Helper()
	request := jobRequestFor(t, state, delegation.Wallet, provider.ModelHashes[0], []JobProviderReservation{{
		ProviderWallet: provider.Wallet, ProviderPeerID: provider.PeerID, ReservedComputeUnits: 50, AssignedModelBytes: 1000,
	}}, 1000)
	request.JobID = crypto.Keccak256Hash([]byte(jobLabel))
	payloadHash, err := JobRequestHash(request)
	if err != nil { t.Fatal(err) }
	action := SessionAction{
		WQPUChainID: delegation.WQPUChainID, Wallet: delegation.Wallet, Session: delegation.Session,
		ActionKind: ActionReserveJob, ActionNonce: nonce, Permission: SessionPermJob,
		PayloadHash: payloadHash, ProtocolVersion: uint32(ProtocolVersion),
	}
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil { t.Fatal(err) }
	digest, err := ActionDigest(action, DevNetworkConfig.EVMChainID)
	if err != nil { t.Fatal(err) }
	sig, err := crypto.Sign(digest, sessionKey)
	if err != nil { t.Fatal(err) }
	return JobReserveEnvelope{Wallet: delegation.Wallet, Session: delegation.Session, ActionNonce: nonce, Request: request, Signature: sig}
}

func TestSignedJobReservationCommitsAndAdvancesNonce(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	envelope := signedJobEnvelope(t, state, delegation, provider, 1, "signed-job")
	job, err := CommitSignedJobReservation(state, envelope, DevNetworkConfig, 122)
	if err != nil { t.Fatal(err) }
	stored, exists, err := LoadJob(state, job.Request.JobID)
	if err != nil || !exists || stored.Request.JobID != job.Request.JobID { t.Fatalf("stored=%+v exists=%v err=%v", stored, exists, err) }
	session, _, err := LoadSession(state, delegation.Wallet, delegation.Session)
	if err != nil || session.ActionNonce != 2 { t.Fatalf("session=%+v err=%v", session, err) }
}

func TestSignedJobReplayFails(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	envelope := signedJobEnvelope(t, state, delegation, provider, 1, "signed-job")
	if _, err := CommitSignedJobReservation(state, envelope, DevNetworkConfig, 122); err != nil { t.Fatal(err) }
	if _, err := CommitSignedJobReservation(state, envelope, DevNetworkConfig, 123); err == nil { t.Fatal("replayed signed job should fail") }
}

func TestChangingProviderAfterSignatureInvalidatesJob(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	envelope := signedJobEnvelope(t, state, delegation, provider, 1, "signed-job")
	envelope.Request.Providers[0].AssignedModelBytes++
	envelope.Request.ModelBytes++
	if _, err := CommitSignedJobReservation(state, envelope, DevNetworkConfig, 122); err == nil { t.Fatal("tampered signed job should fail") }
}

func TestJobEnvelopeCodecRoundTrip(t *testing.T) {
	state, delegation := providerAuthorizedFixture(t)
	provider := publishTestPeer(t, state, delegation, 0, "peer-a", "wqpu://192.0.2.10:7443", 121)
	envelope := signedJobEnvelope(t, state, delegation, provider, 1, "signed-job")
	encoded, err := EncodeJobReserveEnvelope(envelope)
	if err != nil { t.Fatal(err) }
	decoded, err := DecodeJobReserveEnvelope(encoded)
	if err != nil { t.Fatal(err) }
	if decoded.Wallet != envelope.Wallet || decoded.Session != envelope.Session || decoded.ActionNonce != envelope.ActionNonce || decoded.Request.JobID != envelope.Request.JobID || string(decoded.Signature) != string(envelope.Signature) {
		t.Fatalf("decoded=%+v", decoded)
	}
	if _, err := DecodeJobReserveEnvelope(append(encoded, 0)); err == nil { t.Fatal("trailing job envelope bytes should fail") }
}
