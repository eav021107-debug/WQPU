package main

import (
	"context"
	"crypto/ecdsa"
	"errors"
	"fmt"
	"math/big"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"

	wqpu "github.com/eav021107-debug/WQPU/chain/x/wqpu/precompile"
)

// All keys in this command are deliberately public CI/devnet-only keys.
// Never reuse them for real funds, validators, or production sessions.
const walletKeyHex = "4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a"

var sessionKeyHexes = []string{
	"0000000000000000000000000000000000000000000000000000000000000001",
	"0000000000000000000000000000000000000000000000000000000000000002",
	"0000000000000000000000000000000000000000000000000000000000000003",
	"0000000000000000000000000000000000000000000000000000000000000004",
	"0000000000000000000000000000000000000000000000000000000000000005",
	"0000000000000000000000000000000000000000000000000000000000000006",
	"0000000000000000000000000000000000000000000000000000000000000007",
	"0000000000000000000000000000000000000000000000000000000000000008",
}

var legacyEndpoints = []string{
	"wqpu://127.0.0.1:17443",
	"wqpu://127.0.0.1:17444",
	"wqpu://127.0.0.1:17445",
}

var precompileAddress = common.HexToAddress("0x0000000000000000000000000000000000000900")

const abiJSON = `[
  {"type":"function","name":"authorizeSession","stateMutability":"nonpayable","inputs":[
    {"name":"wallet","type":"address"},{"name":"sessionAddress","type":"address"},
    {"name":"issuedHeight","type":"uint64"},{"name":"expiresHeight","type":"uint64"},
    {"name":"maxSpendUnits","type":"uint64"},{"name":"maxJobUnits","type":"uint64"},
    {"name":"revocationNonce","type":"uint64"},{"name":"permissions","type":"uint64"},
    {"name":"signature","type":"bytes"}],"outputs":[]},
  {"type":"function","name":"publishProvider","stateMutability":"nonpayable","inputs":[{"name":"envelope","type":"bytes"}],"outputs":[]}
]`

func mustKey(hexKey string) *ecdsa.PrivateKey {
	key, err := crypto.HexToECDSA(hexKey)
	if err != nil { panic(err) }
	return key
}

func parsedABI() abi.ABI {
	parsed, err := abi.JSON(strings.NewReader(abiJSON))
	if err != nil { panic(err) }
	return parsed
}

func peerID(index int) common.Hash {
	if index < 0 || index >= len(sessionKeyHexes) { return common.Hash{} }
	return crypto.Keccak256Hash([]byte(fmt.Sprintf("wqpu-live-compute-peer-%d", index+1)))
}

func defaultEndpoint(index int) string {
	return fmt.Sprintf("wqpu://127.0.0.1:%d", 17443+index)
}

func validateEndpoint(endpoint string) error {
	if endpoint == "" || len(endpoint) > 256 || strings.TrimSpace(endpoint) != endpoint { return errors.New("valid WQPU endpoint is required") }
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "wqpu" || u.User != nil || u.Hostname() == "" || u.Port() == "" || (u.Path != "" && u.Path != "/") || u.RawQuery != "" || u.Fragment != "" {
		return errors.New("invalid WQPU endpoint")
	}
	port, err := strconv.ParseUint(u.Port(), 10, 16)
	if err != nil || port == 0 { return errors.New("invalid WQPU endpoint port") }
	return nil
}

func waitReceipt(ctx context.Context, client *ethclient.Client, hash common.Hash) (*types.Receipt, error) {
	ticker := time.NewTicker(300 * time.Millisecond)
	defer ticker.Stop()
	for {
		receipt, err := client.TransactionReceipt(ctx, hash)
		if err == nil { return receipt, nil }
		if !errors.Is(err, ethereum.NotFound) { return nil, err }
		select {
		case <-ctx.Done(): return nil, ctx.Err()
		case <-ticker.C:
		}
	}
}

func send(ctx context.Context, client *ethclient.Client, key *ecdsa.PrivateKey, data []byte) error {
	from := crypto.PubkeyToAddress(key.PublicKey)
	nonce, err := client.PendingNonceAt(ctx, from)
	if err != nil { return err }
	gasPrice, err := client.SuggestGasPrice(ctx)
	if err != nil { return err }
	msg := ethereum.CallMsg{From: from, To: &precompileAddress, GasPrice: gasPrice, Value: big.NewInt(0), Data: data}
	gas, err := client.EstimateGas(ctx, msg)
	if err != nil { return fmt.Errorf("estimate gas: %w", err) }
	if gas > ^uint64(0)/2 { return errors.New("gas estimate overflow") }
	gas *= 2
	chainID, err := client.ChainID(ctx)
	if err != nil { return err }
	tx := types.NewTransaction(nonce, precompileAddress, big.NewInt(0), gas, gasPrice, data)
	signed, err := types.SignTx(tx, types.LatestSignerForChainID(chainID), key)
	if err != nil { return err }
	if err := client.SendTransaction(ctx, signed); err != nil { return err }
	receipt, err := waitReceipt(ctx, client, signed.Hash())
	if err != nil { return err }
	if receipt.Status != types.ReceiptStatusSuccessful { return fmt.Errorf("transaction %s reverted", signed.Hash()) }
	return nil
}

