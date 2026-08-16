package precompile

import (
	"encoding/binary"
	"errors"
	"math/big"

	"github.com/ethereum/go-ethereum/common"
)

const (
	jobCodecVersion byte = 1
	MaxJobProviders      = 8
	JobTTLBlocks  uint64 = 10
	ModelHeadroomBPS uint64 = 1200
	BasisPoints uint64 = 10_000
)

type JobProviderReservation struct {
	ProviderWallet       common.Address
	ProviderPeerID       common.Hash
	ReservedComputeUnits uint64
	AssignedModelBytes   uint64
}

type JobRequest struct {
	JobID                common.Hash
	RequesterWallet      common.Address
	ModelHash             common.Hash
	PromptCommitment      common.Hash
	PriceEpoch            uint64
	PricePerMillionUnits  uint64
	MaxComputeUnits       uint64
	MaxChargeUnits        uint64
	ModelBytes            uint64
	Providers             []JobProviderReservation
	ProtocolVersion       uint32
}

type JobReservation struct {
	Request          JobRequest
	RequesterSession common.Address
	CreatedHeight    uint64
	ExpiresHeight    uint64
}

func ChargeForUnits(pricePerMillion, computeUnits uint64) (uint64, error) {
	if pricePerMillion == 0 || computeUnits == 0 {
		return 0, errors.New("WQPU price and compute units must be positive")
	}
	product := new(big.Int).Mul(new(big.Int).SetUint64(pricePerMillion), new(big.Int).SetUint64(computeUnits))
	product.Add(product, big.NewInt(999_999))
	product.Div(product, big.NewInt(1_000_000))
	if !product.IsUint64() {
		return 0, errors.New("WQPU charge overflow")
	}
	return product.Uint64(), nil
}

func (r JobRequest) Validate() error {
	if r.JobID == (common.Hash{}) || r.RequesterWallet == (common.Address{}) || r.ModelHash == (common.Hash{}) || r.PromptCommitment == (common.Hash{}) {
		return errors.New("WQPU job identity/model/prompt commitment are required")
	}
	if r.ProtocolVersion != uint32(ProtocolVersion) {
		return errors.New("unsupported WQPU job protocol version")
	}
	if r.PricePerMillionUnits == 0 || r.MaxComputeUnits == 0 || r.MaxChargeUnits == 0 || r.ModelBytes == 0 {
		return errors.New("WQPU job price/compute/charge/model size must be positive")
	}
	if len(r.Providers) == 0 || len(r.Providers) > MaxJobProviders {
		return errors.New("WQPU job provider count is outside protocol bounds")
	}
	expectedCharge, err := ChargeForUnits(r.PricePerMillionUnits, r.MaxComputeUnits)
	if err != nil {
		return err
	}
	if r.MaxChargeUnits != expectedCharge {
		return errors.New("WQPU job max charge must exactly match global price")
	}
	var totalCompute, totalModel uint64
	seen := map[common.Hash]struct{}{}
	for _, provider := range r.Providers {
		if provider.ProviderWallet == (common.Address{}) || provider.ProviderPeerID == (common.Hash{}) {
			return errors.New("WQPU job provider identity is required")
		}
		if provider.ReservedComputeUnits == 0 || provider.AssignedModelBytes == 0 {
			return errors.New("WQPU job provider reservation must be positive")
		}
		if _, exists := seen[provider.ProviderPeerID]; exists {
			return errors.New("duplicate WQPU provider in job")
		}
		seen[provider.ProviderPeerID] = struct{}{}
		if provider.ReservedComputeUnits > ^uint64(0)-totalCompute || provider.AssignedModelBytes > ^uint64(0)-totalModel {
			return errors.New("WQPU job reservation overflow")
		}
		totalCompute += provider.ReservedComputeUnits
		totalModel += provider.AssignedModelBytes
	}
	if totalCompute != r.MaxComputeUnits {
		return errors.New("WQPU provider compute reservations must equal job maximum")
	}
	if totalModel != r.ModelBytes {
		return errors.New("WQPU model bytes must be fully assigned across providers")
	}
	return nil
}

func CurrentPriceState(state WordState) (uint64, uint64, error) {
	epoch, err := GetUint64(state, "global", []byte("price-epoch"))
	if err != nil {
		return 0, 0, err
	}
	price, err := currentGlobalPrice(state)
	if err != nil {
		return 0, 0, err
	}
	return epoch, price, nil
}

func ReservedPeerUnits(state WordState, peerID common.Hash) (uint64, error) {
	if peerID == (common.Hash{}) {
		return 0, errors.New("WQPU peer id is required")
	}
	return GetUint64(state, "peer-reserved-compute", peerID.Bytes())
}

func setReservedPeerUnits(state WordState, peerID common.Hash, units uint64) error {
	return SetUint64(state, "peer-reserved-compute", peerID.Bytes(), units)
}

