package precompile

import (
	"encoding/binary"
	"encoding/hex"
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

const (
	jobReserveEnvelopeCodec byte = 1
	MaxJobRequestBytes = 4096
	JobReserveSignatureBytes = 65
)

type JobReserveEnvelope struct {
	Wallet      common.Address
	Session     common.Address
	ActionNonce uint64
	Request     JobRequest
	Signature   []byte
}

func JobRequestHash(request JobRequest) (common.Hash, error) {
	encoded, err := EncodeJobRequest(request)
	if err != nil { return common.Hash{}, err }
	return crypto.Keccak256Hash(encoded), nil
}

func EncodeJobReserveEnvelope(envelope JobReserveEnvelope) ([]byte, error) {
	if envelope.Wallet == (common.Address{}) || envelope.Session == (common.Address{}) {
		return nil, errors.New("WQPU job envelope wallet/session are required")
	}
	if envelope.Request.RequesterWallet != envelope.Wallet {
		return nil, errors.New("WQPU job requester does not match envelope wallet")
	}
	if len(envelope.Signature) != JobReserveSignatureBytes {
		return nil, errors.New("WQPU job signature must be 65 bytes")
	}
	request, err := EncodeJobRequest(envelope.Request)
	if err != nil { return nil, err }
	if len(request) > MaxJobRequestBytes { return nil, errors.New("WQPU job request exceeds envelope bound") }
	out := []byte{jobReserveEnvelopeCodec}
	out = append(out, envelope.Wallet.Bytes()...)
	out = append(out, envelope.Session.Bytes()...)
	out = appendUint64(out, envelope.ActionNonce)
	out = appendUint16(out, uint16(len(request)))
	out = append(out, request...)
	out = append(out, envelope.Signature...)
	return out, nil
}

func DecodeJobReserveEnvelope(data []byte) (JobReserveEnvelope, error) {
	minimum := 1 + 20 + 20 + 8 + 2 + JobReserveSignatureBytes
	if len(data) < minimum || data[0] != jobReserveEnvelopeCodec { return JobReserveEnvelope{}, errors.New("invalid WQPU job reserve envelope") }
	pos := 1
	take := func(n int) ([]byte, error) {
		if n < 0 || pos > len(data)-n { return nil, errors.New("truncated WQPU job reserve envelope") }
		out := data[pos:pos+n]; pos += n; return out, nil
	}
	walletRaw, err := take(20); if err != nil { return JobReserveEnvelope{}, err }
	sessionRaw, err := take(20); if err != nil { return JobReserveEnvelope{}, err }
	nonceRaw, err := take(8); if err != nil { return JobReserveEnvelope{}, err }
	lengthRaw, err := take(2); if err != nil { return JobReserveEnvelope{}, err }
	length := int(binary.BigEndian.Uint16(lengthRaw))
	if length == 0 || length > MaxJobRequestBytes { return JobReserveEnvelope{}, errors.New("invalid WQPU job request envelope length") }
	requestRaw, err := take(length); if err != nil { return JobReserveEnvelope{}, err }
	signature, err := take(JobReserveSignatureBytes); if err != nil { return JobReserveEnvelope{}, err }
	if pos != len(data) { return JobReserveEnvelope{}, errors.New("trailing bytes in WQPU job reserve envelope") }
	request, err := DecodeJobRequest(requestRaw)
	if err != nil { return JobReserveEnvelope{}, err }
	envelope := JobReserveEnvelope{
		Wallet: common.BytesToAddress(walletRaw), Session: common.BytesToAddress(sessionRaw), ActionNonce: binary.BigEndian.Uint64(nonceRaw),
		Request: request, Signature: append([]byte(nil), signature...),
	}
	if envelope.Wallet == (common.Address{}) || envelope.Session == (common.Address{}) || request.RequesterWallet != envelope.Wallet {
		return JobReserveEnvelope{}, errors.New("WQPU job reserve identity mismatch")
	}
	return envelope, nil
}

func VerifyJobReserve(state WordState, envelope JobReserveEnvelope, config NetworkConfig, height uint64) (SessionAction, error) {
	if config.WQPUChainID == "" || config.EVMChainID == 0 { return SessionAction{}, errors.New("invalid WQPU network config") }
	payloadHash, err := JobRequestHash(envelope.Request)
	if err != nil { return SessionAction{}, err }
	action := SessionAction{
		WQPUChainID: config.WQPUChainID, Wallet: envelope.Wallet, Session: envelope.Session,
		ActionKind: ActionReserveJob, ActionNonce: envelope.ActionNonce, Permission: SessionPermJob,
		PayloadHash: payloadHash, ProtocolVersion: uint32(ProtocolVersion),
	}
	if _, err := VerifySessionAction(state, action, SessionPermJob, config.EVMChainID, height, "0x"+hex.EncodeToString(envelope.Signature)); err != nil {
		return SessionAction{}, err
	}
	return action, nil
}

func CommitSignedJobReservation(state WordState, envelope JobReserveEnvelope, config NetworkConfig, height uint64) (JobReservation, error) {
	action, err := VerifyJobReserve(state, envelope, config, height)
	if err != nil { return JobReservation{}, err }
	job, err := CommitJobReservation(state, envelope.Request, envelope.Session, height)
	if err != nil { return JobReservation{}, err }
	if err := AdvanceSessionActionNonce(state, envelope.Wallet, envelope.Session, action.ActionNonce); err != nil { return JobReservation{}, err }
	return job, nil
}
