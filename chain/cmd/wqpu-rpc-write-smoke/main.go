package main

import (
	"context"
	"crypto/ecdsa"
	"errors"
	"fmt"
	"math/big"
	"os"
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

// These keys are public, deterministic DEVNET TEST KEYS. They must never be
// reused for real funds or a public validator.
const (
	walletKeyHex  = "4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a"
	sessionKeyHex = "8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3"
)

var precompileAddress = common.HexToAddress("0x0000000000000000000000000000000000000900")

const abiJSON = `[
  {"type":"function","name":"authorizeSession","stateMutability":"nonpayable","inputs":[
    {"name":"wallet","type":"address"},{"name":"sessionAddress","type":"address"},
    {"name":"issuedHeight","type":"uint64"},{"name":"expiresHeight","type":"uint64"},
    {"name":"maxSpendUnits","type":"uint64"},{"name":"maxJobUnits","type":"uint64"},
    {"name":"revocationNonce","type":"uint64"},{"name":"permissions","type":"uint64"},
    {"name":"signature","type":"bytes"}],"outputs":[]},
  {"type":"function","name":"publishProvider","stateMutability":"nonpayable","inputs":[{"name":"envelope","type":"bytes"}],"outputs":[]},
  {"type":"function","name":"bondProvider","stateMutability":"payable","inputs":[{"name":"peerId","type":"bytes32"},{"name":"capacityUnits","type":"uint64"}],"outputs":[]},
  {"type":"function","name":"unbondProvider","stateMutability":"nonpayable","inputs":[{"name":"peerId","type":"bytes32"},{"name":"capacityUnits","type":"uint64"}],"outputs":[]},
  {"type":"function","name":"closePriceEpoch","stateMutability":"nonpayable","inputs":[],"outputs":[]},
  {"type":"function","name":"peerCount","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
  {"type":"function","name":"bondedPriceCapacity","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
  {"type":"function","name":"globalPrice","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
  {"type":"function","name":"priceEpoch","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]}
]`

func mustKey(hexKey string) *ecdsa.PrivateKey {
	key, err := crypto.HexToECDSA(hexKey)
	if err != nil {
		panic(err)
	}
	return key
}

func parsedABI() abi.ABI {
	parsed, err := abi.JSON(strings.NewReader(abiJSON))
	if err != nil {
		panic(err)
	}
	return parsed
}

func waitReceipt(ctx context.Context, client *ethclient.Client, hash common.Hash) (*types.Receipt, error) {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		receipt, err := client.TransactionReceipt(ctx, hash)
		if err == nil {
			return receipt, nil
		}
		if !errors.Is(err, ethereum.NotFound) {
			return nil, err
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-ticker.C:
		}
	}
}

func send(ctx context.Context, client *ethclient.Client, key *ecdsa.PrivateKey, data []byte, value *big.Int) error {
	from := crypto.PubkeyToAddress(key.PublicKey)
	nonce, err := client.PendingNonceAt(ctx, from)
	if err != nil {
		return err
	}
	gasPrice, err := client.SuggestGasPrice(ctx)
	if err != nil {
		return err
	}
	msg := ethereum.CallMsg{From: from, To: &precompileAddress, GasPrice: gasPrice, Value: value, Data: data}
	gas, err := client.EstimateGas(ctx, msg)
	if err != nil {
		return fmt.Errorf("estimate gas: %w", err)
	}
	if gas > ^uint64(0)/2 {
		return errors.New("gas estimate overflow")
	}
	gas *= 2
	chainID, err := client.ChainID(ctx)
	if err != nil {
		return err
	}
	tx := types.NewTransaction(nonce, precompileAddress, value, gas, gasPrice, data)
	signed, err := types.SignTx(tx, types.LatestSignerForChainID(chainID), key)
	if err != nil {
		return err
	}
	if err := client.SendTransaction(ctx, signed); err != nil {
		return err
	}
	receipt, err := waitReceipt(ctx, client, signed.Hash())
	if err != nil {
		return err
	}
	if receipt.Status != types.ReceiptStatusSuccessful {
		return fmt.Errorf("transaction %s reverted", signed.Hash())
	}
	return nil
}

func callUint(ctx context.Context, client *ethclient.Client, contractABI abi.ABI, method string) (uint64, error) {
	data, err := contractABI.Pack(method)
	if err != nil {
		return 0, err
	}
	out, err := client.CallContract(ctx, ethereum.CallMsg{To: &precompileAddress, Data: data}, nil)
	if err != nil {
		return 0, err
	}
	values, err := contractABI.Unpack(method, out)
	if err != nil {
		return 0, err
	}
	if len(values) != 1 {
		return 0, errors.New("unexpected WQPU query output count")
	}
	value, ok := values[0].(*big.Int)
	if !ok || !value.IsUint64() {
		return 0, errors.New("unexpected WQPU uint output")
	}
	return value.Uint64(), nil
}

