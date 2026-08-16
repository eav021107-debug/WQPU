# WQPU compute economy prototype

This document defines the first economic layer for WQPU. It is deliberately separate from the current networking core until the two-peer transport is stable.

## Goal

A user should eventually do only this:

```text
install WQPU -> connect wallet -> wqpu>
```

No WQPU account database and no privileged WQPU server are required.

## Identity

The wallet address is the public identity of a WQPU participant.

WQPU must never ask for a seed phrase or private key. Wallet ownership is proven by signing messages in the wallet. Compute vouchers use EIP-712 typed signatures so the wallet can show structured data instead of an opaque private-key operation.

## Discovery

`WQPURegistry.sol` is a permissionless public directory.

Every participant may publish:

- wallet address (implicitly `msg.sender`);
- reachable P2P endpoint;
- asking price per million compute units;
- currently offered capacity.

There is no privileged registry operator. A newly installed client can read the same registry state as every other client and then connect directly to peers.

The blockchain is only a shared discovery/accounting layer. LLM tensors and prompts must continue to move directly through the WQPU P2P network, not through the blockchain.

## Token

`WQPUToken.sol` is a fixed-supply ERC-20 prototype.

There is no post-deployment mint function. Therefore fake requests cannot manufacture new WQPU. Payment moves existing WQPU from requesters to providers.

Initial distribution is intentionally not fixed in the protocol yet. The constructor receives an initial holder so test deployments can choose a test treasury, DAO, faucet, or other distribution mechanism without changing the token code.

## Price

There is no central price setter.

Each compute provider publishes an asking price. A requester can choose cheaper providers first, subject to latency, model compatibility, capacity and reputation.

That creates the desired feedback loop:

```text
high demand + little free compute
        -> providers can ask more
        -> contributing compute becomes more attractive
        -> more compute appears
        -> competition pushes prices down
```

The external fiat/exchange price of WQPU is separate from the internal compute price and, if a market exists, is determined by that market.

## Payment channels

Putting every token-generation step on-chain would be too slow and expensive for inference. `WQPUComputeMarket.sol` therefore uses escrowed payment channels:

1. requester selects a provider;
2. requester deposits WQPU into a channel;
3. provider performs small pieces of work;
4. requester signs increasing cumulative EIP-712 vouchers;
5. provider periodically claims the newest voucher on-chain;
6. unused deposit can be refunded after channel expiry plus a claim grace period.

A voucher contains cumulative payment and cumulative compute units. Replaying an old voucher cannot reduce the amount already paid.

A requester sending work to their own machines does not create tokens: it only moves already-existing tokens.

## Compute units

The smart contract intentionally does not define how compute is measured. That belongs to the WQPU transport/runtime layer.

The production protocol must pay for verifiable useful work, not simply for "number of requests". Otherwise fake/self-generated requests would be an obvious farming attack.

The next runtime milestone is a metering protocol that attributes actual llama.cpp RPC work to each peer and produces signed cumulative vouchers while a request is running.

Until that metering is implemented and audited, this economic layer is a prototype and must not be used with real-value funds.

## Contracts

- `contracts/WQPUToken.sol` — fixed-supply token.
- `contracts/WQPURegistry.sol` — permissionless peer/price/capacity directory.
- `contracts/WQPUComputeMarket.sol` — escrow + cumulative signed compute vouchers.

## Integration order

1. Stabilize two-peer WQPU networking.
2. Deploy contracts to an EVM test network.
3. Add browser wallet connection to the CLI; never accept seed phrases.
4. Read peer discovery from `WQPURegistry` automatically at startup.
5. Add provider bids and automatic provider selection.
6. Add runtime compute metering and EIP-712 vouchers.
7. Test adversarial cases before any main-network deployment.
