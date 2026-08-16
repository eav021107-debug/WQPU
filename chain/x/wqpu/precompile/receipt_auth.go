package precompile

import (
	"encoding/binary"
	"encoding/hex"
	"errors"
	"strconv"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/signer/core/apitypes"
)

const (
	receiptEnvelopeCodec byte = 1
	ReceiptSignatureBytes = 65
	MaxReceiptEnvelopeBytes = 1024
)

type ReceiptEnvelope struct {
	Receipt            WorkReceipt
	RequesterSignature []byte
	ProviderSignature  []byte
}

func ReceiptTypedData(receipt WorkReceipt, requesterSession, providerSession common.Address, config NetworkConfig) (apitypes.TypedData, error) {
	if config.WQPUChainID == "" || config.EVMChainID == 0 || requesterSession == (common.Address{}) || providerSession == (common.Address{}) {
		return apitypes.TypedData{}, errors.New("invalid WQPU receipt signing context")
	}
	if _, err := EncodeWorkReceipt(receipt); err != nil { return apitypes.TypedData{}, err }
	return apitypes.TypedData{
		Types: apitypes.Types{
			"EIP712Domain": {{Name: "name", Type: "string"}, {Name: "version", Type: "string"}, {Name: "chainId", Type: "uint256"}},
			"WQPUWorkReceipt": {
				{Name: "wqpuChainId", Type: "string"}, {Name: "jobId", Type: "bytes32"},
				{Name: "providerWallet", Type: "address"}, {Name: "providerPeerId", Type: "bytes32"},
				{Name: "requesterSession", Type: "address"}, {Name: "providerSession", Type: "address"},
				{Name: "sequence", Type: "uint64"}, {Name: "computeUnits", Type: "uint64"},
				{Name: "cumulativeComputeUnits", Type: "uint64"}, {Name: "cumulativePaymentUnits", Type: "uint256"},
				{Name: "resultCommitment", Type: "bytes32"}, {Name: "protocolVersion", Type: "uint32"},
			},
		},
		PrimaryType: "WQPUWorkReceipt",
		Domain: apitypes.TypedDataDomain{Name: "WQPU Work Receipt", Version: "1", ChainId: decimal256(config.EVMChainID)},
		Message: apitypes.TypedDataMessage{
			"wqpuChainId": config.WQPUChainID, "jobId": receipt.JobID.Hex(),
			"providerWallet": receipt.ProviderWallet.Hex(), "providerPeerId": receipt.ProviderPeerID.Hex(),
			"requesterSession": requesterSession.Hex(), "providerSession": providerSession.Hex(),
			"sequence": strconv.FormatUint(receipt.Sequence, 10), "computeUnits": strconv.FormatUint(receipt.ComputeUnits, 10),
			"cumulativeComputeUnits": strconv.FormatUint(receipt.CumulativeComputeUnits, 10), "cumulativePaymentUnits": strconv.FormatUint(receipt.CumulativePaymentUnits, 10),
			"resultCommitment": receipt.ResultCommitment.Hex(), "protocolVersion": strconv.FormatUint(uint64(receipt.ProtocolVersion), 10),
		},
	}, nil
}

func ReceiptDigest(receipt WorkReceipt, requesterSession, providerSession common.Address, config NetworkConfig) ([]byte, error) {
	typed, err := ReceiptTypedData(receipt, requesterSession, providerSession, config)
	if err != nil { return nil, err }
	digest, _, err := apitypes.TypedDataAndHash(typed)
	if err != nil { return nil, err }
	if len(digest) != 32 { return nil, errors.New("unexpected WQPU receipt digest length") }
	return digest, nil
}

func verifyDigestSigner(digest, signature []byte, expected common.Address) error {
	if expected == (common.Address{}) || len(digest) != 32 || len(signature) != ReceiptSignatureBytes {
		return errors.New("invalid WQPU signature context")
	}
	sig, err := decodeSignature("0x" + hex.EncodeToString(signature))
	if err != nil { return err }
	pub, err := crypto.SigToPub(digest, sig)
	if err != nil { return errors.New("cannot recover WQPU receipt signer") }
	if crypto.PubkeyToAddress(*pub) != expected { return errors.New("WQPU receipt signed by unexpected session") }
	return nil
}

