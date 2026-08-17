# WQPU compute economy

## Target UX

```text
install WQPU -> connect existing wallet -> node appears in WQPU registry -> wqpu>
```

WQPU never asks for a seed phrase or private key. The browser wallet submits transactions and signs payment vouchers.

## What the blockchain does

The chain is a shared discovery/accounting layer only. Prompts, model tensors and llama.cpp RPC traffic stay off-chain and move directly between WQPU peers.

`WQPURegistry.sol` publishes:

- wallet identity (`msg.sender`);
- reachable P2P endpoint;
- TLS certificate fingerprint;
- offered capacity;
- coarse load/heartbeat;
- one global WQPU compute price.

The runtime supplements the coarse on-chain load with fresher P2P load snapshots and prefers less-busy workers.

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
5. requester signs cumulative EIP-712 vouchers;
6. contract accepts a voucher only when its amount exactly matches the channel price and cumulative compute units;
7. provider claims the newest voucher;
8. unused deposit is refundable after expiry plus the claim grace period.

Old vouchers cannot reduce already-paid amounts, and a voucher with a different price is rejected.

## Compute units

The contracts intentionally do not decide how llama.cpp work is measured. The runtime must measure useful work consistently enough that a requester can produce a cumulative voucher for each provider.

This metering layer is the next economic-runtime milestone. Until it is implemented and adversarially tested, the contracts are test-network prototypes and should not hold real-value funds.

## Runtime integration status

Implemented in the `agent/blockchain-runtime` prototype:

- browser wallet connector without private-key custody;
- on-chain node discovery at startup;
- TLS fingerprint binding;
- one global price;
- live P2P load ranking;
- multiple reachable workers can participate in one llama.cpp request;
- legacy private join-code mode remains available.

Still required before a real public launch:

- deploy token/registry/market to the chosen WQPU EVM chain or test network;
- publish the chain RPC + contract addresses as the default network configuration;
- automatic NAT traversal / relay policy for nodes that cannot accept inbound Internet connections;
- per-provider compute metering;
- requester EIP-712 voucher generation and provider claiming;
- adversarial/security testing and contract audit.
