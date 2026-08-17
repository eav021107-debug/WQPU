package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/ethereum/go-ethereum/common"

	"github.com/eav021107-debug/WQPU/client/internal/chainclient"
	"github.com/eav021107-debug/WQPU/client/internal/computenode"
	"github.com/eav021107-debug/WQPU/client/internal/devidentity"
)

func verifyIdentity(ctx context.Context, chain *chainclient.Client, slot int) error {
	if chain == nil || chain.Registry() == nil { return errors.New("verified WQPU chain registry is required") }
	peer, err := chain.Registry().ResolvePeer(ctx, devidentity.PeerID(slot))
	if err != nil { return fmt.Errorf("resolve WQPU devnet slot %d: %w", slot, err) }
	expected, err := devidentity.SessionAddress(slot)
	if err != nil { return err }
	if peer.Provider.PeerID != devidentity.PeerID(slot) { return errors.New("WQPU registry returned another peer") }
	if peer.ControlSession != expected { return fmt.Errorf("WQPU slot %d control session mismatch", slot) }
	return nil
}

func run(ctx context.Context, rpcURL string, slot int, listenEndpoint, runtimeBase string) error {
	if ctx == nil { return errors.New("WQPU node context is required") }
	if !devidentity.ValidSlot(slot) { return fmt.Errorf("WQPU devnet slot must be within 0..%d", devidentity.SlotCount-1) }
	if runtimeBase == "" { return errors.New("WQPU runtime directory is required") }

	connectCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	chain, err := chainclient.DialDev(connectCtx, rpcURL)
	cancel()
	if err != nil { return err }
	defer chain.Close()

	verifyCtx, verifyCancel := context.WithTimeout(ctx, 15*time.Second)
	err = verifyIdentity(verifyCtx, chain, slot)
	verifyCancel()
	if err != nil { return err }

	key, err := devidentity.SessionKey(slot)
	if err != nil { return err }
	node, err := computenode.Start(ctx, computenode.Config{
		RuntimeBase: runtimeBase,
		Signer: key,
		ChainID: chainclient.DevWQPUChainID,
		LocalPeerID: devidentity.PeerID(slot),
		Registry: chain.Registry(),
		ListenEndpoint: listenEndpoint,
		MaxConnections: 32,
		RPCPort: devidentity.RPCPort(slot),
		RPCThreads: 1,
		RPCDevices: []string{"CPU"},
		RPCOutput: os.Stderr,
		BackendReady: 45 * time.Second,
	})
	if err != nil { return err }
	defer node.Close()

	fmt.Printf("WQPU PHYSICAL NODE READY: slot=%d peer=%s listen=%s chain=%s\n", slot, devidentity.PeerID(slot).Hex(), node.ProviderEndpoint(), rpcURL)
	for {
		select {
		case <-ctx.Done():
			return nil
		case err, ok := <-node.ProviderErrors():
			if !ok { return nil }
			if err != nil { return fmt.Errorf("WQPU physical node provider: %w", err) }
		}
	}
}

func main() {
	if len(os.Args) != 5 {
		fmt.Fprintln(os.Stderr, "usage: wqpu-dev-node RPC_URL SLOT LISTEN_ENDPOINT RUNTIME_BASE")
		os.Exit(2)
	}
	slot, err := strconv.Atoi(os.Args[2])
	if err != nil {
		fmt.Fprintln(os.Stderr, "WQPU devnet slot must be an integer")
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1], slot, os.Args[3], os.Args[4]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

var _ common.Hash
