package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"time"

	"github.com/ethereum/go-ethereum/crypto"

	"github.com/eav021107-debug/WQPU/client/internal/chainclient"
)

// This is the same public CI/devnet-only session key used by the chain write
// smoke. It is never a user wallet key and must never hold real funds.
const publishedSessionKeyHex = "8f2a559490e4f2fda090c1121e52d1d02235d61cf511bfd5baf0f68c19d0f4f3"

func verifyIdentity(client *chainclient.Client) error {
	if client.EVMChainID() != chainclient.DevEVMChainID {
		return fmt.Errorf("verified EVM chain id=%d want %d", client.EVMChainID(), chainclient.DevEVMChainID)
	}
	if client.Protocol() != chainclient.ProtocolVersion {
		return fmt.Errorf("verified WQPU protocol=%d want %d", client.Protocol(), chainclient.ProtocolVersion)
	}
	if client.VerifiedBlock() < chainclient.DefaultMinBlock {
		return fmt.Errorf("verified block=%d below %d", client.VerifiedBlock(), chainclient.DefaultMinBlock)
	}
	if client.Registry() == nil {
		return errors.New("verified WQPU chain has no production registry")
	}
	return nil
}

func verifyPublished(ctx context.Context, client *chainclient.Client) error {
	peerID := crypto.Keccak256Hash([]byte("wqpu-live-rpc-peer"))
	key, err := crypto.HexToECDSA(publishedSessionKeyHex)
	if err != nil { return err }
	expectedSession := crypto.PubkeyToAddress(key.PublicKey)

	peer, err := client.Registry().ResolvePeer(ctx, peerID)
	if err != nil { return fmt.Errorf("resolve published WQPU peer: %w", err) }
	if peer.Provider.PeerID != peerID {
		return fmt.Errorf("resolved peer=%s want %s", peer.Provider.PeerID.Hex(), peerID.Hex())
	}
	if peer.ControlSession != expectedSession {
		return fmt.Errorf("resolved control session=%s want %s", peer.ControlSession.Hex(), expectedSession.Hex())
	}
	if len(peer.Provider.Endpoints) != 1 || peer.Provider.Endpoints[0] != "wqpu://127.0.0.1:7443" {
		return fmt.Errorf("resolved endpoints=%v", peer.Provider.Endpoints)
	}
	if peer.Provider.ProtocolVersion != uint32(chainclient.ProtocolVersion) {
		return fmt.Errorf("provider protocol=%d want %d", peer.Provider.ProtocolVersion, chainclient.ProtocolVersion)
	}
	if peer.Provider.CapacityUnits != 100 || peer.Provider.ReportedBusyUnits != 0 {
		return fmt.Errorf("provider capacity/load=%d/%d want 100/0", peer.Provider.CapacityUnits, peer.Provider.ReportedBusyUnits)
	}
	fmt.Printf("live chain registry resolved peer=%s session=%s endpoint=%s\n", peerID.Hex(), expectedSession.Hex(), peer.Provider.Endpoints[0])
	return nil
}

func run(rpcURL, mode string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	client, err := chainclient.DialDev(ctx, rpcURL)
	if err != nil { return err }
	defer client.Close()
	if err := verifyIdentity(client); err != nil { return err }
	fmt.Printf("live WQPU identity verified: evmChain=%d protocol=%d block=%d\n", client.EVMChainID(), client.Protocol(), client.VerifiedBlock())
	switch mode {
	case "identity":
		return nil
	case "published":
		return verifyPublished(ctx, client)
	default:
		return errors.New("mode must be identity or published")
	}
}

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: wqpu-chainclient-smoke RPC_URL identity|published")
		os.Exit(2)
	}
	if err := run(os.Args[1], os.Args[2]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