func containsModelHash(values []common.Hash, wanted common.Hash) bool {
	for _, value := range values {
		if value == wanted {
			return true
		}
	}
	return false
}

func usableProviderMemory(provider ProviderRecord) uint64 {
	headroom := new(big.Int).Mul(new(big.Int).SetUint64(provider.FreeMemoryBytes), new(big.Int).SetUint64(ModelHeadroomBPS))
	headroom.Div(headroom, new(big.Int).SetUint64(BasisPoints))
	if !headroom.IsUint64() || headroom.Uint64() >= provider.FreeMemoryBytes {
		return 0
	}
	return provider.FreeMemoryBytes - headroom.Uint64()
}

func EncodeJobReservation(job JobReservation) ([]byte, error) {
	if err := job.Request.Validate(); err != nil {
		return nil, err
	}
	if job.RequesterSession == (common.Address{}) || job.ExpiresHeight <= job.CreatedHeight {
		return nil, errors.New("WQPU job session and lifetime are required")
	}
	r := job.Request
	out := []byte{jobCodecVersion}
	out = append(out, r.JobID.Bytes()...)
	out = append(out, r.RequesterWallet.Bytes()...)
	out = append(out, job.RequesterSession.Bytes()...)
	out = append(out, r.ModelHash.Bytes()...)
	out = append(out, r.PromptCommitment.Bytes()...)
	out = appendUint64(out, r.PriceEpoch)
	out = appendUint64(out, r.PricePerMillionUnits)
	out = appendUint64(out, r.MaxComputeUnits)
	out = appendUint64(out, r.MaxChargeUnits)
	out = appendUint64(out, r.ModelBytes)
	out = appendUint64(out, job.CreatedHeight)
	out = appendUint64(out, job.ExpiresHeight)
	out = appendUint32(out, r.ProtocolVersion)
	out = append(out, byte(len(r.Providers)))
	for _, provider := range r.Providers {
		out = append(out, provider.ProviderWallet.Bytes()...)
		out = append(out, provider.ProviderPeerID.Bytes()...)
		out = appendUint64(out, provider.ReservedComputeUnits)
		out = appendUint64(out, provider.AssignedModelBytes)
	}
	return out, nil
}

func DecodeJobReservation(data []byte) (JobReservation, error) {
	if len(data) == 0 || data[0] != jobCodecVersion {
		return JobReservation{}, errors.New("unsupported WQPU job codec")
	}
	pos := 1
	take := func(n int) ([]byte, error) {
		if n < 0 || pos > len(data)-n {
			return nil, errors.New("truncated WQPU job reservation")
		}
		out := data[pos : pos+n]
		pos += n
		return out, nil
	}
	read64 := func() (uint64, error) {
		raw, err := take(8)
		if err != nil { return 0, err }
		return binary.BigEndian.Uint64(raw), nil
	}
	jobRaw, err := take(32); if err != nil { return JobReservation{}, err }
	walletRaw, err := take(20); if err != nil { return JobReservation{}, err }
	sessionRaw, err := take(20); if err != nil { return JobReservation{}, err }
	modelRaw, err := take(32); if err != nil { return JobReservation{}, err }
	promptRaw, err := take(32); if err != nil { return JobReservation{}, err }
	epoch, err := read64(); if err != nil { return JobReservation{}, err }
	price, err := read64(); if err != nil { return JobReservation{}, err }
	maxCompute, err := read64(); if err != nil { return JobReservation{}, err }
	maxCharge, err := read64(); if err != nil { return JobReservation{}, err }
	modelBytes, err := read64(); if err != nil { return JobReservation{}, err }
	created, err := read64(); if err != nil { return JobReservation{}, err }
	expires, err := read64(); if err != nil { return JobReservation{}, err }
	protocolRaw, err := take(4); if err != nil { return JobReservation{}, err }
	countRaw, err := take(1); if err != nil { return JobReservation{}, err }
	count := int(countRaw[0])
	if count == 0 || count > MaxJobProviders {
		return JobReservation{}, errors.New("invalid WQPU job provider count")
	}
	providers := make([]JobProviderReservation, 0, count)
	for i := 0; i < count; i++ {
		providerWalletRaw, err := take(20); if err != nil { return JobReservation{}, err }
		peerRaw, err := take(32); if err != nil { return JobReservation{}, err }
		compute, err := read64(); if err != nil { return JobReservation{}, err }
		assigned, err := read64(); if err != nil { return JobReservation{}, err }
		providers = append(providers, JobProviderReservation{
			ProviderWallet: common.BytesToAddress(providerWalletRaw), ProviderPeerID: common.BytesToHash(peerRaw),
			ReservedComputeUnits: compute, AssignedModelBytes: assigned,
		})
	}
	if pos != len(data) {
		return JobReservation{}, errors.New("trailing bytes in WQPU job reservation")
	}
	out := JobReservation{
		Request: JobRequest{
			JobID: common.BytesToHash(jobRaw), RequesterWallet: common.BytesToAddress(walletRaw), ModelHash: common.BytesToHash(modelRaw),
			PromptCommitment: common.BytesToHash(promptRaw), PriceEpoch: epoch, PricePerMillionUnits: price, MaxComputeUnits: maxCompute,
			MaxChargeUnits: maxCharge, ModelBytes: modelBytes, Providers: providers, ProtocolVersion: binary.BigEndian.Uint32(protocolRaw),
		},
		RequesterSession: common.BytesToAddress(sessionRaw), CreatedHeight: created, ExpiresHeight: expires,
	}
	if _, err := EncodeJobReservation(out); err != nil {
		return JobReservation{}, err
	}
	return out, nil
}

