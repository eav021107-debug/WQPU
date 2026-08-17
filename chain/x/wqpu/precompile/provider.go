package precompile

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"net"
	"net/url"
	"strconv"

	"github.com/ethereum/go-ethereum/common"
)

const (
	providerCodecVersion byte = 1
	MaxProviderEndpoints      = 4
	MaxProviderModels         = 16
	MaxEndpointBytes          = 256
)

type ProviderRecord struct {
	Wallet            common.Address
	PeerID            common.Hash
	Endpoints         []string
	ModelHashes       []common.Hash
	CapacityUnits     uint64
	ReportedBusyUnits uint64
	FreeMemoryBytes   uint64
	CapabilityHash    common.Hash
	HeartbeatHeight   uint64
	ExpiresHeight     uint64
	ProtocolVersion   uint32
}

func validEndpoint(raw string) bool {
	if len(raw) == 0 || len(raw) > MaxEndpointBytes {
		return false
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "wqpu" || u.Host == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return false
	}
	host := u.Hostname()
	port := u.Port()
	if host == "" || port == "" {
		return false
	}
	if ip := net.ParseIP(host); ip != nil && ip.IsUnspecified() {
		return false
	}
	value, err := strconv.ParseUint(port, 10, 16)
	return err == nil && value > 0
}

func (p ProviderRecord) Validate() error {
	if p.Wallet == (common.Address{}) {
		return errors.New("provider wallet is required")
	}
	if p.PeerID == (common.Hash{}) || p.CapabilityHash == (common.Hash{}) {
		return errors.New("provider peer and capability hashes are required")
	}
	if p.ProtocolVersion != uint32(ProtocolVersion) {
		return errors.New("unsupported WQPU provider protocol version")
	}
	if p.CapacityUnits == 0 || p.ReportedBusyUnits > p.CapacityUnits {
		return errors.New("invalid provider capacity/load")
	}
	if p.ExpiresHeight <= p.HeartbeatHeight {
		return errors.New("provider expiry must follow heartbeat")
	}
	if len(p.Endpoints) == 0 || len(p.Endpoints) > MaxProviderEndpoints {
		return errors.New("provider endpoint count is outside protocol bounds")
	}
	seenEndpoints := map[string]struct{}{}
	for _, endpoint := range p.Endpoints {
		if !validEndpoint(endpoint) {
			return fmt.Errorf("invalid WQPU endpoint %q", endpoint)
		}
		if _, exists := seenEndpoints[endpoint]; exists {
			return errors.New("duplicate provider endpoint")
		}
		seenEndpoints[endpoint] = struct{}{}
	}
	if len(p.ModelHashes) == 0 || len(p.ModelHashes) > MaxProviderModels {
		return errors.New("provider model count is outside protocol bounds")
	}
	seenModels := map[common.Hash]struct{}{}
	for _, model := range p.ModelHashes {
		if model == (common.Hash{}) {
			return errors.New("zero model hash is not allowed")
		}
		if _, exists := seenModels[model]; exists {
			return errors.New("duplicate provider model")
		}
		seenModels[model] = struct{}{}
	}
	return nil
}

func appendUint16(dst []byte, value uint16) []byte {
	var raw [2]byte
	binary.BigEndian.PutUint16(raw[:], value)
	return append(dst, raw[:]...)
}

func appendUint32(dst []byte, value uint32) []byte {
	var raw [4]byte
	binary.BigEndian.PutUint32(raw[:], value)
	return append(dst, raw[:]...)
}

func appendUint64(dst []byte, value uint64) []byte {
	var raw [8]byte
	binary.BigEndian.PutUint64(raw[:], value)
	return append(dst, raw[:]...)
}

