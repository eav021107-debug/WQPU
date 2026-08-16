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

// Public deterministic DEVNET-ONLY keys. Never use these keys for real funds.
const (
	walletKeyHex          = "4c0883a69102937d6231471b5dbb6204fe5129617082792b1eaa4b7c3e9b4b5a"
	providerSessionKeyHex = "1111111111111111111111111111111111111111111111111111111111111111"
	requestSessionKeyHex  = "2222222222222222222222222222222222222222222222222222222222222222"
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
  {"type":"function","name":"fundSession","stateMutability":"payable","inputs":[{"name":"wallet","type":"address"},{"name":"session","type":"address"},{"name":"units","type":"uint64"}],"outputs":[]},
  {"type":"function","name":"reserveJob","stateMutability":"nonpayable","inputs":[{"name":"envelope","type":"bytes"}],"outputs":[]},
  {"type":"function","name":"submitReceipt","stateMutability":"nonpayable","inputs":[{"name":"envelope","type":"bytes"}],"outputs":[]},
  {"type":"function","name":"finalizeJob","stateMutability":"nonpayable","inputs":[{"name":"envelope","type":"bytes"}],"outputs":[]},
  {"type":"function","name":"withdrawSession","stateMutability":"nonpayable","inputs":[{"name":"wallet","type":"address"},{"name":"session","type":"address"},{"name":"units","type":"uint64"}],"outputs":[]},
  {"type":"function","name":"globalPrice","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]},
  {"type":"function","name":"priceEpoch","stateMutability":"view","inputs":[],"outputs":[{"type":"uint256"}]}
]`

func mustKey(raw string) *ecdsa.PrivateKey {
	key, err := crypto.HexToECDSA(raw)
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
	chainID, err := client.ChainID(ctx)
	if err != nil {
		return err
	}
	tx := types.NewTransaction(nonce, precompileAddress, value, gas*2, gasPrice, data)
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

func signSession(walletKey *ecdsa.PrivateKey, delegation wqpu.SessionDelegation) ([]byte, error) {
	digest, err := wqpu.SessionDigest(delegation, wqpu.DevNetworkConfig.EVMChainID)
	if err != nil {
		return nil, err
	}
	return crypto.Sign(digest, walletKey)
}

func authorize(ctx context.Context, client *ethclient.Client, contractABI abi.ABI, walletKey *ecdsa.PrivateKey, delegation wqpu.SessionDelegation) error {
	sig, err := signSession(walletKey, delegation)
	if err != nil {
		return err
	}
	data, err := contractABI.Pack(
		"authorizeSession",
		delegation.Wallet, delegation.Session,
		delegation.IssuedHeight, delegation.ExpiresHeight,
		delegation.MaxSpendUnits, delegation.MaxJobUnits,
		delegation.RevocationNonce, delegation.Permissions,
		sig,
	)
	if err != nil {
		return err
	}
	return send(ctx, client, walletKey, data, big.NewInt(0))
}

func signAction(key *ecdsa.PrivateKey, action wqpu.SessionAction) ([]byte, error) {
	digest, err := wqpu.ActionDigest(action, wqpu.DevNetworkConfig.EVMChainID)
	if err != nil {
		return nil, err
	}
	return crypto.Sign(digest, key)
}

func paymentNative(units uint64) *big.Int {
	return new(big.Int).Mul(new(big.Int).SetUint64(units), new(big.Int).SetUint64(wqpu.NativeUnitsPerPaymentUnit))
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
	providerKey := mustKey(providerSessionKeyHex)
	requesterKey := mustKey(requestSessionKeyHex)
	wallet := crypto.PubkeyToAddress(walletKey.PublicKey)
	providerSession := crypto.PubkeyToAddress(providerKey.PublicKey)
	requesterSession := crypto.PubkeyToAddress(requesterKey.PublicKey)
	contractABI := parsedABI()

	height, err := client.BlockNumber(ctx)
	if err != nil {
		return err
	}
	providerDelegation := wqpu.SessionDelegation{
		WQPUChainID: wqpu.DevNetworkConfig.WQPUChainID,
		Wallet: wallet,
		Session: providerSession,
		IssuedHeight: height,
		ExpiresHeight: height + 1_000,
		MaxSpendUnits: 1,
		MaxJobUnits: 1,
		Permissions: wqpu.SessionPermProvider,
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	requesterDelegation := wqpu.SessionDelegation{
		WQPUChainID: wqpu.DevNetworkConfig.WQPUChainID,
		Wallet: wallet,
		Session: requesterSession,
		IssuedHeight: height,
		ExpiresHeight: height + 1_000,
		MaxSpendUnits: 1_000,
		MaxJobUnits: 100,
		Permissions: wqpu.SessionPermJob | wqpu.SessionPermSettle,
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	if err := authorize(ctx, client, contractABI, walletKey, providerDelegation); err != nil {
		return fmt.Errorf("authorize provider session: %w", err)
	}
	if err := authorize(ctx, client, contractABI, walletKey, requesterDelegation); err != nil {
		return fmt.Errorf("authorize requester session: %w", err)
	}

	peerID := crypto.Keccak256Hash([]byte("wqpu-economic-peer"))
	modelHash := crypto.Keccak256Hash([]byte("wqpu-economic-model"))
	announcement := wqpu.ProviderAnnouncement{
		Wallet: wallet,
		PeerID: peerID,
		Endpoints: []string{"wqpu://127.0.0.1:7443"},
		ModelHashes: []common.Hash{modelHash},
		CapacityUnits: 20_000,
		FreeMemoryBytes: 8 * 1024 * 1024 * 1024,
		CapabilityHash: crypto.Keccak256Hash([]byte("wqpu-economic-capability")),
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	providerPayload, err := wqpu.ProviderAnnouncementHash(announcement)
	if err != nil {
		return err
	}
	providerAction := wqpu.SessionAction{
		WQPUChainID: wqpu.DevNetworkConfig.WQPUChainID,
		Wallet: wallet,
		Session: providerSession,
		ActionKind: wqpu.ActionPublishProvider,
		ActionNonce: 0,
		Permission: wqpu.SessionPermProvider,
		PayloadHash: providerPayload,
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	providerSig, err := signAction(providerKey, providerAction)
	if err != nil {
		return err
	}
	providerEnvelope, err := wqpu.EncodeProviderPublishEnvelope(wqpu.ProviderPublishEnvelope{
		Wallet: wallet,
		Session: providerSession,
		ActionNonce: 0,
		Announcement: announcement,
		Signature: providerSig,
	})
	if err != nil {
		return err
	}
	publishData, err := contractABI.Pack("publishProvider", providerEnvelope)
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, publishData, big.NewInt(0)); err != nil {
		return fmt.Errorf("publishProvider: %w", err)
	}

	const fundedUnits uint64 = 100
	fundData, err := contractABI.Pack("fundSession", wallet, requesterSession, fundedUnits)
	if err != nil {
		return err
	}
	beforeFund, err := client.BalanceAt(ctx, precompileAddress, nil)
	if err != nil {
		return err
	}
	if beforeFund.Sign() != 0 {
		return fmt.Errorf("fresh WQPU precompile balance=%s, want 0", beforeFund)
	}
	if err := send(ctx, client, walletKey, fundData, paymentNative(fundedUnits)); err != nil {
		return fmt.Errorf("fundSession: %w", err)
	}
	afterFund, err := client.BalanceAt(ctx, precompileAddress, nil)
	if err != nil {
		return err
	}
	if afterFund.Cmp(paymentNative(fundedUnits)) != 0 {
		return fmt.Errorf("precompile balance after fund=%s, want %s", afterFund, paymentNative(fundedUnits))
	}

	price, err := callUint(ctx, client, contractABI, "globalPrice")
	if err != nil {
		return err
	}
	epoch, err := callUint(ctx, client, contractABI, "priceEpoch")
	if err != nil {
		return err
	}
	const maxCompute uint64 = 10_000
	maxCharge, err := wqpu.ChargeForUnits(price, maxCompute)
	if err != nil {
		return err
	}
	if maxCharge >= fundedUnits {
		return fmt.Errorf("test job charge=%d must leave refundable escrow", maxCharge)
	}
	jobID := crypto.Keccak256Hash([]byte("wqpu-economic-job"))
	request := wqpu.JobRequest{
		JobID: jobID,
		RequesterWallet: wallet,
		ModelHash: modelHash,
		PromptCommitment: crypto.Keccak256Hash([]byte("private prompt commitment")),
		PriceEpoch: epoch,
		PricePerMillionUnits: price,
		MaxComputeUnits: maxCompute,
		MaxChargeUnits: maxCharge,
		ModelBytes: 1 * 1024 * 1024,
		Providers: []wqpu.JobProviderReservation{{
			ProviderWallet: wallet,
			ProviderPeerID: peerID,
			ReservedComputeUnits: maxCompute,
			AssignedModelBytes: 1 * 1024 * 1024,
		}},
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	jobPayload, err := wqpu.JobRequestHash(request)
	if err != nil {
		return err
	}
	reserveAction := wqpu.SessionAction{
		WQPUChainID: wqpu.DevNetworkConfig.WQPUChainID,
		Wallet: wallet,
		Session: requesterSession,
		ActionKind: wqpu.ActionReserveJob,
		ActionNonce: 0,
		Permission: wqpu.SessionPermJob,
		PayloadHash: jobPayload,
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	reserveSig, err := signAction(requesterKey, reserveAction)
	if err != nil {
		return err
	}
	jobEnvelope, err := wqpu.EncodeJobReserveEnvelope(wqpu.JobReserveEnvelope{
		Wallet: wallet,
		Session: requesterSession,
		ActionNonce: 0,
		Request: request,
		Signature: reserveSig,
	})
	if err != nil {
		return err
	}
	reserveData, err := contractABI.Pack("reserveJob", jobEnvelope)
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, reserveData, big.NewInt(0)); err != nil {
		return fmt.Errorf("reserveJob: %w", err)
	}

	const acceptedCompute uint64 = 3_000
	acceptedCharge, err := wqpu.ChargeForUnits(price, acceptedCompute)
	if err != nil {
		return err
	}
	receipt := wqpu.WorkReceipt{
		JobID: jobID,
		ProviderWallet: wallet,
		ProviderPeerID: peerID,
		Sequence: 1,
		ComputeUnits: acceptedCompute,
		CumulativeComputeUnits: acceptedCompute,
		CumulativePaymentUnits: acceptedCharge,
		ResultCommitment: crypto.Keccak256Hash([]byte("accepted model result")),
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	receiptDigest, err := wqpu.ReceiptDigest(receipt, requesterSession, providerSession, wqpu.DevNetworkConfig)
	if err != nil {
		return err
	}
	requesterReceiptSig, err := crypto.Sign(receiptDigest, requesterKey)
	if err != nil {
		return err
	}
	providerReceiptSig, err := crypto.Sign(receiptDigest, providerKey)
	if err != nil {
		return err
	}
	receiptEnvelope, err := wqpu.EncodeReceiptEnvelope(wqpu.ReceiptEnvelope{
		Receipt: receipt,
		RequesterSignature: requesterReceiptSig,
		ProviderSignature: providerReceiptSig,
	})
	if err != nil {
		return err
	}
	receiptData, err := contractABI.Pack("submitReceipt", receiptEnvelope)
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, receiptData, big.NewInt(0)); err != nil {
		return fmt.Errorf("submitReceipt: %w", err)
	}

	finalizePayload, err := wqpu.FinalizePayloadHash(jobID)
	if err != nil {
		return err
	}
	finalizeAction := wqpu.SessionAction{
		WQPUChainID: wqpu.DevNetworkConfig.WQPUChainID,
		Wallet: wallet,
		Session: requesterSession,
		ActionKind: wqpu.ActionFinalizeJob,
		ActionNonce: 1,
		Permission: wqpu.SessionPermSettle,
		PayloadHash: finalizePayload,
		ProtocolVersion: uint32(wqpu.ProtocolVersion),
	}
	finalizeSig, err := signAction(requesterKey, finalizeAction)
	if err != nil {
		return err
	}
	finalizeEnvelope, err := wqpu.EncodeFinalizeEnvelope(wqpu.FinalizeEnvelope{
		Wallet: wallet,
		Session: requesterSession,
		ActionNonce: 1,
		JobID: jobID,
		Signature: finalizeSig,
	})
	if err != nil {
		return err
	}
	finalizeData, err := contractABI.Pack("finalizeJob", finalizeEnvelope)
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, finalizeData, big.NewInt(0)); err != nil {
		return fmt.Errorf("finalizeJob: %w", err)
	}

	afterFinalize, err := client.BalanceAt(ctx, precompileAddress, nil)
	if err != nil {
		return err
	}
	expectedAfterFinalize := paymentNative(fundedUnits - acceptedCharge)
	if afterFinalize.Cmp(expectedAfterFinalize) != 0 {
		return fmt.Errorf("precompile balance after settlement=%s, want %s", afterFinalize, expectedAfterFinalize)
	}

	unused := fundedUnits - acceptedCharge
	withdrawData, err := contractABI.Pack("withdrawSession", wallet, requesterSession, unused)
	if err != nil {
		return err
	}
	if err := send(ctx, client, walletKey, withdrawData, big.NewInt(0)); err != nil {
		return fmt.Errorf("withdrawSession: %w", err)
	}
	finalBalance, err := client.BalanceAt(ctx, precompileAddress, nil)
	if err != nil {
		return err
	}
	if finalBalance.Sign() != 0 {
		return fmt.Errorf("WQPU precompile retained %s native units after payout/refund", finalBalance)
	}

	fmt.Printf(
		"economic cycle passed: wallet=%s providerSession=%s requesterSession=%s maxCharge=%d paid=%d refunded=%d\n",
		wallet.Hex(), providerSession.Hex(), requesterSession.Hex(), maxCharge, acceptedCharge, unused,
	)
	return nil
}

func main() {
	wallet := crypto.PubkeyToAddress(mustKey(walletKeyHex).PublicKey)
	if len(os.Args) == 2 && os.Args[1] == "address" {
		fmt.Println(wallet.Hex())
		return
	}
	if len(os.Args) != 3 || os.Args[1] != "run" {
		fmt.Fprintln(os.Stderr, "usage: wqpu-economic-smoke address | run http://127.0.0.1:8545")
		os.Exit(2)
	}
	if err := run(os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
