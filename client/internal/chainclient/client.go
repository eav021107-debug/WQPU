package chainclient

import (
	"context"
	"errors"
	"fmt"
	"math/big"
	"net/url"
	"strings"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"

	"github.com/eav021107-debug/WQPU/client/internal/chainregistry"
)

const (
	DevWQPUChainID          = "wqpu-dev-1"
	DevEVMChainID    uint64 = 711711
	ProtocolVersion  uint64 = 1
	DefaultMinBlock  uint64 = 1
)

var protocolVersionSelector = func() [4]byte {
	hash := crypto.Keccak256([]byte("protocolVersion()"))
	var selector [4]byte
	copy(selector[:], hash[:4])
	return selector
}()

type RPC interface {
	ChainID(context.Context) (*big.Int, error)
	BlockNumber(context.Context) (uint64, error)
	CallContract(context.Context, ethereum.CallMsg, *big.Int) ([]byte, error)
}

type Config struct {
	ExpectedEVMChainID   uint64
	ExpectedProtocol     uint64
	MinimumBlock         uint64
}

func DevConfig() Config {
	return Config{
		ExpectedEVMChainID: DevEVMChainID,
		ExpectedProtocol: ProtocolVersion,
		MinimumBlock: DefaultMinBlock,
	}
}

type Verified struct {
	RPC             RPC
	Registry        *chainregistry.Registry
	EVMChainID      uint64
	ProtocolVersion uint64
	BlockNumber     uint64
}

type Client struct {
	eth      *ethclient.Client
	verified Verified
}

func normalizeConfig(config Config) (Config, error) {
	if config.ExpectedEVMChainID == 0 { return Config{}, errors.New("expected WQPU EVM chain id must be positive") }
	if config.ExpectedProtocol == 0 { return Config{}, errors.New("expected WQPU protocol version must be positive") }
	if config.MinimumBlock == 0 { config.MinimumBlock = DefaultMinBlock }
	return config, nil
}

func decodeProtocolWord(output []byte) (uint64, error) {
	if len(output) != 32 { return 0, fmt.Errorf("protocolVersion returned %d bytes, want one ABI word", len(output)) }
	value := new(big.Int).SetBytes(output)
	if !value.IsUint64() || value.Sign() <= 0 { return 0, errors.New("protocolVersion returned a non-canonical positive uint64") }
	return value.Uint64(), nil
}

func Verify(ctx context.Context, rpc RPC, config Config) (Verified, error) {
	if ctx == nil { return Verified{}, errors.New("WQPU chain verification context is required") }
	if rpc == nil { return Verified{}, errors.New("WQPU chain RPC is required") }
	validated, err := normalizeConfig(config)
	if err != nil { return Verified{}, err }
	select {
	case <-ctx.Done(): return Verified{}, ctx.Err()
	default:
	}

	chainID, err := rpc.ChainID(ctx)
	if err != nil { return Verified{}, fmt.Errorf("read EVM chain id: %w", err) }
	if chainID == nil || !chainID.IsUint64() || chainID.Sign() <= 0 {
		return Verified{}, errors.New("WQPU RPC returned an invalid EVM chain id")
	}
	actualChainID := chainID.Uint64()
	if actualChainID != validated.ExpectedEVMChainID {
		return Verified{}, fmt.Errorf("wrong EVM chain id: got %d want %d", actualChainID, validated.ExpectedEVMChainID)
	}

	height, err := rpc.BlockNumber(ctx)
	if err != nil { return Verified{}, fmt.Errorf("read WQPU canonical head: %w", err) }
	if height < validated.MinimumBlock {
		return Verified{}, fmt.Errorf("WQPU canonical head %d is below required block %d", height, validated.MinimumBlock)
	}

	selector := protocolVersionSelector
	output, err := rpc.CallContract(ctx, ethereum.CallMsg{To: &chainregistry.PrecompileAddress, Data: selector[:]}, nil)
	if err != nil { return Verified{}, fmt.Errorf("call WQPU protocolVersion at %s: %w", chainregistry.PrecompileAddress.Hex(), err) }
	protocol, err := decodeProtocolWord(output)
	if err != nil { return Verified{}, err }
	if protocol != validated.ExpectedProtocol {
		return Verified{}, fmt.Errorf("wrong WQPU protocol version: got %d want %d", protocol, validated.ExpectedProtocol)
	}

	registry, err := chainregistry.New(rpc)
	if err != nil { return Verified{}, err }
	return Verified{RPC: rpc, Registry: registry, EVMChainID: actualChainID, ProtocolVersion: protocol, BlockNumber: height}, nil
}

func validateRPCURL(raw string) error {
	if raw == "" || strings.TrimSpace(raw) != raw { return errors.New("valid WQPU RPC URL is required") }
	parsed, err := url.Parse(raw)
	if err != nil { return errors.New("invalid WQPU RPC URL") }
	switch parsed.Scheme {
	case "http", "https", "ws", "wss":
	default:
		return errors.New("WQPU RPC URL must use http, https, ws, or wss")
	}
	if parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" {
		return errors.New("WQPU RPC URL must have a host and no credentials or fragment")
	}
	return nil
}

func Dial(ctx context.Context, rawURL string, config Config) (*Client, error) {
	if err := validateRPCURL(rawURL); err != nil { return nil, err }
	eth, err := ethclient.DialContext(ctx, rawURL)
	if err != nil { return nil, fmt.Errorf("dial WQPU JSON-RPC: %w", err) }
	verified, err := Verify(ctx, eth, config)
	if err != nil { eth.Close(); return nil, err }
	return &Client{eth: eth, verified: verified}, nil
}

func DialDev(ctx context.Context, rawURL string) (*Client, error) {
	return Dial(ctx, rawURL, DevConfig())
}

func (c *Client) Registry() *chainregistry.Registry {
	if c == nil { return nil }
	return c.verified.Registry
}

func (c *Client) EVMChainID() uint64 {
	if c == nil { return 0 }
	return c.verified.EVMChainID
}

func (c *Client) Protocol() uint64 {
	if c == nil { return 0 }
	return c.verified.ProtocolVersion
}

func (c *Client) VerifiedBlock() uint64 {
	if c == nil { return 0 }
	return c.verified.BlockNumber
}

func (c *Client) Close() {
	if c != nil && c.eth != nil { c.eth.Close() }
}

var _ common.Address = chainregistry.PrecompileAddress
