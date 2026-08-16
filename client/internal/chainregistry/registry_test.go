package chainregistry

import (
	"context"
	"encoding/binary"
	"errors"
	"math/big"
	"testing"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
)

type mockCaller struct {
	active bool
	record []byte
	control common.Address
	err error
}

func (m mockCaller) CallContract(_ context.Context, msg ethereum.CallMsg, _ *big.Int) ([]byte, error) {
	if m.err != nil { return nil, m.err }
	parsed, err := abi.JSON(stringsReader(registryABIJSON))
	if err != nil { return nil, err }
	if len(msg.Data) < 4 { return nil, errors.New("missing selector") }
	method, err := parsed.MethodById(msg.Data[:4])
	if err != nil { return nil, err }
	switch method.Name {
	case "providerActive": return method.Outputs.Pack(m.active)
	case "providerRecord": return method.Outputs.Pack(m.record)
	case "peerControlSession": return method.Outputs.Pack(m.control)
	default: return nil, errors.New("unexpected method")
	}
}

type stringReader string
func (s stringReader) Read(p []byte) (int, error) {
	if len(s) == 0 { return 0, io.EOF }
	n := copy(p, string(s))
	return n, io.EOF
}

func appendU16(out []byte, value uint16) []byte { var b [2]byte; binary.BigEndian.PutUint16(b[:], value); return append(out, b[:]...) }
func appendU32(out []byte, value uint32) []byte { var b [4]byte; binary.BigEndian.PutUint32(b[:], value); return append(out, b[:]...) }
func appendU64(out []byte, value uint64) []byte { var b [8]byte; binary.BigEndian.PutUint64(b[:], value); return append(out, b[:]...) }

func providerRecord(peer common.Hash) []byte {
	wallet := common.HexToAddress("0x1000000000000000000000000000000000000001")
	model := common.HexToHash("0x0100000000000000000000000000000000000000000000000000000000000001")
	capability := common.HexToHash("0x0200000000000000000000000000000000000000000000000000000000000002")
	endpoint := []byte("wqpu://127.0.0.1:7443")
	out := []byte{1}
	out = append(out, wallet.Bytes()...)
	out = append(out, peer.Bytes()...)
	out = appendU16(out, 1)
	out = appendU16(out, uint16(len(endpoint)))
	out = append(out, endpoint...)
	out = appendU16(out, 1)
	out = append(out, model.Bytes()...)
	out = appendU64(out, 100)
	out = appendU64(out, 10)
	out = appendU64(out, 8*1024*1024*1024)
	out = append(out, capability.Bytes()...)
	out = appendU64(out, 100)
	out = appendU64(out, 200)
	out = appendU32(out, ProtocolVersion)
	return out
}

func testPeer() common.Hash { return common.HexToHash("0xaa000000000000000000000000000000000000000000000000000000000000aa") }

func TestResolvePeerRequiresActiveChainRecordAndControlSession(t *testing.T) {
	peerID := testPeer()
	control := common.HexToAddress("0x2000000000000000000000000000000000000002")
	registry, err := New(mockCaller{active: true, record: providerRecord(peerID), control: control})
	if err != nil { t.Fatal(err) }
	peer, err := registry.ResolvePeer(context.Background(), peerID)
	if err != nil { t.Fatal(err) }
	if peer.Provider.PeerID != peerID || peer.ControlSession != control || len(peer.Provider.Endpoints) != 1 {
		t.Fatalf("resolved peer=%+v", peer)
	}
}

func TestResolvePeerRejectsInactivePeer(t *testing.T) {
	peerID := testPeer()
	registry, err := New(mockCaller{active: false, record: providerRecord(peerID), control: common.HexToAddress("0x2000000000000000000000000000000000000002")})
	if err != nil { t.Fatal(err) }
	if _, err := registry.ResolvePeer(context.Background(), peerID); err == nil { t.Fatal("inactive peer should fail") }
}

func TestResolvePeerRejectsRecordForAnotherPeer(t *testing.T) {
	peerID := testPeer()
	other := common.HexToHash("0xbb000000000000000000000000000000000000000000000000000000000000bb")
	registry, err := New(mockCaller{active: true, record: providerRecord(other), control: common.HexToAddress("0x2000000000000000000000000000000000000002")})
	if err != nil { t.Fatal(err) }
	if _, err := registry.ResolvePeer(context.Background(), peerID); err == nil { t.Fatal("mismatched provider record should fail") }
}

func TestResolvePeerRejectsMissingControlSession(t *testing.T) {
	peerID := testPeer()
	registry, err := New(mockCaller{active: true, record: providerRecord(peerID)})
	if err != nil { t.Fatal(err) }
	if _, err := registry.ResolvePeer(context.Background(), peerID); err == nil { t.Fatal("missing control session should fail") }
}

func TestDecodeProviderRecordRejectsTrailingAndMalformedEndpoint(t *testing.T) {
	peerID := testPeer()
	valid := providerRecord(peerID)
	if _, err := DecodeProviderRecord(append(valid, 0)); err == nil { t.Fatal("trailing provider bytes should fail") }

	bad := append([]byte(nil), valid...)
	endpointStart := 1 + 20 + 32 + 2 + 2
	copy(bad[endpointStart: endpointStart+len("wqpu://127.0.0.1:7443")], []byte("http://127.0.0.1:7443"))
	if _, err := DecodeProviderRecord(bad); err == nil { t.Fatal("non-WQPU endpoint should fail") }
}
