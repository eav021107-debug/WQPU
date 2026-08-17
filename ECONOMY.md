# WQPU compute economy

## Target UX

```text
install WQPU -> connect existing wallet -> node appears in WQPU registry -> wqpu>
```

WQPU never asks for the user's wallet seed phrase or private key. The browser wallet submits registration and funding transactions itself.

For fully automatic per-request settlement, the remaining design is a narrowly scoped local session key authorized once by the wallet. That session key must be able to sign compute vouchers only; it must not be able to transfer arbitrary wallet funds. This session authorization is not implemented yet.

## What the blockchain does

The chain is a shared discovery/accounting layer only. Prompts, model tensors and llama.cpp RPC traffic stay off-chain and move directly between WQPU peers.

`WQPURegistry.sol` publishes:

- wallet identity (`msg.sender`);
- reachable P2P endpoint;
- TLS certificate fingerprint;
- offered capacity;
- coarse load at registration/update time;
- one global WQPU compute price.

Registration is persistent. The runtime determines real liveness and current load over direct P2P connections, so a user's wallet is not asked to approve periodic heartbeat transactions.

## One network price

All users use the same compute price. Providers do not set independent prices.

`WQPURegistry.globalPricePerMillionUnits` is the network price. A payment channel snapshots that value when it opens, so a price update cannot change the cost of work already in progress.

The prototype currently has a `priceController`. On a production WQPU chain that authority must be transferred to the chosen chain-governance mechanism; it must not remain a privileged application server.

## Token

`WQPUToken.sol` is fixed supply. There is no post-deployment mint function. Compute rewards transfer already-existing WQPU from requesters to providers; fake requests cannot mint new WQPU.

## Payment channels

`WQPUComputeMarket.sol` uses escrowed cumulative vouchers:

1. requester chooses a provider;
2. requester opens a channel and deposits WQPU;
3. the channel snapshots the current global price;
4. provider performs metered work;
5. requester/session signer signs cumulative EIP-712 vouchers;
6. contract accepts a voucher only when its amount exactly matches the channel price and cumulative compute units;
7. any relayer may submit the valid voucher, but the contract always pays the channel's provider wallet;
8. unused deposit is refundable after expiry plus the claim grace period.

Old vouchers cannot reduce already-paid amounts, replayed vouchers cannot pay twice, and a voucher with a different price is rejected.

## Compute units

The contracts intentionally do not decide how llama.cpp work is measured. The runtime must measure useful work consistently enough that a requester can produce a cumulative voucher for each provider.

This metering layer is the next economic-runtime milestone. Until it is implemented and adversarially tested, the contracts are test-network prototypes and should not hold real-value funds.

## Runtime integration status

Implemented in the `agent/blockchain-runtime` prototype:

- browser wallet connector without wallet-key custody;
- on-chain node discovery;
- persistent wallet/endpoint/TLS identity;
- TLS fingerprint verification;
- one global compute price;
- live P2P load ranking;
- multiple reachable workers can participate in one llama.cpp request;
- relayer-friendly provider claims;
- publishable `network-config.json` for eventual zero-config public installs;
- reproducible local Anvil devnet with automatic contract deployment;
- Linux and Windows one-command installer smoke tests;
- real Python registry-client round-trip against deployed Solidity contracts;
- legacy private join-code mode remains available.

Still required before a real public launch:

- choose/deploy the production WQPU EVM chain or test network;
- publish chain RPC + contract addresses in `network-config.json`;
- automatic NAT traversal / relay policy for nodes that cannot accept inbound Internet connections;
- verifiable per-provider compute metering;
- one-time session authorization + automatic EIP-712 voucher signing;
- relayer policy/infrastructure for gasless claims;
- adversarial/security testing and contract audit.
