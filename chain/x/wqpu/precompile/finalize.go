package precompile

import (
	"encoding/binary"
	"encoding/hex"
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

const (
	finalizeEnvelopeCodec byte = 1
	FinalizeSignatureBytes = 65
)

type FinalizeEnvelope struct {
	Wallet      common.Address
	Session     common.Address
	ActionNonce uint64
	JobID       common.Hash
	Signature   []byte
}

func FinalizePayloadHash(jobID common.Hash) (common.Hash, error) {
	if jobID == (common.Hash{}) { return common.Hash{}, errors.New("WQPU job id is required") }
	payload := append([]byte("wqpu-finalize/v1"), jobID.Bytes()...)
	return crypto.Keccak256Hash(payload), nil
}

func EncodeFinalizeEnvelope(envelope FinalizeEnvelope) ([]byte, error) {
	if envelope.Wallet == (common.Address{}) || envelope.Session == (common.Address{}) || envelope.JobID == (common.Hash{}) || len(envelope.Signature) != FinalizeSignatureBytes {
		return nil, errors.New("invalid WQPU finalize envelope")
	}
	out := []byte{finalizeEnvelopeCodec}
	out = append(out, envelope.Wallet.Bytes()...)
	out = append(out, envelope.Session.Bytes()...)
	out = appendUint64(out, envelope.ActionNonce)
	out = append(out, envelope.JobID.Bytes()...)
	out = append(out, envelope.Signature...)
	return out, nil
}

func DecodeFinalizeEnvelope(data []byte) (FinalizeEnvelope, error) {
	const expected = 1 + 20 + 20 + 8 + 32 + FinalizeSignatureBytes
	if len(data) != expected || data[0] != finalizeEnvelopeCodec { return FinalizeEnvelope{}, errors.New("invalid WQPU finalize envelope length/version") }
	pos := 1
	take := func(n int) []byte { out := data[pos:pos+n]; pos += n; return out }
	wallet := common.BytesToAddress(take(20))
	session := common.BytesToAddress(take(20))
	nonce := binary.BigEndian.Uint64(take(8))
	jobID := common.BytesToHash(take(32))
	sig := append([]byte(nil), take(FinalizeSignatureBytes)...)
	envelope := FinalizeEnvelope{Wallet: wallet, Session: session, ActionNonce: nonce, JobID: jobID, Signature: sig}
	if _, err := EncodeFinalizeEnvelope(envelope); err != nil { return FinalizeEnvelope{}, err }
	return envelope, nil
}

func VerifyFinalizeEnvelope(state WordState, envelope FinalizeEnvelope, config NetworkConfig, height uint64) (SessionAction, error) {
	job, exists, err := LoadJob(state, envelope.JobID)
	if err != nil { return SessionAction{}, err }
	if !exists { return SessionAction{}, errors.New("unknown WQPU job") }
	if job.Request.RequesterWallet != envelope.Wallet || job.RequesterSession != envelope.Session {
		return SessionAction{}, errors.New("WQPU finalize signer does not own job")
	}
	payloadHash, err := FinalizePayloadHash(envelope.JobID)
	if err != nil { return SessionAction{}, err }
	action := SessionAction{
		WQPUChainID: config.WQPUChainID, Wallet: envelope.Wallet, Session: envelope.Session,
		ActionKind: ActionFinalizeJob, ActionNonce: envelope.ActionNonce, Permission: SessionPermSettle,
		PayloadHash: payloadHash, ProtocolVersion: uint32(ProtocolVersion),
	}
	if _, err := VerifySessionAction(state, action, SessionPermSettle, config.EVMChainID, height, "0x"+hex.EncodeToString(envelope.Signature)); err != nil {
		return SessionAction{}, err
	}
	return action, nil
}
