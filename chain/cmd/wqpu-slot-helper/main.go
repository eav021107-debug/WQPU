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

const slotCount = 8

var precompileAddress = common.HexToAddress("0x0000000000000000000000000000000000000900")

const registryABIJSON = `[
  {"type":"function","name":"providerActive","stateMutability":"view","inputs":[{"name":"peerId","type":"bytes32"}],"outputs":[{"type":"bool"}]}
]`

func peerID(slot int) common.Hash {
	if slot < 0 || slot >= slotCount {
		return common.Hash{}
	}
	return crypto.Keccak256Hash([]byte(fmt.Sprintf("wqpu-live-compute-peer-%d", slot+1)))
}

func providerActive(ctx context.Context, client *ethclient.Client, contractABI abi.ABI, slot int) (bool, error) {
	id := peerID(slot)
	if id == (common.Hash{}) {
		return false, errors.New("invalid compute slot")
	}
	input, err := contractABI.Pack("providerActive", id)
	if err != nil {
		return false, err
	}
	output, err := client.CallContract(ctx, ethereum.CallMsg{To: &precompileAddress, Data: input}, (*big.Int)(nil))
	if err != nil {
		return false, err
	}
	values, err := contractABI.Unpack("providerActive", output)
	if err != nil {
		return false, err
	}
	if len(values) != 1 {
		return false, errors.New("invalid providerActive result")
	}
	active, ok := values[0].(bool)
	if !ok {
		return false, errors.New("invalid providerActive bool")
	}
	return active, nil
}

func main() {
	if len(os.Args) != 3 || (os.Args[1] != "free" && os.Args[1] != "active") {
		fmt.Fprintln(os.Stderr, "usage: wqpu-slot-helper free|active RPC_URL")
		os.Exit(2)
	}
	parsed, err := abi.JSON(strings.NewReader(registryABIJSON))
	if err != nil {
		panic(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client, err := ethclient.DialContext(ctx, os.Args[2])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer client.Close()

	activeSlots := make([]int, 0, slotCount-1)
	for slot := 1; slot < slotCount; slot++ {
		active, err := providerActive(ctx, client, parsed, slot)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		if active {
			activeSlots = append(activeSlots, slot)
			continue
		}
		if os.Args[1] == "free" {
			fmt.Println(slot)
			return
		}
	}

	if os.Args[1] == "free" {
		fmt.Fprintln(os.Stderr, "no free WQPU devnet compute slots")
		os.Exit(1)
	}
	for i, slot := range activeSlots {
		if i > 0 {
			fmt.Print(" ")
		}
		fmt.Print(slot)
	}
	fmt.Println()
}