func waitForHeight(ctx context.Context, client *ethclient.Client, target uint64) error {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		height, err := client.BlockNumber(ctx)
		if err == nil && height >= target {
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func run(rpcURL string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	client, err := ethclient.DialContext(ctx, rpcURL)
	if err != nil {
		return err
	}
	defer client.Close()

	walletKey := mustKey(walletKeyHex)
	sessionKey := mustKey(sessionKeyHex)
	wallet := crypto.PubkeyToAddress(walletKey.PublicKey)
	session := crypto.PubkeyToAddress(sessionKey.PublicKey)
	contractABI := parsedABI()

	height, err := client.BlockNumber(ctx)
	if err != nil {
		return err
	}
	delegation := wqpu.SessionDelegation{
		WQPUChainID: wqpu.DevNetworkConfig.WQPUChainID,
		Wallet: wallet,
		Session: session,
		IssuedHeight: height,
		ExpiresHeight: height + 1_000,
		MaxSpendUnits: 1_000_000_000,
		MaxJobUnits: 100_000_000,
		RevocationNonce: 0,
		Permissions: wqpu.SessionAllPermissions,
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	digest, err := wqpu.SessionDigest(delegation, wqpu.DevNetworkConfig.EVMChainID)
	if err != nil {
		return err
	}
	walletSig, err := crypto.Sign(digest, walletKey)
	if err != nil {
		return err
	}
	authorizeData, err := contractABI.Pack(
		"authorizeSession",
		wallet, session,
		delegation.IssuedHeight, delegation.ExpiresHeight,
		delegation.MaxSpendUnits, delegation.MaxJobUnits,
		delegation.RevocationNonce, delegation.Permissions,
		walletSig,
	)
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, authorizeData, big.NewInt(0)); err != nil {
		return fmt.Errorf("authorizeSession: %w", err)
	}

	peerID := crypto.Keccak256Hash([]byte("wqpu-live-rpc-peer"))
	announcement := wqpu.ProviderAnnouncement{
		Wallet: wallet,
		PeerID: peerID,
		Endpoints: []string{"wqpu://127.0.0.1:7443"},
		ModelHashes: []common.Hash{crypto.Keccak256Hash([]byte("wqpu-live-model"))},
		CapacityUnits: 100,
		ReportedBusyUnits: 0,
		FreeMemoryBytes: 8 * 1024 * 1024 * 1024,
		CapabilityHash: crypto.Keccak256Hash([]byte("wqpu-live-capability")),
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	payloadHash, err := wqpu.ProviderAnnouncementHash(announcement)
	if err != nil {
		return err
	}
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
	if err != nil {
		return err
	}
	providerSig, err := crypto.Sign(actionDigest, sessionKey)
	if err != nil {
		return err
	}
	envelope, err := wqpu.EncodeProviderPublishEnvelope(wqpu.ProviderPublishEnvelope{
		Wallet: wallet,
		Session: session,
		ActionNonce: 0,
		Announcement: announcement,
		Signature: providerSig,
	})
	if err != nil {
		return err
	}
	publishData, err := contractABI.Pack("publishProvider", envelope)
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, publishData, big.NewInt(0)); err != nil {
		return fmt.Errorf("publishProvider: %w", err)
	}
	peerCount, err := callUint(ctx, client, contractABI, "peerCount")
	if err != nil {
		return err
	}
	if peerCount != 1 {
		return fmt.Errorf("peerCount=%d, want 1", peerCount)
	}

	bondData, err := contractABI.Pack("bondProvider", peerID, uint64(100))
	if err != nil {
		return err
	}
	bondNative := new(big.Int).Mul(big.NewInt(100), new(big.Int).SetUint64(wqpu.NativeUnitsPerPaymentUnit))
	if err := send(ctx, client, walletKey, bondData, bondNative); err != nil {
		return fmt.Errorf("bondProvider: %w", err)
	}
	bonded, err := callUint(ctx, client, contractABI, "bondedPriceCapacity")
	if err != nil {
		return err
	}
	if bonded != 100 {
		return fmt.Errorf("bondedPriceCapacity=%d, want 100", bonded)
	}

	currentHeight, err := client.BlockNumber(ctx)
	if err != nil {
		return err
	}
	targetEpoch := currentHeight/wqpu.PriceEpochBlocks + 1
	if err := waitForHeight(ctx, client, targetEpoch*wqpu.PriceEpochBlocks); err != nil {
		return err
	}
	closeData, err := contractABI.Pack("closePriceEpoch")
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, closeData, big.NewInt(0)); err != nil {
		return fmt.Errorf("closePriceEpoch: %w", err)
	}
	price, err := callUint(ctx, client, contractABI, "globalPrice")
	if err != nil {
		return err
	}
	if price != 950 {
		return fmt.Errorf("globalPrice=%d, want 950 after idle bonded epoch", price)
	}
	epoch, err := callUint(ctx, client, contractABI, "priceEpoch")
	if err != nil {
		return err
	}
	if epoch < targetEpoch {
		return fmt.Errorf("priceEpoch=%d, want at least %d", epoch, targetEpoch)
	}

	unbondData, err := contractABI.Pack("unbondProvider", peerID, uint64(100))
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, unbondData, big.NewInt(0)); err != nil {
		return fmt.Errorf("unbondProvider: %w", err)
	}
	bonded, err = callUint(ctx, client, contractABI, "bondedPriceCapacity")
	if err != nil {
		return err
	}
	if bonded != 0 {
		return fmt.Errorf("bondedPriceCapacity=%d after unbond, want 0", bonded)
	}

	fmt.Printf("live writes passed: wallet=%s peer=%s price=%d epoch=%d\n", wallet.Hex(), peerID.Hex(), price, epoch)
	return nil
}

func main() {
	wallet := crypto.PubkeyToAddress(mustKey(walletKeyHex).PublicKey)
	if len(os.Args) == 2 && os.Args[1] == "address" {
		fmt.Println(wallet.Hex())
		return
	}
	if len(os.Args) != 3 || os.Args[1] != "run" {
		fmt.Fprintln(os.Stderr, "usage: wqpu-rpc-write-smoke address | run http://127.0.0.1:8545")
		os.Exit(2)
	}
	if err := run(os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
