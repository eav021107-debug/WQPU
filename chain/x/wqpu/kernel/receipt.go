package kernel

import "errors"

type WorkReceipt struct {
	JobID                 string
	ProviderWallet         string
	ProviderPeerID         string
	Sequence               uint64
	ComputeUnits           uint64
	CumulativeComputeUnits uint64
	CumulativePaymentUnits uint64
	ResultCommitment       string
}

func (r WorkReceipt) Validate() error {
	if r.JobID == "" || r.ProviderWallet == "" || r.ProviderPeerID == "" || r.ResultCommitment == "" {
		return errors.New("receipt identity/commitment fields must be non-empty")
	}
	if r.Sequence == 0 || r.ComputeUnits == 0 || r.CumulativeComputeUnits == 0 || r.CumulativePaymentUnits == 0 {
		return errors.New("receipt counters and payment must be positive")
	}
	if r.ComputeUnits > r.CumulativeComputeUnits {
		return errors.New("receipt delta exceeds cumulative compute")
	}
	return nil
}

func reservationForPeer(job JobReservation, peerID string) (ProviderReservation, bool) {
	for _, item := range job.Providers {
		if item.ProviderPeerID == peerID {
			return item, true
		}
	}
	return ProviderReservation{}, false
}

func validateReceiptSnapshot(job JobReservation, receipt WorkReceipt) error {
	if err := job.Validate(); err != nil {
		return err
	}
	if err := receipt.Validate(); err != nil {
		return err
	}
	if receipt.JobID != job.JobID {
		return errors.New("receipt belongs to another job")
	}
	reservation, ok := reservationForPeer(job, receipt.ProviderPeerID)
	if !ok || reservation.ProviderWallet != receipt.ProviderWallet {
		return errors.New("receipt provider is not reserved for this job")
	}
	expected, err := ChargeForUnits(job.PricePerMillionUnits, receipt.CumulativeComputeUnits)
	if err != nil {
		return err
	}
	if receipt.CumulativePaymentUnits != expected {
		return errors.New("receipt payment does not match global network price")
	}
	if receipt.CumulativeComputeUnits > job.MaxComputeUnits || receipt.CumulativePaymentUnits > job.MaxChargeUnits {
		return errors.New("receipt exceeds job limits")
	}
	return nil
}

// ValidateReceipt is called only after requester+provider signatures are verified.
// It enforces replay, monotonic accounting and the single global network price.
func ValidateReceipt(job JobReservation, previous *WorkReceipt, receipt WorkReceipt) error {
	if err := validateReceiptSnapshot(job, receipt); err != nil {
		return err
	}

	var oldSequence, oldCompute, oldPayment uint64
	if previous != nil {
		if previous.JobID != job.JobID || previous.ProviderPeerID != receipt.ProviderPeerID {
			return errors.New("previous receipt belongs to another stream")
		}
		oldSequence = previous.Sequence
		oldCompute = previous.CumulativeComputeUnits
		oldPayment = previous.CumulativePaymentUnits
	}
	if receipt.Sequence <= oldSequence {
		return errors.New("receipt sequence must strictly increase")
	}
	if receipt.CumulativeComputeUnits <= oldCompute {
		return errors.New("cumulative compute must strictly increase")
	}
	if receipt.CumulativePaymentUnits < oldPayment {
		return errors.New("cumulative payment cannot decrease")
	}
	if receipt.ComputeUnits != receipt.CumulativeComputeUnits-oldCompute {
		return errors.New("receipt compute delta does not match cumulative compute")
	}
	return nil
}

type Settlement struct {
	JobID       string
	TotalCharge uint64
	Payouts     map[string]uint64
}

func buildSettlement(job JobReservation, latestByPeer map[string]WorkReceipt, requireAll bool) (Settlement, error) {
	if err := job.Validate(); err != nil {
		return Settlement{}, err
	}
	if requireAll && len(latestByPeer) != len(job.Providers) {
		return Settlement{}, errors.New("final settlement requires a receipt from every reserved provider")
	}
	if len(latestByPeer) > len(job.Providers) {
		return Settlement{}, errors.New("settlement contains an unreserved provider")
	}
	for peerID := range latestByPeer {
		if _, ok := reservationForPeer(job, peerID); !ok {
			return Settlement{}, errors.New("settlement contains an unreserved provider")
		}
	}

	out := Settlement{JobID: job.JobID, Payouts: map[string]uint64{}}
	var totalCompute uint64
	for _, reservation := range job.Providers {
		receipt, ok := latestByPeer[reservation.ProviderPeerID]
		if !ok {
			if requireAll {
				return Settlement{}, errors.New("missing provider receipt")
			}
			continue
		}
		if err := validateReceiptSnapshot(job, receipt); err != nil {
			return Settlement{}, err
		}
		if receipt.CumulativeComputeUnits > ^uint64(0)-totalCompute {
			return Settlement{}, errors.New("settlement compute overflow")
		}
		totalCompute += receipt.CumulativeComputeUnits
		if receipt.CumulativePaymentUnits > ^uint64(0)-out.TotalCharge {
			return Settlement{}, errors.New("settlement payment overflow")
		}
		out.TotalCharge += receipt.CumulativePaymentUnits
		old := out.Payouts[receipt.ProviderWallet]
		if receipt.CumulativePaymentUnits > ^uint64(0)-old {
			return Settlement{}, errors.New("provider payout overflow")
		}
		out.Payouts[receipt.ProviderWallet] = old + receipt.CumulativePaymentUnits
	}
	if totalCompute > job.MaxComputeUnits {
		return Settlement{}, errors.New("settlement exceeds job compute maximum")
	}
	if out.TotalCharge > job.MaxChargeUnits {
		return Settlement{}, errors.New("settlement exceeds job charge maximum")
	}
	return out, nil
}

// BuildSettlement requires one latest co-signed receipt from every reserved worker.
func BuildSettlement(job JobReservation, latestByPeer map[string]WorkReceipt) (Settlement, error) {
	return buildSettlement(job, latestByPeer, true)
}

// BuildTimeoutSettlement pays every already accepted co-signed receipt and
// releases the unused remainder. Missing providers receive no payment because
// no requester-approved receipt exists for them.
func BuildTimeoutSettlement(job JobReservation, latestByPeer map[string]WorkReceipt) (Settlement, error) {
	return buildSettlement(job, latestByPeer, false)
}
