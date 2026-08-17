package precompile

import (
	"encoding/binary"
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

const jobRequestCodecVersion byte = 1

func EncodeJobRequest(request JobRequest) ([]byte, error) {
	if err := request.Validate(); err != nil { return nil, err }
	out := []byte{jobRequestCodecVersion}
	out = append(out, request.JobID.Bytes()...)
	out = append(out, request.RequesterWallet.Bytes()...)
	out = append(out, request.ModelHash.Bytes()...)
	out = append(out, request.PromptCommitment.Bytes()...)
	out = appendUint64(out, request.PriceEpoch)
	out = appendUint64(out, request.PricePerMillionUnits)
	out = appendUint64(out, request.MaxComputeUnits)
	out = appendUint64(out, request.MaxChargeUnits)
	out = appendUint64(out, request.ModelBytes)
	out = appendUint32(out, request.ProtocolVersion)
	out = append(out, byte(len(request.Providers)))
	for _, provider := range request.Providers {
		out = append(out, provider.ProviderWallet.Bytes()...)
		out = append(out, provider.ProviderPeerID.Bytes()...)
		out = appendUint64(out, provider.ReservedComputeUnits)
		out = appendUint64(out, provider.AssignedModelBytes)
	}
	return out, nil
}

func DecodeJobRequest(data []byte) (JobRequest, error) {
	if len(data) == 0 || data[0] != jobRequestCodecVersion {
		return JobRequest{}, errors.New("unsupported WQPU job request codec")
	}
	pos := 1
	take := func(n int) ([]byte, error) {
		if n < 0 || pos > len(data)-n { return nil, errors.New("truncated WQPU job request") }
		out := data[pos:pos+n]; pos += n; return out, nil
	}
	read64 := func() (uint64, error) { raw, err := take(8); if err != nil { return 0, err }; return binary.BigEndian.Uint64(raw), nil }
	jobRaw, err := take(32); if err != nil { return JobRequest{}, err }
	walletRaw, err := take(20); if err != nil { return JobRequest{}, err }
	modelRaw, err := take(32); if err != nil { return JobRequest{}, err }
	promptRaw, err := take(32); if err != nil { return JobRequest{}, err }
	epoch, err := read64(); if err != nil { return JobRequest{}, err }
	price, err := read64(); if err != nil { return JobRequest{}, err }
	compute, err := read64(); if err != nil { return JobRequest{}, err }
	charge, err := read64(); if err != nil { return JobRequest{}, err }
	modelBytes, err := read64(); if err != nil { return JobRequest{}, err }
	protocolRaw, err := take(4); if err != nil { return JobRequest{}, err }
	countRaw, err := take(1); if err != nil { return JobRequest{}, err }
	count := int(countRaw[0])
	if count == 0 || count > MaxJobProviders { return JobRequest{}, errors.New("invalid WQPU job request provider count") }
	providers := make([]JobProviderReservation, 0, count)
	for i := 0; i < count; i++ {
		providerWalletRaw, err := take(20); if err != nil { return JobRequest{}, err }
		peerRaw, err := take(32); if err != nil { return JobRequest{}, err }
		reserved, err := read64(); if err != nil { return JobRequest{}, err }
		assigned, err := read64(); if err != nil { return JobRequest{}, err }
		providers = append(providers, JobProviderReservation{ProviderWallet: common.BytesToAddress(providerWalletRaw), ProviderPeerID: common.BytesToHash(peerRaw), ReservedComputeUnits: reserved, AssignedModelBytes: assigned})
	}
	if pos != len(data) { return JobRequest{}, errors.New("trailing bytes in WQPU job request") }
	request := JobRequest{
		JobID: common.BytesToHash(jobRaw), RequesterWallet: common.BytesToAddress(walletRaw), ModelHash: common.BytesToHash(modelRaw), PromptCommitment: common.BytesToHash(promptRaw),
		PriceEpoch: epoch, PricePerMillionUnits: price, MaxComputeUnits: compute, MaxChargeUnits: charge, ModelBytes: modelBytes,
		Providers: providers, ProtocolVersion: binary.BigEndian.Uint32(protocolRaw),
	}
	if err := request.Validate(); err != nil { return JobRequest{}, err }
	return request, nil
}