func EncodeProvider(p ProviderRecord) ([]byte, error) {
	if err := p.Validate(); err != nil {
		return nil, err
	}
	out := make([]byte, 0, 256)
	out = append(out, providerCodecVersion)
	out = append(out, p.Wallet.Bytes()...)
	out = append(out, p.PeerID.Bytes()...)
	out = appendUint32(out, p.ProtocolVersion)
	out = appendUint64(out, p.CapacityUnits)
	out = appendUint64(out, p.ReportedBusyUnits)
	out = appendUint64(out, p.FreeMemoryBytes)
	out = appendUint64(out, p.HeartbeatHeight)
	out = appendUint64(out, p.ExpiresHeight)
	out = append(out, p.CapabilityHash.Bytes()...)
	out = append(out, byte(len(p.Endpoints)))
	for _, endpoint := range p.Endpoints {
		raw := []byte(endpoint)
		out = appendUint16(out, uint16(len(raw)))
		out = append(out, raw...)
	}
	out = append(out, byte(len(p.ModelHashes)))
	for _, model := range p.ModelHashes {
		out = append(out, model.Bytes()...)
	}
	return out, nil
}

type providerReader struct {
	data []byte
	pos  int
}

func (r *providerReader) take(n int) ([]byte, error) {
	if n < 0 || r.pos > len(r.data)-n {
		return nil, errors.New("truncated WQPU provider record")
	}
	out := r.data[r.pos : r.pos+n]
	r.pos += n
	return out, nil
}

func (r *providerReader) u8() (byte, error) {
	raw, err := r.take(1)
	if err != nil {
		return 0, err
	}
	return raw[0], nil
}

func (r *providerReader) u16() (uint16, error) {
	raw, err := r.take(2)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint16(raw), nil
}

func (r *providerReader) u32() (uint32, error) {
	raw, err := r.take(4)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint32(raw), nil
}

func (r *providerReader) u64() (uint64, error) {
	raw, err := r.take(8)
	if err != nil {
		return 0, err
	}
	return binary.BigEndian.Uint64(raw), nil
}

func DecodeProvider(data []byte) (ProviderRecord, error) {
	if len(data) == 0 {
		return ProviderRecord{}, errors.New("empty WQPU provider record")
	}
	r := &providerReader{data: data}
	version, err := r.u8()
	if err != nil || version != providerCodecVersion {
		return ProviderRecord{}, errors.New("unsupported WQPU provider codec")
	}
	walletRaw, err := r.take(common.AddressLength)
	if err != nil {
		return ProviderRecord{}, err
	}
	peerRaw, err := r.take(common.HashLength)
	if err != nil {
		return ProviderRecord{}, err
	}
	protocolVersion, err := r.u32()
	if err != nil {
		return ProviderRecord{}, err
	}
	capacity, err := r.u64()
	if err != nil {
		return ProviderRecord{}, err
	}
	busy, err := r.u64()
	if err != nil {
		return ProviderRecord{}, err
	}
	freeMemory, err := r.u64()
	if err != nil {
		return ProviderRecord{}, err
	}
	heartbeat, err := r.u64()
	if err != nil {
		return ProviderRecord{}, err
	}
	expires, err := r.u64()
	if err != nil {
		return ProviderRecord{}, err
	}
	capRaw, err := r.take(common.HashLength)
	if err != nil {
		return ProviderRecord{}, err
	}
	endpointCount, err := r.u8()
	if err != nil || endpointCount == 0 || int(endpointCount) > MaxProviderEndpoints {
		return ProviderRecord{}, errors.New("invalid encoded endpoint count")
	}
	endpoints := make([]string, 0, endpointCount)
	for i := 0; i < int(endpointCount); i++ {
		length, err := r.u16()
		if err != nil || length == 0 || int(length) > MaxEndpointBytes {
			return ProviderRecord{}, errors.New("invalid encoded endpoint length")
		}
		raw, err := r.take(int(length))
		if err != nil {
			return ProviderRecord{}, err
		}
		endpoints = append(endpoints, string(raw))
	}
	modelCount, err := r.u8()
	if err != nil || modelCount == 0 || int(modelCount) > MaxProviderModels {
		return ProviderRecord{}, errors.New("invalid encoded model count")
	}
	models := make([]common.Hash, 0, modelCount)
	for i := 0; i < int(modelCount); i++ {
		raw, err := r.take(common.HashLength)
		if err != nil {
			return ProviderRecord{}, err
		}
		models = append(models, common.BytesToHash(raw))
	}
	if r.pos != len(r.data) {
		return ProviderRecord{}, errors.New("trailing bytes in WQPU provider record")
	}
	out := ProviderRecord{
		Wallet:            common.BytesToAddress(walletRaw),
		PeerID:            common.BytesToHash(peerRaw),
		Endpoints:         endpoints,
		ModelHashes:       models,
		CapacityUnits:     capacity,
		ReportedBusyUnits: busy,
		FreeMemoryBytes:   freeMemory,
		CapabilityHash:    common.BytesToHash(capRaw),
		HeartbeatHeight:   heartbeat,
		ExpiresHeight:     expires,
		ProtocolVersion:   protocolVersion,
	}
	if err := out.Validate(); err != nil {
		return ProviderRecord{}, err
	}
	return out, nil
}