func StoreJob(state WordState, job JobReservation) error {
	encoded, err := EncodeJobReservation(job)
	if err != nil { return err }
	if err := WriteBlob(state, "job", job.Request.JobID.Bytes(), encoded); err != nil { return err }
	_, _, err = AddIndexedHash(state, "active-jobs", job.Request.JobID)
	return err
}

func LoadJob(state WordState, jobID common.Hash) (JobReservation, bool, error) {
	if state == nil || jobID == (common.Hash{}) {
		return JobReservation{}, false, errors.New("valid state and WQPU job id required")
	}
	encoded, err := ReadBlob(state, "job", jobID.Bytes())
	if err != nil { return JobReservation{}, false, err }
	if len(encoded) == 0 { return JobReservation{}, false, nil }
	job, err := DecodeJobReservation(encoded)
	if err != nil { return JobReservation{}, false, err }
	if job.Request.JobID != jobID { return JobReservation{}, false, errors.New("WQPU job stored under wrong id") }
	return job, true, nil
}

func CommitJobReservation(state WordState, request JobRequest, requesterSession common.Address, height uint64) (JobReservation, error) {
	if state == nil { return JobReservation{}, errors.New("nil WQPU state") }
	if err := request.Validate(); err != nil { return JobReservation{}, err }
	if requesterSession == (common.Address{}) { return JobReservation{}, errors.New("requester WQPU session is required") }
	if _, exists, err := LoadJob(state, request.JobID); err != nil { return JobReservation{}, err } else if exists { return JobReservation{}, errors.New("WQPU job id already exists") }
	epoch, price, err := CurrentPriceState(state)
	if err != nil { return JobReservation{}, err }
	if request.PriceEpoch != epoch || request.PricePerMillionUnits != price {
		return JobReservation{}, errors.New("WQPU job uses stale global price")
	}
	if _, err := SessionCanReserveSpend(state, request.RequesterWallet, requesterSession, height, request.MaxChargeUnits); err != nil {
		return JobReservation{}, err
	}
	if height > ^uint64(0)-JobTTLBlocks { return JobReservation{}, errors.New("WQPU job expiry overflow") }
	expires := height + JobTTLBlocks

	newReserved := make(map[common.Hash]uint64, len(request.Providers))
	for _, reservation := range request.Providers {
		provider, exists, err := LoadPeerProvider(state, reservation.ProviderPeerID)
		if err != nil { return JobReservation{}, err }
		if !exists || !provider.ActiveAt(height) { return JobReservation{}, errors.New("WQPU job selected unknown or inactive peer") }
		if provider.Wallet != reservation.ProviderWallet { return JobReservation{}, errors.New("WQPU job provider wallet/peer mismatch") }
		if provider.ExpiresHeight < expires { return JobReservation{}, errors.New("WQPU provider expires before job") }
		if !containsModelHash(provider.ModelHashes, request.ModelHash) { return JobReservation{}, errors.New("WQPU provider does not advertise requested model") }
		if reservation.AssignedModelBytes > usableProviderMemory(provider) { return JobReservation{}, errors.New("WQPU provider lacks advertised free memory") }
		current, err := ReservedPeerUnits(state, provider.PeerID)
		if err != nil { return JobReservation{}, err }
		if current > provider.CapacityUnits || reservation.ReservedComputeUnits > provider.CapacityUnits-current {
			return JobReservation{}, errors.New("WQPU provider capacity is already reserved")
		}
		newReserved[provider.PeerID] = current + reservation.ReservedComputeUnits
	}

	job := JobReservation{Request: request, RequesterSession: requesterSession, CreatedHeight: height, ExpiresHeight: expires}
	if _, err := EncodeJobReservation(job); err != nil { return JobReservation{}, err }
	if err := ReserveSessionSpend(state, request.RequesterWallet, requesterSession, height, request.MaxChargeUnits); err != nil { return JobReservation{}, err }
	for peerID, units := range newReserved {
		if err := setReservedPeerUnits(state, peerID, units); err != nil { return JobReservation{}, err }
	}
	if err := StoreJob(state, job); err != nil { return JobReservation{}, err }
	return job, nil
}
