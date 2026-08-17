package chainregistry

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"math/big"
	"net"
	"net/url"
	"strconv"
	"strings"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
)

const (
	ProtocolVersion        = 1
	MaxProviderRecordBytes = 16 * 1024
	MaxProviderEndpoints   = 4
	MaxProviderModels      = 16
	MaxEndpointBytes       = 256
)

var PrecompileAddress = common.HexToAddress("0x0000000000000000000000000000000000000900")

const registryABIJSON = `[
  {"type":"function","name":"providerActive","stateMutability":"view","inputs":[{"name":"peerId","type":"bytes32"}],"outputs":[{"type":"bool"}]},
  {"type":"function","name":"providerRecord","stateMutability":"view","inputs":[{"name":"peerId","type":"bytes32"}],"outputs":[{"type":"bytes"}]},
  {"type":"function","name":"peerControlSession","stateMutability":"view","inputs":[{"name":"peerId","type":"bytes32"}],"outputs":[{"type":"address"}]}
]`

type Caller interface {
	CallContract(context.Context, ethereum.CallMsg, *big.Int) ([]byte, error)
}

type Provider struct {
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

type Peer struct {
	Provider       Provider
	ControlSession common.Address
}

type Registry struct {
	caller Caller
	abi    abi.ABI
}

func New(caller Caller) (*Registry, error) {
	if caller == nil {
		return nil, errors.New("WQPU chain registry caller is required")
	}
	parsed, err := abi.JSON(strings.NewReader(registryABIJSON))
	if err != nil {
		return nil, err
	}
	return &Registry{caller: caller, abi: parsed}, nil
}

func (r *Registry) call(ctx context.Context, method string, args ...any) ([]any, error) {
	if r == nil || r.caller == nil {
		return nil, errors.New("WQPU chain registry is unavailable")
	}
	input, err := r.abi.Pack(method, args...)
	if err != nil {
		return nil, err
	}
	out, err := r.caller.CallContract(ctx, ethereum.CallMsg{To: &PrecompileAddress, Data: input}, nil)
	if err != nil {
		return nil, fmt.Errorf("WQPU registry %s: %w", method, err)
	}
	values, err := r.abi.Unpack(method, out)
	if err != nil {
		return nil, fmt.Errorf("decode WQPU registry %s: %w", method, err)
	}
	return values, nil
}

func (r *Registry) ResolvePeer(ctx context.Context, peerID common.Hash) (Peer, error) {
	if peerID == (common.Hash{}) {
		return Peer{}, errors.New("WQPU peer id is required")
	}
	activeValues, err := r.call(ctx, "providerActive", peerID)
	if err != nil {
		return Peer{}, err
	}
	if len(activeValues) != 1 {
		return Peer{}, errors.New("invalid providerActive output")
	}
	active, ok := activeValues[0].(bool)
	if !ok || !active {
		return Peer{}, errors.New("WQPU peer is unknown or inactive")
	}

	recordValues, err := r.call(ctx, "providerRecord", peerID)
	if err != nil {
		return Peer{}, err
	}
	if len(recordValues) != 1 {
		return Peer{}, errors.New("invalid providerRecord output")
	}
	raw, ok := recordValues[0].([]byte)
	if !ok {
		return Peer{}, errors.New("invalid providerRecord bytes")
	}
	provider, err := DecodeProviderRecord(raw)
	if err != nil {
		return Peer{}, err
	}
	if provider.PeerID != peerID {
		return Peer{}, errors.New("provider record returned for another peer")
	}

	controlValues, err := r.call(ctx, "peerControlSession", peerID)
	if err != nil {
		return Peer{}, err
	}
	if len(controlValues) != 1 {
		return Peer{}, errors.New("invalid peerControlSession output")
	}
	control, ok := controlValues[0].(common.Address)
	if !ok || control == (common.Address{}) {
		return Peer{}, errors.New("WQPU peer has no valid control session")
	}
	return Peer{Provider: provider, ControlSession: control}, nil
}

// DecodeProviderRecord mirrors chain/x/wqpu/precompile.EncodeProvider exactly.
// Wire format v1:
// version | wallet | peer_id | protocol | capacity | busy | free_memory |
// heartbeat | expiry | capability | u8 endpoint_count | endpoints |
// u8 model_count | model_hashes.
func DecodeProviderRecord(data []byte) (Provider, error) {
	if len(data) == 0 || len(data) > MaxProviderRecordBytes {
		return Provider{}, errors.New("invalid WQPU provider record length")
	}
	pos := 0
	take := func(n int) ([]byte, error) {
		if n < 0 || pos > len(data)-n {
			return nil, errors.New("truncated WQPU provider record")
		}
		out := data[pos : pos+n]
		pos += n
		return out, nil
	}
	readU8 := func() (byte, error) {
		raw, err := take(1)
		if err != nil { return 0, err }
		return raw[0], nil
	}
	readU16 := func() (uint16, error) {
		raw, err := take(2)
		if err != nil { return 0, err }
		return binary.BigEndian.Uint16(raw), nil
	}
	readU32 := func() (uint32, error) {
		raw, err := take(4)
		if err != nil { return 0, err }
		return binary.BigEndian.Uint32(raw), nil
	}
	readU64 := func() (uint64, error) {
		raw, err := take(8)
		if err != nil { return 0, err }
		return binary.BigEndian.Uint64(raw), nil
	}

	version, err := readU8()
	if err != nil { return Provider{}, err }
	if version != 1 {
		return Provider{}, errors.New("unsupported WQPU provider record codec")
	}
	walletRaw, err := take(20)
	if err != nil { return Provider{}, err }
	peerRaw, err := take(32)
	if err != nil { return Provider{}, err }
	provider := Provider{Wallet: common.BytesToAddress(walletRaw), PeerID: common.BytesToHash(peerRaw)}
	if provider.Wallet == (common.Address{}) || provider.PeerID == (common.Hash{}) {
		return Provider{}, errors.New("invalid WQPU provider identity")
	}

	provider.ProtocolVersion, err = readU32()
	if err != nil { return Provider{}, err }
	provider.CapacityUnits, err = readU64()
	if err != nil { return Provider{}, err }
	provider.ReportedBusyUnits, err = readU64()
	if err != nil { return Provider{}, err }
	provider.FreeMemoryBytes, err = readU64()
	if err != nil { return Provider{}, err }
	provider.HeartbeatHeight, err = readU64()
	if err != nil { return Provider{}, err }
	provider.ExpiresHeight, err = readU64()
	if err != nil { return Provider{}, err }
	capRaw, err := take(32)
	if err != nil { return Provider{}, err }
	provider.CapabilityHash = common.BytesToHash(capRaw)

	endpointCount, err := readU8()
	if err != nil { return Provider{}, err }
	if endpointCount == 0 || int(endpointCount) > MaxProviderEndpoints {
		return Provider{}, errors.New("invalid WQPU provider endpoint count")
	}
	provider.Endpoints = make([]string, 0, endpointCount)
	seenEndpoints := make(map[string]struct{}, endpointCount)
	for i := 0; i < int(endpointCount); i++ {
		length, err := readU16()
		if err != nil { return Provider{}, err }
		if length == 0 || int(length) > MaxEndpointBytes {
			return Provider{}, errors.New("invalid WQPU provider endpoint length")
		}
		raw, err := take(int(length))
		if err != nil { return Provider{}, err }
		endpoint := string(raw)
		if err := validateEndpoint(endpoint); err != nil { return Provider{}, err }
		if _, exists := seenEndpoints[endpoint]; exists {
			return Provider{}, errors.New("duplicate WQPU provider endpoint")
		}
		seenEndpoints[endpoint] = struct{}{}
		provider.Endpoints = append(provider.Endpoints, endpoint)
	}

	modelCount, err := readU8()
	if err != nil { return Provider{}, err }
	if modelCount == 0 || int(modelCount) > MaxProviderModels {
		return Provider{}, errors.New("invalid WQPU provider model count")
	}
	provider.ModelHashes = make([]common.Hash, 0, modelCount)
	seenModels := make(map[common.Hash]struct{}, modelCount)
	for i := 0; i < int(modelCount); i++ {
		raw, err := take(32)
		if err != nil { return Provider{}, err }
		model := common.BytesToHash(raw)
		if model == (common.Hash{}) {
			return Provider{}, errors.New("zero WQPU provider model hash")
		}
		if _, exists := seenModels[model]; exists {
			return Provider{}, errors.New("duplicate WQPU provider model hash")
		}
		seenModels[model] = struct{}{}
		provider.ModelHashes = append(provider.ModelHashes, model)
	}
	if pos != len(data) {
		return Provider{}, errors.New("trailing WQPU provider record bytes")
	}
	if provider.ProtocolVersion != ProtocolVersion {
		return Provider{}, errors.New("unsupported WQPU provider protocol version")
	}
	if provider.CapacityUnits == 0 || provider.ReportedBusyUnits > provider.CapacityUnits || provider.CapabilityHash == (common.Hash{}) {
		return Provider{}, errors.New("invalid WQPU provider resource record")
	}
	if provider.ExpiresHeight <= provider.HeartbeatHeight {
		return Provider{}, errors.New("invalid WQPU provider expiry")
	}
	return provider, nil
}

func validateEndpoint(endpoint string) error {
	if len(endpoint) == 0 || len(endpoint) > MaxEndpointBytes {
		return errors.New("invalid WQPU endpoint length")
	}
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "wqpu" || u.User != nil || u.Hostname() == "" || u.Port() == "" || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("invalid WQPU endpoint")
	}
	if ip := net.ParseIP(u.Hostname()); ip == nil && strings.ContainsAny(u.Hostname(), " /\\") {
		return errors.New("invalid WQPU endpoint host")
	}
	port, err := strconv.ParseUint(u.Port(), 10, 16)
	if err != nil || port == 0 {
		return errors.New("invalid WQPU endpoint port")
	}
	return nil
}
