package precompile

import (
	"encoding/hex"
	"testing"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/vm"
	"github.com/ethereum/go-ethereum/crypto"
)

func signedFinalizeEnvelope(t *testing.T, state *memoryState, delegation SessionDelegation, job JobReservation, nonce uint64) FinalizeEnvelope {
	t.Helper()
	payloadHash, err := FinalizePayloadHash(job.Request.JobID)
	if err != nil { t.Fatal(err) }
	action := SessionAction{
		WQPUChainID: delegation.WQPUChainID, Wallet: delegation.Wallet, Session: delegation.Session,
		ActionKind: ActionFinalizeJob, ActionNonce: nonce, Permission: SessionPermSettle,
		PayloadHash: payloadHash, ProtocolVersion: uint32(ProtocolVersion),
	}
	sessionKey, err := crypto.HexToECDSA("8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3")
	if err != nil { t.Fatal(err) }
	digest, err := ActionDigest(action, DevNetworkConfig.EVMChainID)
	if err != nil { t.Fatal(err) }
	sig, err := crypto.Sign(digest, sessionKey)
	if err != nil { t.Fatal(err) }
	return FinalizeEnvelope{Wallet: delegation.Wallet, Session: delegation.Session, ActionNonce: nonce, JobID: job.Request.JobID, Signature: sig}
}

func finalizeInput(t *testing.T, envelope FinalizeEnvelope) []byte {
	t.Helper()
	raw, err := EncodeFinalizeEnvelope(envelope)
	if err != nil { t.Fatal(err) }
	args := append(abiUintWord(32), encodeDynamicBytes(raw)[32:]...)
	input := append([]byte{}, selectorFinalizeJob[:]...)
	return append(input, args...)
}

func TestFinalizeEnvelopeCodecAndABI(t *testing.T) {
	state, delegation, _, job := receiptFixture(t)
	// receiptFixture used a session without Settle permission, but codec/ABI do
	// not weaken permissions; VerifyFinalizeEnvelope separately enforces them.
	envelope := FinalizeEnvelope{Wallet: delegation.Wallet, Session: delegation.Session, ActionNonce: 2, JobID: job.Request.JobID, Signature: make([]byte, 65)}
	raw, err := EncodeFinalizeEnvelope(envelope)
	if err != nil { t.Fatal(err) }
	decoded, err := DecodeFinalizeEnvelope(raw)
	if err != nil { t.Fatal(err) }
	if decoded.JobID != envelope.JobID || decoded.Wallet != envelope.Wallet { t.Fatalf("decoded=%+v", decoded) }
	input := finalizeInput(t, envelope)
	decodedABI, err := decodeFinalizeJob(input)
	if err != nil || decodedABI.JobID != envelope.JobID { t.Fatalf("decoded=%+v err=%v", decodedABI, err) }
	_ = state
}

func TestTimeoutABIIsExactBytes32(t *testing.T) {
	jobID := crypto.Keccak256Hash([]byte("timeout-job"))
	input := append(append([]byte{}, selectorTimeoutJob[:]...), jobID.Bytes()...)
	decoded, err := decodeTimeoutJob(input)
	if err != nil || decoded != jobID { t.Fatalf("decoded=%s err=%v", decoded.Hex(), err) }
	if _, err := decodeTimeoutJob(append(input, 0)); err == nil { t.Fatal("timeoutJob trailing byte should fail") }
}

func TestSettlementContractGasAndRegistration(t *testing.T) {
	contract := NewSettlementNetworkContract(DevNetworkConfig)
	if gas := contract.RequiredGas(selectorFinalizeJob[:]); gas != finalizeJobGas { t.Fatalf("finalize gas=%d", gas) }
	if gas := contract.RequiredGas(selectorTimeoutJob[:]); gas != timeoutJobGas { t.Fatalf("timeout gas=%d", gas) }
	precompiles := WithWQPUSettlementNetwork(nil)
	registered, ok := precompiles[Address]
	if !ok { t.Fatal("settlement WQPU contract missing") }
	if _, ok := registered.(SettlementNetworkContract); !ok { t.Fatalf("unexpected contract type %T", registered) }
}

func TestSettlementContractRefusesAddressCollision(t *testing.T) {
	precompiles := map[common.Address]vm.PrecompiledContract{Address: occupiedContract{}}
	defer func() { if recover() == nil { t.Fatal("settlement WQPU registration should reject collision") } }()
	_ = WithWQPUSettlementNetwork(precompiles)
}

func TestFinalizeSignatureRequiresSettlePermission(t *testing.T) {
	state, delegation, _, job := receiptFixture(t)
	envelope := signedFinalizeEnvelope(t, state, delegation, job, 2)
	if _, err := VerifyFinalizeEnvelope(state, envelope, DevNetworkConfig, 124); err == nil {
		t.Fatal("job-only session must not finalize without Settle permission")
	}
	_ = hex.EncodeToString // keep test imports tied to signature representation
}
