package precompile

import (
	"encoding/binary"
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

const receiptCodecVersion byte = 1

type WorkReceipt struct {
	JobID                  common.Hash
	ProviderWallet          common.Address
	ProviderPeerID          common.Hash
	Sequence                uint64
	ComputeUnits            uint64
	CumulativeComputeUnits  uint64
	CumulativePaymentUnits  uint64
	ResultCommitment        common.Hash
	ProtocolVersion         uint32
}

func findJobProvider(job JobReservation, peerID common.Hash) (JobProviderReservation, bool) {
	for _, provider := range job.Request.Providers {
		if provider.ProviderPeerID == peerID {
			return provider, true
		}
	}
	return JobProviderReservation{}, false
}

func ValidateWorkReceipt(job JobReservation, previous *WorkReceipt, receipt WorkReceipt) error {
	if receipt.JobID != job.Request.JobID || receipt.ProviderWallet == (common.Address{}) || receipt.ProviderPeerID == (common.Hash{}) || receipt.ResultCommitment == (common.Hash{}) {
		return errors.New("invalid WQPU receipt identity")
	}
	if receipt.ProtocolVersion != uint32(ProtocolVersion) {
		return errors.New("unsupported WQPU receipt protocol version")
	}
	if receipt.Sequence == 0 || receipt.ComputeUnits == 0 || receipt.CumulativeComputeUnits == 0 || receipt.CumulativePaymentUnits == 0 {
		return errors.New("WQPU receipt work and counters must be positive")
	}
	reservation, ok := findJobProvider(job, receipt.ProviderPeerID)
	if !ok || reservation.ProviderWallet != receipt.ProviderWallet {
		return errors.New("WQPU receipt provider is not reserved by job")
	}
	if receipt.CumulativeComputeUnits > reservation.ReservedComputeUnits {
		return errors.New("WQPU receipt exceeds provider compute reservation")
	}
	if previous == nil {
		if receipt.Sequence != 1 || receipt.CumulativeComputeUnits != receipt.ComputeUnits {
			return errors.New("first WQPU receipt must start at sequence 1 and exact cumulative work")
		}
	} else {
		if previous.JobID != receipt.JobID || previous.ProviderPeerID != receipt.ProviderPeerID || previous.ProviderWallet != receipt.ProviderWallet {
			return errors.New("previous WQPU receipt belongs to another stream")
		}
		if previous.Sequence == ^uint64(0) || receipt.Sequence != previous.Sequence+1 {
			return errors.New("WQPU receipt sequence must increase exactly by one")
		}
		if receipt.ComputeUnits > ^uint64(0)-previous.CumulativeComputeUnits || receipt.CumulativeComputeUnits != previous.CumulativeComputeUnits+receipt.ComputeUnits {
			return errors.New("WQPU cumulative compute must advance exactly by receipt work")
		}
	}
	expectedPayment, err := ChargeForUnits(job.Request.PricePerMillionUnits, receipt.CumulativeComputeUnits)
	if err != nil {
		return err
	}
	if receipt.CumulativePaymentUnits != expectedPayment {
		return errors.New("WQPU receipt payment does not match global job price")
	}
	return nil
}

func EncodeWorkReceipt(receipt WorkReceipt) ([]byte, error) {
	// Structural validation that does not need the job itself.
	if receipt.JobID == (common.Hash{}) || receipt.ProviderWallet == (common.Address{}) || receipt.ProviderPeerID == (common.Hash{}) || receipt.ResultCommitment == (common.Hash{}) {
		return nil, errors.New("invalid WQPU receipt identity")
	}
	if receipt.Sequence == 0 || receipt.ComputeUnits == 0 || receipt.CumulativeComputeUnits == 0 || receipt.CumulativePaymentUnits == 0 || receipt.ProtocolVersion != uint32(ProtocolVersion) {
		return nil, errors.New("invalid WQPU receipt counters/version")
	}
	out := []byte{receiptCodecVersion}
	out = append(out, receipt.JobID.Bytes()...)
	out = append(out, receipt.ProviderWallet.Bytes()...)
	out = append(out, receipt.ProviderPeerID.Bytes()...)
	out = appendUint64(out, receipt.Sequence)
	out = appendUint64(out, receipt.ComputeUnits)
	out = appendUint64(out, receipt.CumulativeComputeUnits)
	out = appendUint64(out, receipt.CumulativePaymentUnits)
	out = append(out, receipt.ResultCommitment.Bytes()...)
	out = appendUint32(out, receipt.ProtocolVersion)
	return out, nil
}

func DecodeWorkReceipt(data []byte) (WorkReceipt, error) {
	const expected = 1 + 32 + 20 + 32 + 8*4 + 32 + 4
	if len(data) != expected || data[0] != receiptCodecVersion {
		return WorkReceipt{}, errors.New("invalid WQPU receipt codec/length")
	}
	pos := 1
	take := func(n int) []byte { out := data[pos:pos+n]; pos += n; return out }
	receipt := WorkReceipt{
		JobID: common.BytesToHash(take(32)),
		ProviderWallet: common.BytesToAddress(take(20)),
		ProviderPeerID: common.BytesToHash(take(32)),
		Sequence: binary.BigEndian.Uint64(take(8)),
		ComputeUnits: binary.BigEndian.Uint64(take(8)),
		CumulativeComputeUnits: binary.BigEndian.Uint64(take(8)),
		CumulativePaymentUnits: binary.BigEndian.Uint64(take(8)),
		ResultCommitment: common.BytesToHash(take(32)),
		ProtocolVersion: binary.BigEndian.Uint32(take(4)),
	}
	if _, err := EncodeWorkReceipt(receipt); err != nil {
		return WorkReceipt{}, err
	}
	return receipt, nil
}

func receiptStorageKey(jobID, peerID common.Hash) []byte {
	out := make([]byte, 0, 64)
	out = append(out, jobID.Bytes()...)
	out = append(out, peerID.Bytes()...)
	return out
}

func StoreLatestReceipt(state WordState, receipt WorkReceipt) error {
	encoded, err := EncodeWorkReceipt(receipt)
	if err != nil { return err }
	return WriteBlob(state, "latest-receipt", receiptStorageKey(receipt.JobID, receipt.ProviderPeerID), encoded)
}

func LoadLatestReceipt(state WordState, jobID, peerID common.Hash) (WorkReceipt, bool, error) {
	if state == nil || jobID == (common.Hash{}) || peerID == (common.Hash{}) {
		return WorkReceipt{}, false, errors.New("valid state, WQPU job and peer id required")
	}
	encoded, err := ReadBlob(state, "latest-receipt", receiptStorageKey(jobID, peerID))
	if err != nil { return WorkReceipt{}, false, err }
	if len(encoded) == 0 { return WorkReceipt{}, false, nil }
	receipt, err := DecodeWorkReceipt(encoded)
	if err != nil { return WorkReceipt{}, false, err }
	if receipt.JobID != jobID || receipt.ProviderPeerID != peerID {
		return WorkReceipt{}, false, errors.New("WQPU receipt stored under wrong stream")
	}
	return receipt, true, nil
}

func DeleteLatestReceipt(state WordState, jobID, peerID common.Hash) error {
	return DeleteBlob(state, "latest-receipt", receiptStorageKey(jobID, peerID))
}