func publishOne(ctx context.Context, client *ethclient.Client, walletKey *ecdsa.PrivateKey, contractABI abi.ABI, height uint64, index int, endpoint string) error {
	if index < 0 || index >= len(sessionKeyHexes) { return fmt.Errorf("compute slot must be within 0..%d", len(sessionKeyHexes)-1) }
	if err := validateEndpoint(endpoint); err != nil { return err }
	wallet := crypto.PubkeyToAddress(walletKey.PublicKey)
	sessionKey := mustKey(sessionKeyHexes[index])
	session := crypto.PubkeyToAddress(sessionKey.PublicKey)
	delegation := wqpu.SessionDelegation{
		WQPUChainID: wqpu.DevNetworkConfig.WQPUChainID,
		Wallet: wallet,
		Session: session,
		IssuedHeight: height,
		ExpiresHeight: height + 100_000,
		MaxSpendUnits: 1_000_000_000,
		MaxJobUnits: 100_000_000,
		RevocationNonce: 0,
		Permissions: wqpu.SessionAllPermissions,
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	digest, err := wqpu.SessionDigest(delegation, wqpu.DevNetworkConfig.EVMChainID)
	if err != nil { return err }
	walletSig, err := crypto.Sign(digest, walletKey)
	if err != nil { return err }
	authorizeData, err := contractABI.Pack("authorizeSession", wallet, session, delegation.IssuedHeight, delegation.ExpiresHeight, delegation.MaxSpendUnits, delegation.MaxJobUnits, delegation.RevocationNonce, delegation.Permissions, walletSig)
	if err != nil { return err }
	if err := send(ctx, client, walletKey, authorizeData); err != nil { return fmt.Errorf("authorize compute session %d: %w", index, err) }

	announcement := wqpu.ProviderAnnouncement{
		Wallet: wallet,
		PeerID: peerID(index),
		Endpoints: []string{endpoint},
		ModelHashes: []common.Hash{crypto.Keccak256Hash([]byte("wqpu-live-tiny-model"))},
		CapacityUnits: 100,
		ReportedBusyUnits: 0,
		FreeMemoryBytes: 8 * 1024 * 1024 * 1024,
		CapabilityHash: crypto.Keccak256Hash([]byte(fmt.Sprintf("wqpu-live-compute-capability-%d", index+1))),
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	payloadHash, err := wqpu.ProviderAnnouncementHash(announcement)
	if err != nil { return err }
	action := wqpu.SessionAction{
		WQPUChainID: wqpu.DevNetworkConfig.WQPUChainID,
		Wallet: wallet,
		Session: session,
		ActionKind: wqpu.ActionPublishProvider,
		ActionNonce: 0,
		Permission: wqpu.SessionPermProvider,
		PayloadHash: payloadHash,
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	actionDigest, err := wqpu.ActionDigest(action, wqpu.DevNetworkConfig.EVMChainID)
	if err != nil { return err }
	sig, err := crypto.Sign(actionDigest, sessionKey)
	if err != nil { return err }
	envelope, err := wqpu.EncodeProviderPublishEnvelope(wqpu.ProviderPublishEnvelope{Wallet: wallet, Session: session, ActionNonce: 0, Announcement: announcement, Signature: sig})
	if err != nil { return err }
	publishData, err := contractABI.Pack("publishProvider", envelope)
	if err != nil { return err }
	if err := send(ctx, client, walletKey, publishData); err != nil { return fmt.Errorf("publish compute peer %d: %w", index, err) }
	fmt.Printf("published compute peer %d id=%s session=%s endpoint=%s\n", index, peerID(index).Hex(), session.Hex(), endpoint)
	return nil
}

func withClient(ctx context.Context, rpcURL string, fn func(*ethclient.Client, *ecdsa.PrivateKey, abi.ABI, uint64) error) error {
	client, err := ethclient.DialContext(ctx, rpcURL)
	if err != nil { return err }
	defer client.Close()
	height, err := client.BlockNumber(ctx)
	if err != nil { return err }
	return fn(client, mustKey(walletKeyHex), parsedABI(), height)
}

func publish(ctx context.Context, rpcURL string) error {
	return withClient(ctx, rpcURL, func(client *ethclient.Client, walletKey *ecdsa.PrivateKey, contractABI abi.ABI, height uint64) error {
		for index, endpoint := range legacyEndpoints {
			if err := publishOne(ctx, client, walletKey, contractABI, height, index, endpoint); err != nil { return err }
		}
		return nil
	})
}

func publishSlot(ctx context.Context, rpcURL string, slot int, endpoint string) error {
	return withClient(ctx, rpcURL, func(client *ethclient.Client, walletKey *ecdsa.PrivateKey, contractABI abi.ABI, height uint64) error {
		return publishOne(ctx, client, walletKey, contractABI, height, slot, endpoint)
	})
}

func main() {
	wallet := crypto.PubkeyToAddress(mustKey(walletKeyHex).PublicKey)
	if len(os.Args) == 2 && os.Args[1] == "address" {
		fmt.Println(wallet.Hex())
		return
	}
	if len(os.Args) == 2 && os.Args[1] == "describe" {
		for index, sessionHex := range sessionKeyHexes {
			session := crypto.PubkeyToAddress(mustKey(sessionHex).PublicKey)
			fmt.Printf("%d %s %s %s\n", index, peerID(index).Hex(), session.Hex(), defaultEndpoint(index))
		}
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	if len(os.Args) == 3 && os.Args[1] == "publish" {
		if err := publish(ctx, os.Args[2]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	if len(os.Args) == 5 && os.Args[1] == "publish-one" {
		slot, err := strconv.Atoi(os.Args[3])
		if err != nil {
			fmt.Fprintln(os.Stderr, "compute slot must be an integer")
			os.Exit(2)
		}
		if err := publishSlot(ctx, os.Args[2], slot, os.Args[4]); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	fmt.Fprintln(os.Stderr, "usage: wqpu-compute-bootstrap address | describe | publish RPC_URL | publish-one RPC_URL SLOT ENDPOINT")
	os.Exit(2)
}
