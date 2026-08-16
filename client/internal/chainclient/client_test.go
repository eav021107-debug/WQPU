package chainclient

import (
	"context"
	"errors"
	"math/big"
	"testing"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
)

type fakeRPC struct {
	chainID *big.Int
	height  uint64
	output  []byte

	chainErr error
	heightErr error
	callErr error

	chainCalls int
	heightCalls int
	contractCalls int
	lastCall ethereum.CallMsg
}

func (f *fakeRPC) ChainID(context.Context) (*big.Int, error) {
	f.chainCalls++
	if f.chainErr != nil { return nil, f.chainErr }
	if f.chainID == nil { return nil, nil }
	return new(big.Int).Set(f.chainID), nil
}

func (f *fakeRPC) BlockNumber(context.Context) (uint64, error) {
	f.heightCalls++
	return f.height, f.heightErr
}

func (f *fakeRPC) CallContract(_ context.Context, msg ethereum.CallMsg, _ *big.Int) ([]byte, error) {
	f.contractCalls++
	f.lastCall = msg
	if f.callErr != nil { return nil, f.callErr }
	return append([]byte(nil), f.output...), nil
}

func protocolWord(value uint64) []byte {
	out := make([]byte, 32)
	new(big.Int).SetUint64(value).FillBytes(out)
	return out
}

func validRPC() *fakeRPC {
	return &fakeRPC{chainID: new(big.Int).SetUint64(DevEVMChainID), height: 25, output: protocolWord(ProtocolVersion)}
}

func TestVerifyDevChainIdentityBeforeRegistryUse(t *testing.T) {
	rpc := validRPC()
	verified, err := Verify(context.Background(), rpc, DevConfig())
	if err != nil { t.Fatal(err) }
	if verified.Registry == nil || verified.EVMChainID != DevEVMChainID || verified.ProtocolVersion != ProtocolVersion || verified.BlockNumber != 25 {
		t.Fatalf("verified=%+v", verified)
	}
	if rpc.chainCalls != 1 || rpc.heightCalls != 1 || rpc.contractCalls != 1 { t.Fatalf("call counts chain=%d head=%d contract=%d", rpc.chainCalls, rpc.heightCalls, rpc.contractCalls) }
	if rpc.lastCall.To == nil || *rpc.lastCall.To != chainregistry.PrecompileAddress { t.Fatalf("protocol call target=%v", rpc.lastCall.To) }
	selector := protocolVersionSelector
	if string(rpc.lastCall.Data) != string(selector[:]) { t.Fatalf("protocol selector=%x want=%x", rpc.lastCall.Data, selector) }
}

func TestWrongChainIDFailsBeforeHeadOrPrecompile(t *testing.T) {
	rpc := validRPC()
	rpc.chainID = big.NewInt(1)
	if _, err := Verify(context.Background(), rpc, DevConfig()); err == nil { t.Fatal("wrong EVM chain should fail") }
	if rpc.heightCalls != 0 || rpc.contractCalls != 0 { t.Fatalf("wrong chain reached later calls head=%d contract=%d", rpc.heightCalls, rpc.contractCalls) }
}

func TestImmatureHeadFailsBeforePrecompile(t *testing.T) {
	rpc := validRPC()
	rpc.height = 0
	if _, err := Verify(context.Background(), rpc, DevConfig()); err == nil { t.Fatal("block zero should fail readiness") }
	if rpc.contractCalls != 0 { t.Fatalf("immature head reached protocol call %d times", rpc.contractCalls) }
}

func TestProtocolVersionMustBeOneCanonicalABIWord(t *testing.T) {
	for name, output := range map[string][]byte{
		"empty": nil,
		"short": make([]byte, 31),
		"zero": protocolWord(0),
		"wrong": protocolWord(2),
	} {
		t.Run(name, func(t *testing.T) {
			rpc := validRPC()
			rpc.output = output
			if _, err := Verify(context.Background(), rpc, DevConfig()); err == nil { t.Fatalf("protocol output %s should fail", name) }
		})
	}
	tooLarge := make([]byte, 32)
	tooLarge[0] = 1
	if _, err := decodeProtocolWord(tooLarge); err == nil { t.Fatal("protocol value wider than uint64 should fail") }
}

func TestVerificationPropagatesRPCFailures(t *testing.T) {
	rpc := validRPC(); rpc.chainErr = errors.New("chain unavailable")
	if _, err := Verify(context.Background(), rpc, DevConfig()); err == nil { t.Fatal("chain id RPC error should fail") }
	rpc = validRPC(); rpc.heightErr = errors.New("head unavailable")
	if _, err := Verify(context.Background(), rpc, DevConfig()); err == nil { t.Fatal("head RPC error should fail") }
	rpc = validRPC(); rpc.callErr = errors.New("precompile unavailable")
	if _, err := Verify(context.Background(), rpc, DevConfig()); err == nil { t.Fatal("protocol RPC error should fail") }
}

func TestCanceledContextFailsClosed(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	rpc := validRPC()
	if _, err := Verify(ctx, rpc, DevConfig()); err == nil { t.Fatal("canceled verification should fail") }
	if rpc.chainCalls != 0 { t.Fatal("canceled verification should not touch RPC") }
}

func TestRPCURLValidation(t *testing.T) {
	for _, raw := range []string{"http://127.0.0.1:8545", "https://rpc.example.test", "ws://127.0.0.1:8546", "wss://rpc.example.test/ws"} {
		if err := validateRPCURL(raw); err != nil { t.Fatalf("valid URL %q: %v", raw, err) }
	}
	for _, raw := range []string{"", " http://127.0.0.1:8545", "ftp://rpc.example.test", "http:///missing-host", "http://user:pass@rpc.example.test", "http://rpc.example.test/#fragment"} {
		if err := validateRPCURL(raw); err == nil { t.Fatalf("unsafe URL accepted: %q", raw) }
	}
}

func TestNormalizeConfigRequiresExplicitIdentity(t *testing.T) {
	if _, err := normalizeConfig(Config{}); err == nil { t.Fatal("empty config should fail") }
	if _, err := normalizeConfig(Config{ExpectedEVMChainID: DevEVMChainID}); err == nil { t.Fatal("missing protocol should fail") }
	config, err := normalizeConfig(Config{ExpectedEVMChainID: DevEVMChainID, ExpectedProtocol: ProtocolVersion})
	if err != nil { t.Fatal(err) }
	if config.MinimumBlock != DefaultMinBlock { t.Fatalf("minimum block=%d", config.MinimumBlock) }
}

var _ common.Address = chainregistry.PrecompileAddress
