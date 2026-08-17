package main

import (
	"context"
	"errors"
	"fmt"
	"math/big"
	"os"
	"strings"
	"time"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/crypto"
	"github.com/ethereum/go-ethereum/ethclient"
)

const providerSessionKeyHex = "8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3"

var precompile = common.HexToAddress("0x0000000000000000000000000000000000000900")

const abiJSON = `[
 {"type":"function","name":"providerActive","stateMutability":"view","inputs":[{"name":"peerId","type":"bytes32"}],"outputs":[{"type":"bool"}]},
 {"type":"function","name":"providerRecord","stateMutability":"view","inputs":[{"name":"peerId","type":"bytes32"}],"outputs":[{"type":"bytes"}]},
 {"type":"function","name":"peerControlSession","stateMutability":"view","inputs":[{"name":"peerId","type":"bytes32"}],"outputs":[{"type":"address"}]}
]`

func call(ctx context.Context, client *ethclient.Client, parsed abi.ABI, method string, args ...any) ([]any, error) {
	data, err := parsed.Pack(method, args...)
	if err != nil { return nil, err }
	out, err := client.CallContract(ctx, ethereum.CallMsg{To: &precompile, Data: data}, nil)
	if err != nil { return nil, err }
	return parsed.Unpack(method, out)
}

func run(rpcURL string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client, err := ethclient.DialContext(ctx, rpcURL)
	if err != nil { return err }
	defer client.Close()
	parsed, err := abi.JSON(strings.NewReader(abiJSON))
	if err != nil { return err }

	peerID := crypto.Keccak256Hash([]byte("wqpu-live-rpc-peer"))
	key, err := crypto.HexToECDSA(providerSessionKeyHex)
	if err != nil { return err }
	expectedSession := crypto.PubkeyToAddress(key.PublicKey)

	activeValues, err := call(ctx, client, parsed, "providerActive", peerID)
	if err != nil { return fmt.Errorf("providerActive: %w", err) }
	if len(activeValues) != 1 { return errors.New("providerActive returned wrong value count") }
	active, ok := activeValues[0].(bool)
	if !ok || !active { return errors.New("published live peer is not active") }

	recordValues, err := call(ctx, client, parsed, "providerRecord", peerID)
	if err != nil { return fmt.Errorf("providerRecord: %w", err) }
	if len(recordValues) != 1 { return errors.New("providerRecord returned wrong value count") }
	record, ok := recordValues[0].([]byte)
	if !ok || len(record) == 0 { return errors.New("published live peer has no provider record") }

	controlValues, err := call(ctx, client, parsed, "peerControlSession", peerID)
	if err != nil { return fmt.Errorf("peerControlSession: %w", err) }
	if len(controlValues) != 1 { return errors.New("peerControlSession returned wrong value count") }
	control, ok := controlValues[0].(common.Address)
	if !ok || control != expectedSession {
		return fmt.Errorf("peer control session=%v, want %s", controlValues[0], expectedSession.Hex())
	}

	fmt.Printf("live registry passed: peer=%s controlSession=%s recordBytes=%d\n", peerID.Hex(), control.Hex(), len(record))
	return nil
}

func main() {
	_ = big.NewInt // keep go-ethereum CallContract interface types linked consistently
	if len(os.Args) != 3 || os.Args[1] != "run" {
		fmt.Fprintln(os.Stderr, "usage: wqpu-registry-smoke run http://127.0.0.1:8545")
		os.Exit(2)
	}
	if err := run(os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
