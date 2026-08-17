package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/eav021107-debug/WQPU/client/internal/chainclient"
)

func main() {
	if len(os.Args) != 2 {
		fmt.Fprintln(os.Stderr, "usage: wqpu-rpc-verify RPC_URL")
		os.Exit(2)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 6*time.Second)
	defer cancel()

	client, err := chainclient.DialDev(ctx, os.Args[1])
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	defer client.Close()

	fmt.Printf("WQPU RPC VERIFIED chain_id=%d protocol=%d block=%d\n", client.EVMChainID(), client.Protocol(), client.VerifiedBlock())
}
