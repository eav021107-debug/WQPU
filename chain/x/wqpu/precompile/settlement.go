package precompile

import (
	"encoding/binary"
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

const settlementCodecVersion byte = 1

type ProviderPayout struct {
	ProviderWallet common.Address
	ProviderPeerID common.Hash
	ComputeUnits   uint64
	PaymentUnits   uint64
}

type JobSettlement struct {
	JobID           common.Hash
	RequesterWallet common.Address
	TotalCharge     uint64
	TimedOut        bool
	FinalizedHeight uint64
	Payouts         []ProviderPayout
	ProtocolVersion uint32
}

func validateLatestReceiptForSettlement(job JobReservation, reservation JobProviderReservation, receipt WorkReceipt) error {
	if receipt.JobID != job.Request.JobID || receipt.ProviderPeerID != reservation.ProviderPeerID || receipt.ProviderWallet != reservation.ProviderWallet {
		return errors.New("WQPU settlement receipt identity mismatch")
	}
	if receipt.Sequence == 0 || receipt.CumulativeComputeUnits == 0 || receipt.CumulativeComputeUnits > reservation.ReservedComputeUnits {
		return errors.New("WQPU settlement receipt compute is outside reservation")
	}
	expected, err := ChargeForUnits(job.Request.PricePerMillionUnits, receipt.CumulativeComputeUnits)
	if err != nil { return err }
	if receipt.CumulativePaymentUnits != expected {
		return errors.New("WQPU settlement receipt payment does not match job price")
	}
	return nil
}

func BuildJobSettlement(state WordState, job JobReservation, height uint64, timedOut bool) (JobSettlement, error) {
	if timedOut && height < job.ExpiresHeight { return JobSettlement{}, errors.New("WQPU job has not timed out") }
	if !timedOut && height >= job.ExpiresHeight { return JobSettlement{}, errors.New("expired WQPU job requires timeout settlement") }
	settlement := JobSettlement{
		JobID: job.Request.JobID, RequesterWallet: job.Request.RequesterWallet,
		TimedOut: timedOut, FinalizedHeight: height, ProtocolVersion: uint32(ProtocolVersion),
	}
	for _, reservation := range job.Request.Providers {
		receipt, exists, err := LoadLatestReceipt(state, job.Request.JobID, reservation.ProviderPeerID)
		if err != nil { return JobSettlement{}, err }
		if !exists { continue }
		if err := validateLatestReceiptForSettlement(job, reservation, receipt); err != nil { return JobSettlement{}, err }
		if receipt.CumulativePaymentUnits > ^uint64(0)-settlement.TotalCharge {
			return JobSettlement{}, errors.New("WQPU settlement total charge overflow")
		}
		settlement.TotalCharge += receipt.CumulativePaymentUnits
		settlement.Payouts = append(settlement.Payouts, ProviderPayout{
			ProviderWallet: reservation.ProviderWallet, ProviderPeerID: reservation.ProviderPeerID,
			ComputeUnits: receipt.CumulativeComputeUnits, PaymentUnits: receipt.CumulativePaymentUnits,
		})
	}
	if settlement.TotalCharge > job.Request.MaxChargeUnits {
		return JobSettlement{}, errors.New("WQPU settlement exceeds reserved maximum charge")
	}
	return settlement, nil
}

func EncodeJobSettlement(settlement JobSettlement) ([]byte, error) {
	if settlement.JobID == (common.Hash{}) || settlement.RequesterWallet == (common.Address{}) || settlement.ProtocolVersion != uint32(ProtocolVersion) {
		return nil, errors.New("invalid WQPU settlement identity/version")
	}
	if len(settlement.Payouts) > MaxJobProviders { return nil, errors.New("too many WQPU settlement payouts") }
	var total uint64
	seen := map[common.Hash]struct{}{}
	for _, payout := range settlement.Payouts {
		if payout.ProviderWallet == (common.Address{}) || payout.ProviderPeerID == (common.Hash{}) || payout.ComputeUnits == 0 || payout.PaymentUnits == 0 {
			return nil, errors.New("invalid WQPU settlement payout")
		}
		if _, exists := seen[payout.ProviderPeerID]; exists { return nil, errors.New("duplicate WQPU settlement peer") }
		seen[payout.ProviderPeerID] = struct{}{}
		if payout.PaymentUnits > ^uint64(0)-total { return nil, errors.New("WQPU settlement payout overflow") }
		total += payout.PaymentUnits
	}
	if total != settlement.TotalCharge { return nil, errors.New("WQPU settlement payout sum mismatch") }
	out := []byte{settlementCodecVersion}
	out = append(out, settlement.JobID.Bytes()...)
	out = append(out, settlement.RequesterWallet.Bytes()...)
	out = appendUint64(out, settlement.TotalCharge)
	if settlement.TimedOut { out = append(out, 1) } else { out = append(out, 0) }
	out = appendUint64(out, settlement.FinalizedHeight)
	out = appendUint32(out, settlement.ProtocolVersion)
	out = append(out, byte(len(settlement.Payouts)))
	for _, payout := range settlement.Payouts {
		out = append(out, payout.ProviderWallet.Bytes()...)
		out = append(out, payout.ProviderPeerID.Bytes()...)
		out = appendUint64(out, payout.ComputeUnits)
		out = appendUint64(out, payout.PaymentUnits)
	}
	return out, nil
}

func DecodeJobSettlement(data []byte) (JobSettlement, error) {
	if len(data) < 1+32+20+8+1+8+4+1 || data[0] != settlementCodecVersion { return JobSettlement{}, errors.New("invalid WQPU settlement codec") }
	pos := 1
	take := func(n int) ([]byte, error) { if n < 0 || pos > len(data)-n { return nil, errors.New("truncated WQPU settlement") }; out := data[pos:pos+n]; pos += n; return out, nil }
	read64 := func() (uint64, error) { raw, err := take(8); if err != nil { return 0, err }; return binary.BigEndian.Uint64(raw), nil }
	jobRaw, err := take(32); if err != nil { return JobSettlement{}, err }
	walletRaw, err := take(20); if err != nil { return JobSettlement{}, err }
	total, err := read64(); if err != nil { return JobSettlement{}, err }
	timedRaw, err := take(1); if err != nil || timedRaw[0] > 1 { return JobSettlement{}, errors.New("invalid WQPU settlement timeout flag") }
	height, err := read64(); if err != nil { return JobSettlement{}, err }
	protocolRaw, err := take(4); if err != nil { return JobSettlement{}, err }
	countRaw, err := take(1); if err != nil { return JobSettlement{}, err }
	count := int(countRaw[0]); if count > MaxJobProviders { return JobSettlement{}, errors.New("invalid WQPU settlement payout count") }
	payouts := make([]ProviderPayout, 0, count)
	for i := 0; i < count; i++ {
		providerWalletRaw, err := take(20); if err != nil { return JobSettlement{}, err }
		peerRaw, err := take(32); if err != nil { return JobSettlement{}, err }
		compute, err := read64(); if err != nil { return JobSettlement{}, err }
		payment, err := read64(); if err != nil { return JobSettlement{}, err }
		payouts = append(payouts, ProviderPayout{ProviderWallet: common.BytesToAddress(providerWalletRaw), ProviderPeerID: common.BytesToHash(peerRaw), ComputeUnits: compute, PaymentUnits: payment})
	}
	if pos != len(data) { return JobSettlement{}, errors.New("trailing bytes in WQPU settlement") }
	out := JobSettlement{JobID: common.BytesToHash(jobRaw), RequesterWallet: common.BytesToAddress(walletRaw), TotalCharge: total, TimedOut: timedRaw[0] == 1, FinalizedHeight: height, Payouts: payouts, ProtocolVersion: binary.BigEndian.Uint32(protocolRaw)}
	if _, err := EncodeJobSettlement(out); err != nil { return JobSettlement{}, err }
	return out, nil
}

func StoreJobSettlement(state WordState, settlement JobSettlement) error {
	encoded, err := EncodeJobSettlement(settlement)
	if err != nil { return err }
	return WriteBlob(state, "job-settlement", settlement.JobID.Bytes(), encoded)
}

func LoadJobSettlement(state WordState, jobID common.Hash) (JobSettlement, bool, error) {
	encoded, err := ReadBlob(state, "job-settlement", jobID.Bytes())
	if err != nil { return JobSettlement{}, false, err }
	if len(encoded) == 0 { return JobSettlement{}, false, nil }
	settlement, err := DecodeJobSettlement(encoded)
	if err != nil { return JobSettlement{}, false, err }
	if settlement.JobID != jobID { return JobSettlement{}, false, errors.New("WQPU settlement stored under wrong job") }
	return settlement, true, nil
}

func FinalizeJobAccounting(state WordState, jobID common.Hash, height uint64, timedOut bool) (JobSettlement, error) {
	job, exists, err := LoadJob(state, jobID)
	if err != nil { return JobSettlement{}, err }
	if !exists { return JobSettlement{}, errors.New("unknown WQPU job") }
	settlement, err := BuildJobSettlement(state, job, height, timedOut)
	if err != nil { return JobSettlement{}, err }
	if _, err := EncodeJobSettlement(settlement); err != nil { return JobSettlement{}, err }
	for _, reservation := range job.Request.Providers {
		reserved, err := ReservedPeerUnits(state, reservation.ProviderPeerID)
		if err != nil { return JobSettlement{}, err }
		if reserved < reservation.ReservedComputeUnits { return JobSettlement{}, errors.New("corrupt WQPU peer reservation accounting") }
	}
	session, exists, err := LoadSession(state, job.Request.RequesterWallet, job.RequesterSession)
	if err != nil { return JobSettlement{}, err }
	if !exists || session.ReservedUnits < job.Request.MaxChargeUnits { return JobSettlement{}, errors.New("corrupt WQPU requester reservation accounting") }

	if err := SettleSessionSpend(state, job.Request.RequesterWallet, job.RequesterSession, job.Request.MaxChargeUnits, settlement.TotalCharge); err != nil { return JobSettlement{}, err }
	for _, reservation := range job.Request.Providers {
		reserved, _ := ReservedPeerUnits(state, reservation.ProviderPeerID)
		if err := setReservedPeerUnits(state, reservation.ProviderPeerID, reserved-reservation.ReservedComputeUnits); err != nil { return JobSettlement{}, err }
		if err := DeleteLatestReceipt(state, job.Request.JobID, reservation.ProviderPeerID); err != nil { return JobSettlement{}, err }
	}
	if err := DeleteBlob(state, "job", job.Request.JobID.Bytes()); err != nil { return JobSettlement{}, err }
	if _, err := RemoveIndexedHash(state, "active-jobs", job.Request.JobID); err != nil { return JobSettlement{}, err }
	if err := MarkJobCompleted(state, job.Request.JobID); err != nil { return JobSettlement{}, err }
	if err := StoreJobSettlement(state, settlement); err != nil { return JobSettlement{}, err }
	return settlement, nil
}
