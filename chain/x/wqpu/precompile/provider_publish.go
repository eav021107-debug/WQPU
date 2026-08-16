package precompile

import (
	"encoding/binary"
	"encoding/hex"
	"errors"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
)

const (
	providerPublishEnvelopeCodec byte = 1
	MaxProviderAnnouncementBytes = 4096
	ProviderPublishSignatureBytes = 65
)

type ProviderPublishEnvelope struct {
	Wallet       common.Address
	Session      common.Address
	ActionNonce  uint64
	Announcement ProviderAnnouncement
	Signature    []byte
}

func EncodeProviderPublishEnvelope(envelope ProviderPublishEnvelope) ([]byte, error) {
	if envelope.Wallet == (common.Address{}) || envelope.Session == (common.Address{}) {
		return nil, errors.New("provider publish wallet and session are required")
	}
	if envelope.Announcement.Wallet != envelope.Wallet {
		return nil, errors.New("provider announcement wallet does not match envelope")
	}
	if len(envelope.Signature) != ProviderPublishSignatureBytes {
		return nil, errors.New("provider publish signature must be 65 bytes")
	}
	announcement, err := EncodeProviderAnnouncement(envelope.Announcement)
	if err != nil {
		return nil, err
	}
	if len(announcement) > MaxProviderAnnouncementBytes {
		return nil, errors.New("provider announcement exceeds publish envelope limit")
	}
	out := []byte{providerPublishEnvelopeCodec}
	out = append(out, envelope.Wallet.Bytes()...)
	out = append(out, envelope.Session.Bytes()...)
	out = appendUint64(out, envelope.ActionNonce)
	out = appendUint16(out, uint16(len(announcement)))
	out = append(out, announcement...)
	out = append(out, envelope.Signature...)
	return out, nil
}

func DecodeProviderPublishEnvelope(data []byte) (ProviderPublishEnvelope, error) {
	minimum := 1 + common.AddressLength*2 + 8 + 2 + ProviderPublishSignatureBytes
	if len(data) < minimum || data[0] != providerPublishEnvelopeCodec {
		return ProviderPublishEnvelope{}, errors.New("invalid WQPU provider publish envelope")
	}
	pos := 1
	take := func(n int) ([]byte, error) {
		if n < 0 || pos > len(data)-n {
			return nil, errors.New("truncated WQPU provider publish envelope")
		}
		out := data[pos : pos+n]
		pos += n
		return out, nil
	}
	walletRaw, err := take(common.AddressLength); if err != nil { return ProviderPublishEnvelope{}, err }
	sessionRaw, err := take(common.AddressLength); if err != nil { return ProviderPublishEnvelope{}, err }
	nonceRaw, err := take(8); if err != nil { return ProviderPublishEnvelope{}, err }
	lengthRaw, err := take(2); if err != nil { return ProviderPublishEnvelope{}, err }
	length := int(binary.BigEndian.Uint16(lengthRaw))
	if length == 0 || length > MaxProviderAnnouncementBytes {
		return ProviderPublishEnvelope{}, errors.New("invalid WQPU provider announcement envelope length")
	}
	announcementRaw, err := take(length); if err != nil { return ProviderPublishEnvelope{}, err }
	signature, err := take(ProviderPublishSignatureBytes); if err != nil { return ProviderPublishEnvelope{}, err }
	if pos != len(data) {
		return ProviderPublishEnvelope{}, errors.New("trailing bytes in WQPU provider publish envelope")
	}
	announcement, err := DecodeProviderAnnouncement(announcementRaw)
	if err != nil {
		return ProviderPublishEnvelope{}, err
	}
	out := ProviderPublishEnvelope{
		Wallet: common.BytesToAddress(walletRaw),
		Session: common.BytesToAddress(sessionRaw),
		ActionNonce: binary.BigEndian.Uint64(nonceRaw),
		Announcement: announcement,
		Signature: append([]byte(nil), signature...),
	}
	if out.Wallet == (common.Address{}) || out.Session == (common.Address{}) || out.Announcement.Wallet != out.Wallet {
		return ProviderPublishEnvelope{}, errors.New("provider publish identity mismatch")
	}
	return out, nil
}

func ProviderAnnouncementHash(announcement ProviderAnnouncement) (common.Hash, error) {
	encoded, err := EncodeProviderAnnouncement(announcement)
	if err != nil {
		return common.Hash{}, err
	}
	return crypto.Keccak256Hash(encoded), nil
}

func VerifyProviderPublish(
	state WordState,
	envelope ProviderPublishEnvelope,
	config NetworkConfig,
	height uint64,
) (SessionAction, error) {
	if config.WQPUChainID == "" || config.EVMChainID == 0 {
		return SessionAction{}, errors.New("invalid WQPU network config")
	}
	if envelope.Announcement.Wallet != envelope.Wallet {
		return SessionAction{}, errors.New("provider wallet mismatch")
	}
	payloadHash, err := ProviderAnnouncementHash(envelope.Announcement)
	if err != nil {
		return SessionAction{}, err
	}
	action := SessionAction{
		WQPUChainID: config.WQPUChainID,
		Wallet: envelope.Wallet,
		Session: envelope.Session,
		ActionKind: ActionPublishProvider,
		ActionNonce: envelope.ActionNonce,
		Permission: SessionPermProvider,
		PayloadHash: payloadHash,
		ProtocolVersion: uint32(ProtocolVersion),
	}
	signatureHex := "0x" + hex.EncodeToString(envelope.Signature)
	if _, err := VerifySessionAction(state, action, SessionPermProvider, config.EVMChainID, height, signatureHex); err != nil {
		return SessionAction{}, err
	}
	return action, nil
}

// CommitProviderPublish mutates registry + nonce. The precompile caller must
// wrap it in an EVM StateDB snapshot and revert the snapshot on any error.
func CommitProviderPublish(state WordState, envelope ProviderPublishEnvelope, config NetworkConfig, height uint64) error {
	action, err := VerifyProviderPublish(state, envelope, config, height)
	if err != nil {
		return err
	}
	provider, err := envelope.Announcement.ToRecord(height)
	if err != nil {
		return err
	}
	if err := StorePeerProvider(state, provider); err != nil {
		return err
	}
	return AdvanceSessionActionNonce(state, envelope.Wallet, envelope.Session, action.ActionNonce)
}