func providerKey(wallet common.Address) []byte { return wallet.Bytes() }

func StoreProvider(state WordState, provider ProviderRecord) error {
	encoded, err := EncodeProvider(provider)
	if err != nil {
		return err
	}
	if err := WriteBlob(state, "provider-record", providerKey(provider.Wallet), encoded); err != nil {
		return err
	}
	_, _, err = AddIndexedAddress(state, "providers", provider.Wallet)
	return err
}

func LoadProvider(state WordState, wallet common.Address) (ProviderRecord, bool, error) {
	if state == nil || wallet == (common.Address{}) {
		return ProviderRecord{}, false, errors.New("valid state and provider wallet required")
	}
	encoded, err := ReadBlob(state, "provider-record", providerKey(wallet))
	if err != nil {
		return ProviderRecord{}, false, err
	}
	if len(encoded) == 0 {
		return ProviderRecord{}, false, nil
	}
	provider, err := DecodeProvider(encoded)
	if err != nil {
		return ProviderRecord{}, false, err
	}
	if provider.Wallet != wallet {
		return ProviderRecord{}, false, errors.New("provider record stored under wrong wallet")
	}
	return provider, true, nil
}

func DeleteProvider(state WordState, wallet common.Address) error {
	if state == nil || wallet == (common.Address{}) {
		return errors.New("valid state and provider wallet required")
	}
	if err := DeleteBlob(state, "provider-record", providerKey(wallet)); err != nil {
		return err
	}
	_, err := RemoveIndexedAddress(state, "providers", wallet)
	return err
}

func (p ProviderRecord) ActiveAt(height uint64) bool {
	return p.HeartbeatHeight <= height && height < p.ExpiresHeight
}

// EqualProvider is intentionally strict and useful for codec/state tests.
func EqualProvider(a, b ProviderRecord) bool {
	if a.Wallet != b.Wallet || a.PeerID != b.PeerID || a.CapacityUnits != b.CapacityUnits ||
		a.ReportedBusyUnits != b.ReportedBusyUnits || a.FreeMemoryBytes != b.FreeMemoryBytes ||
		a.CapabilityHash != b.CapabilityHash || a.HeartbeatHeight != b.HeartbeatHeight ||
		a.ExpiresHeight != b.ExpiresHeight || a.ProtocolVersion != b.ProtocolVersion {
		return false
	}
	if len(a.Endpoints) != len(b.Endpoints) || len(a.ModelHashes) != len(b.ModelHashes) {
		return false
	}
	for i := range a.Endpoints {
		if a.Endpoints[i] != b.Endpoints[i] {
			return false
		}
	}
	return bytes.Equal(flattenHashes(a.ModelHashes), flattenHashes(b.ModelHashes))
}

func flattenHashes(values []common.Hash) []byte {
	out := make([]byte, 0, len(values)*common.HashLength)
	for _, value := range values {
		out = append(out, value.Bytes()...)
	}
	return out
}
