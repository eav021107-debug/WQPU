# WQPU sovereign compute economy

This document describes the current `next-foundation` economic design. It replaces the earlier Solidity/ERC-20/provider-bid prototype.

The stable `main` branch is not changed by this design work.

## Core rule

WQPU has one native coin and one network compute price.

There is no per-provider asking price and no privileged price setter. Providers compete for work at the same current network price. The chain changes that price gradually from observed reserved demand relative to economically backed supply.

## Native WQPU

WQPU is the native asset of the sovereign WQPU chain. The compute market does not depend on an Ethereum or Solana settlement contract.

The protocol currently uses 18 native decimal places (`awqpu`). Compute-accounting payment counters use nano-WQPU precision:

```text
1 payment unit = 1e9 awqpu = 1e-9 WQPU
```

Protocol counters are bounded integers and all native conversions are checked for overflow.

## Wallets and sessions

A user keeps their normal external EVM wallet. WQPU never imports or stores the user's seed phrase or wallet private key.

The wallet signs one EIP-712 session delegation containing:

- WQPU chain ID;
- temporary session address;
- issue and expiry heights;
- lifetime spend limit;
- per-job limit;
- permission mask;
- wallet revocation nonce;
- protocol version.

The temporary secp256k1 session key lives in the WQPU process and signs background WQPU actions. Every action has its own monotonically increasing nonce and payload hash, so signatures cannot be replayed or moved to a different action.

## Devices and discovery

A wallet may own many independent computers. The wallet identifies the owner; `peer_id` identifies the actual machine.

Each peer publishes a bounded signed provider record containing its reachable WQPU endpoint, model hashes, advertised capacity, busy units, free memory, capability commitment and expiry.

Discovery comes from chain state. The target client does not require manual invite codes or a permanent coordinator.

Prompts, tensors and generated text do **not** go on-chain. They move over the WQPU P2P/inference transport.

## One global compute price

The network price moves toward a target utilization of 70%, with a maximum change of 5% per price epoch.

Demand is not provider-reported "busy" load. It is compute that the chain has actually reserved for active jobs.

Most importantly, the supply denominator is **not raw advertised capacity**.

```text
price supply of peer = min(advertised capacity, bonded capacity)
```

A provider with no bond may still be discovered and may still be scheduled, but its claimed capacity contributes zero downward pressure to the network price.

This prevents a free Sybil attack where an attacker creates many fake peers, advertises enormous capacity and forces the global price down without putting capital at risk.

## Provider capacity bond

Bond is attached to a specific `peer_id`, not only to a wallet. One bond therefore cannot back an arbitrary number of Sybil peers owned by the same wallet.

Protocol v1 currently requires one payment unit of native WQPU bond for one unit of price capacity. This is a consensus parameter and may only change through a protocol upgrade.

The current native precompile surface includes:

```text
bondProvider(bytes32 peerId, uint64 capacityUnits)
unbondProvider(bytes32 peerId, uint64 capacityUnits)
providerBondCapacity(bytes32 peerId)
providerPriceCapacity(bytes32 peerId)
bondedPriceCapacity()
```

`bondProvider` is payable and checks that the native value exactly matches the declared bonded capacity. Bond above the peer's advertised capacity is rejected.

Bond cannot be removed while that peer has reserved compute. This prevents a provider from withdrawing its price backing in the middle of accepted work.

Slashing and reputation rules are intentionally a later layer. The first security invariant is narrower: **unbacked claimed capacity cannot lower the global price**.

## Price epochs

The native WQPU precompile exposes permissionless epoch closing:

```text
closePriceEpoch()
priceEpoch()
```

Protocol v1 uses 20-block epochs. Anyone may trigger an eligible epoch close, but the same epoch cannot be applied twice. The transition uses only:

- active bond-backed price capacity;
- active chain-reserved compute demand;
- the previous global price.

The price math is cross-tested against the Go reference kernel.

## Requester escrow

Before useful work begins, a requester funds a limited WQPU session and the job reserves its maximum charge.

A session cannot reserve more than:

- its wallet-authorized lifetime spend limit;
- its per-job limit;
- its actually funded native WQPU escrow.

Concurrent jobs share the same reservation accounting, so the same funds cannot pay for two jobs at once.

Unused reservation is released when the job settles or times out.

## Provider capacity reservation

Every job names the exact peer set and reserves compute on every selected `peer_id` before inference begins.

The chain checks model compatibility, peer activity, provider ownership, free advertised capacity, model-memory assignment, session lifetime and requester escrow before committing anything.

If any check fails, the EVM snapshot is reverted and no partial reservation remains.

## Work receipts

The blockchain does not trust a provider merely claiming that it worked.

Accepted work is represented by a monotonic receipt for one job and one peer. A receipt contains only accounting/commitment data, including compute amount and a result commitment. It does not contain the prompt or generated answer.

A payable receipt requires both sides:

1. provider/session signature: "this peer performed this work";
2. requester/session signature: "this work was accepted".

Old or reordered cumulative receipts cannot increase payment.

## Settlement

Final settlement pays only accepted work at the job's locked global price.

The lifecycle is:

```text
wallet session
    -> native WQPU escrow
    -> job money + compute reservation
    -> distributed inference
    -> dual-signed receipts
    -> provider native WQPU payout
    -> unused requester reserve released
    -> peer capacity released
    -> job id permanently marked completed
```

A requester may explicitly finalize. After the deadline, timeout settlement is permissionless so a vanished client cannot leave funds or provider capacity locked forever.

## What the bond does not prove

A bond makes fake supply economically costly; it does not by itself prove that a GPU/CPU exists or that benchmark claims are truthful.

Before a public-value network, WQPU still needs adversarial work on:

- benchmark/capability verification;
- provider reputation and failure scoring;
- slashable offenses and evidence rules;
- wash-demand/self-job manipulation of the demand side;
- bond calibration against real compute economics;
- real multi-node failure and timeout testing.

Until those parts and the native transaction path are exercised end-to-end, `next-foundation` remains a development network design and should not be treated as production financial infrastructure.
