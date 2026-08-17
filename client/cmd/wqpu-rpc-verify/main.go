package main

import (
	"context"
	"encoding/hex"
	"errors"
	"fmt"
	"math/big"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/ethclient"

	"github.com/eav021107-debug/WQPU/client/internal/chainclient"
)

func parseCheckpoint(blockRaw, hashRaw string) (uint64, common.Hash, error) {
	block, err := strconv.ParseUint(blockRaw, 10, 64)
	if err != nil || block == 0 {
		return 0, common.Hash{}, errors.New("checkpoint block must be a positive uint64")
	}
	if len(hashRaw) != 66 || !strings.HasPrefix(hashRaw, "0x") {
		return 0, common.Hash{}, errors.New("checkpoint hash must be 0x-prefixed 32 bytes")
	}
	raw, err := hex.DecodeString(hashRaw[2:])
	if err != nil || len(raw) != 32 {
		return 0, common.Hash{}, errors.New("checkpoint hash must be canonical hex")
	}
	return block, common.BytesToHash(raw), nil
}

func verifyCheckpoint(ctx context.Context, rpcURL string, block uint64, expected common.Hash) error {
	eth, err := ethclient.DialContext(ctx, rpcURL)
	if err != nil {
		return fmt.Errorf("dial WQPU checkpoint RPC: %w", err)
	}
	defer eth.Close()

	header, err := eth.HeaderByNumber(ctx, new(big.Int).SetUint64(block))
	if err != nil {
		return fmt.Errorf("read WQPU checkpoint block %d: %w", block, err)
	}
	if header == nil {
		return errors.New("WQPU checkpoint header is missing")
	}
	actual := header.Hash()
	if actual != expected {
		return fmt.Errorf("wrong WQPU checkpoint hash at block %d: got %s want %s", block, actual.Hex(), expected.Hex())
	}
	return nil
}

func main() {
	if len(os.Args) != 2 && len(os.Args) != 4 {
		fmt.Fprintln(os.Stderr, "usage: wqpu-rpc-verify RPC_URL [CHECKPOINT_BLOCK CHECKPOINT_HASH]")
		os.Exit(2)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	client, err := chainclient.DialDev(ctx, os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer client.Close()

	if len(os.Args) == 4 {
		block, hash, err := parseCheckpoint(os.Args[2], os.Args[3])
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
		if client.VerifiedBlock() < block {
			fmt.Fprintf(os.Stderr, "WQPU canonical head %d is below checkpoint block %d\n", client.VerifiedBlock(), block)
			os.Exit(1)
		}
		if err := verifyCheckpoint(ctx, os.Args[1], block, hash); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Printf("WQPU RPC VERIFIED chain_id=%d protocol=%d block=%d checkpoint=%d:%s\n", client.EVMChainID(), client.Protocol(), client.VerifiedBlock(), block, hash.Hex())
		return
	}

	fmt.Printf("WQPU RPC VERIFIED chain_id=%d protocol=%d block=%d\n", client.EVMChainID(), client.Protocol(), client.VerifiedBlock())
}
