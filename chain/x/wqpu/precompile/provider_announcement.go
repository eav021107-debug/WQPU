package precompile

import (
	"encoding/binary"
	"errors"

	"github.com/ethereum/go-ethereum/common"
)

const (
	providerAnnouncementCodec byte = 1
	ProviderTTLBlocks uint64 = 20
)

type ProviderAnnouncement struct {
	Wallet            common.Address
	PeerID            common.Hash
	Endpoints         []string
	ModelHashes       []common.Hash
	CapacityUnits     uint64
	ReportedBusyUnits uint64
	FreeMemoryBytes   uint64
	CapabilityHash    common.Hash
	ProtocolVersion   uint32
}

func (a ProviderAnnouncement) Validate() error {
	probe := ProviderRecord{
		Wallet: a.Wallet,
		PeerID: a.PeerID,
		Endpoints: a.Endpoints,
		ModelHashes: a.ModelHashes,
		CapacityUnits: a.CapacityUnits,
		ReportedBusyUnits: a.ReportedBusyUnits,
		FreeMemoryBytes: a.FreeMemoryBytes,
		CapabilityHash: a.CapabilityHash,
		HeartbeatHeight: 0,
		ExpiresHeight: 1,
		ProtocolVersion: a.ProtocolVersion,
	}
	return probe.Validate()
}

func (a ProviderAnnouncement) ToRecord(height uint64) (ProviderRecord, error) {
	if err := a.Validate(); err != nil {
		return ProviderRecord{}, err
	}
	if height > ^uint64(0)-ProviderTTLBlocks {
		return ProviderRecord{}, errors.New("WQPU provider expiry height overflow")
	}
	return ProviderRecord{
		Wallet: a.Wallet,
		PeerID: a.PeerID,
		Endpoints: append([]string(nil), a.Endpoints...),
		ModelHashes: append([]common.Hash(nil), a.ModelHashes...),
		CapacityUnits: a.CapacityUnits,
		ReportedBusyUnits: a.ReportedBusyUnits,
		FreeMemoryBytes: a.FreeMemoryBytes,
		CapabilityHash: a.CapabilityHash,
		HeartbeatHeight: height,
		ExpiresHeight: height + ProviderTTLBlocks,
		ProtocolVersion: a.ProtocolVersion,
	}, nil
}

func EncodeProviderAnnouncement(a ProviderAnnouncement) ([]byte, error) {
	if err := a.Validate(); err != nil {
		return nil, err
	}
	out := []byte{providerAnnouncementCodec}
	out = append(out, a.Wallet.Bytes()...)
	out = append(out, a.PeerID.Bytes()...)
	out = appendUint32(out, a.ProtocolVersion)
	out = appendUint64(out, a.CapacityUnits)
	out = appendUint64(out, a.ReportedBusyUnits)
	out = appendUint64(out, a.FreeMemoryBytes)
	out = append(out, a.CapabilityHash.Bytes()...)
	out = append(out, byte(len(a.Endpoints)))
	for _, endpoint := range a.Endpoints {
		raw := []byte(endpoint)
		out = appendUint16(out, uint16(len(raw)))
		out = append(out, raw...)
	}
	out = append(out, byte(len(a.ModelHashes)))
	for _, model := range a.ModelHashes {
		out = append(out, model.Bytes()...)
	}
	return out, nil
}

func DecodeProviderAnnouncement(data []byte) (ProviderAnnouncement, error) {
	if len(data) == 0 || data[0] != providerAnnouncementCodec {
		return ProviderAnnouncement{}, errors.New("unsupported WQPU provider announcement codec")
	}
	pos := 1
	take := func(n int) ([]byte, error) {
		if n < 0 || pos > len(data)-n {
			return nil, errors.New("truncated WQPU provider announcement")
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
	walletRaw, err := take(common.AddressLength); if err != nil { return ProviderAnnouncement{}, err }
	peerRaw, err := take(common.HashLength); if err != nil { return ProviderAnnouncement{}, err }
	protocolRaw, err := take(4); if err != nil { return ProviderAnnouncement{}, err }
	capacity, err := read64(); if err != nil { return ProviderAnnouncement{}, err }
	busy, err := read64(); if err != nil { return ProviderAnnouncement{}, err }
	freeMemory, err := read64(); if err != nil { return ProviderAnnouncement{}, err }
	capRaw, err := take(common.HashLength); if err != nil { return ProviderAnnouncement{}, err }
	endpointCountRaw, err := take(1); if err != nil { return ProviderAnnouncement{}, err }
	endpointCount := int(endpointCountRaw[0])
	if endpointCount == 0 || endpointCount > MaxProviderEndpoints {
		return ProviderAnnouncement{}, errors.New("invalid WQPU announcement endpoint count")
	}
	endpoints := make([]string, 0, endpointCount)
	for i := 0; i < endpointCount; i++ {
		lengthRaw, err := take(2); if err != nil { return ProviderAnnouncement{}, err }
		length := int(binary.BigEndian.Uint16(lengthRaw))
		if length == 0 || length > MaxEndpointBytes {
			return ProviderAnnouncement{}, errors.New("invalid WQPU announcement endpoint length")
		}
		raw, err := take(length); if err != nil { return ProviderAnnouncement{}, err }
		endpoints = append(endpoints, string(raw))
	}
	modelCountRaw, err := take(1); if err != nil { return ProviderAnnouncement{}, err }
	modelCount := int(modelCountRaw[0])
	if modelCount == 0 || modelCount > MaxProviderModels {
		return ProviderAnnouncement{}, errors.New("invalid WQPU announcement model count")
	}
	models := make([]common.Hash, 0, modelCount)
	for i := 0; i < modelCount; i++ {
		raw, err := take(common.HashLength); if err != nil { return ProviderAnnouncement{}, err }
		models = append(models, common.BytesToHash(raw))
	}
	if pos != len(data) {
		return ProviderAnnouncement{}, errors.New("trailing bytes in WQPU provider announcement")
	}
	out := ProviderAnnouncement{
		Wallet: common.BytesToAddress(walletRaw), PeerID: common.BytesToHash(peerRaw), Endpoints: endpoints,
		ModelHashes: models, CapacityUnits: capacity, ReportedBusyUnits: busy, FreeMemoryBytes: freeMemory,
		CapabilityHash: common.BytesToHash(capRaw), ProtocolVersion: binary.BigEndian.Uint32(protocolRaw),
	}
	if err := out.Validate(); err != nil {
		return ProviderAnnouncement{}, err
	}
	return out, nil
}