func EncodeReceiptEnvelope(envelope ReceiptEnvelope) ([]byte, error) {
	if len(envelope.RequesterSignature) != ReceiptSignatureBytes || len(envelope.ProviderSignature) != ReceiptSignatureBytes {
		return nil, errors.New("WQPU receipt requires two 65-byte signatures")
	}
	receipt, err := EncodeWorkReceipt(envelope.Receipt)
	if err != nil { return nil, err }
	out := []byte{receiptEnvelopeCodec}
	out = appendUint16(out, uint16(len(receipt)))
	out = append(out, receipt...)
	out = append(out, envelope.RequesterSignature...)
	out = append(out, envelope.ProviderSignature...)
	return out, nil
}

func DecodeReceiptEnvelope(data []byte) (ReceiptEnvelope, error) {
	if len(data) < 1+2+ReceiptSignatureBytes*2 || data[0] != receiptEnvelopeCodec { return ReceiptEnvelope{}, errors.New("invalid WQPU receipt envelope") }
	length := int(binary.BigEndian.Uint16(data[1:3]))
	if length <= 0 || 3+length+ReceiptSignatureBytes*2 != len(data) { return ReceiptEnvelope{}, errors.New("invalid WQPU receipt envelope length") }
	receipt, err := DecodeWorkReceipt(data[3:3+length])
	if err != nil { return ReceiptEnvelope{}, err }
	start := 3 + length
	return ReceiptEnvelope{
		Receipt: receipt,
		RequesterSignature: append([]byte(nil), data[start:start+ReceiptSignatureBytes]...),
		ProviderSignature: append([]byte(nil), data[start+ReceiptSignatureBytes:]...),
	}, nil
}

func VerifyReceiptEnvelope(state WordState, envelope ReceiptEnvelope, config NetworkConfig, height uint64) error {
	job, exists, err := LoadJob(state, envelope.Receipt.JobID)
	if err != nil { return err }
	if !exists { return errors.New("unknown WQPU job") }
	if height >= job.ExpiresHeight { return errors.New("WQPU job is already expired") }
	reservation, ok := findJobProvider(job, envelope.Receipt.ProviderPeerID)
	if !ok || reservation.ProviderWallet != envelope.Receipt.ProviderWallet { return errors.New("WQPU receipt peer is not reserved by job") }
	providerSession, exists, err := LoadPeerControlSession(state, envelope.Receipt.ProviderPeerID)
	if err != nil { return err }
	if !exists { return errors.New("WQPU peer has no authorized control session") }
	if _, err := ActiveSessionForPermission(state, job.Request.RequesterWallet, job.RequesterSession, height, SessionPermJob); err != nil { return err }
	if _, err := ActiveSessionForPermission(state, reservation.ProviderWallet, providerSession, height, SessionPermProvider); err != nil { return err }
	previous, hasPrevious, err := LoadLatestReceipt(state, envelope.Receipt.JobID, envelope.Receipt.ProviderPeerID)
	if err != nil { return err }
	var previousPtr *WorkReceipt
	if hasPrevious { previousPtr = &previous }
	if err := ValidateWorkReceipt(job, previousPtr, envelope.Receipt); err != nil { return err }
	digest, err := ReceiptDigest(envelope.Receipt, job.RequesterSession, providerSession, config)
	if err != nil { return err }
	if err := verifyDigestSigner(digest, envelope.RequesterSignature, job.RequesterSession); err != nil { return err }
	if err := verifyDigestSigner(digest, envelope.ProviderSignature, providerSession); err != nil { return err }
	return nil
}

func CommitAcceptedReceipt(state WordState, envelope ReceiptEnvelope, config NetworkConfig, height uint64) error {
	if err := VerifyReceiptEnvelope(state, envelope, config, height); err != nil { return err }
	return StoreLatestReceipt(state, envelope.Receipt)
}
