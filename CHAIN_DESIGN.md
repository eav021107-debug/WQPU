# WQPU chain design

## Decision

WQPU is a sovereign application-specific L1. It does not deploy its token or settlement onto Ethereum, Solana, or another external chain.

Consensus/execution uses **CometBFT + Cosmos SDK + Cosmos EVM** as open-source components. WQPU controls its own genesis, validator set, native coin, fees, protocol rules, upgrades and chain state.

WQPU-specific consensus state lives in `x/wqpu`. Wallet-facing access is exposed through a **native WQPU EVM precompile** added to `wqpud`, rather than a separately deployed upgradeable smart contract. This keeps one authoritative implementation of scheduling/payment rules while allowing ordinary EVM-compatible wallets to interact with the chain.

The WQPU precompile has no owner/admin role. It is simply a deterministic interface into `x/wqpu` state. Its address is part of the WQPU protocol and cannot silently change per validator.

This avoids implementing Byzantine consensus, transaction ordering, validator slashing, state commitment and wallet compatibility from scratch.

## Initial chain identity

Development/test values:

```text
chain-id: wqpu-dev-1
EVM chain-id: 711711
native display denom: WQPU
native base denom: awqpu
1 WQPU = 1,000,000,000,000,000,000 awqpu
```

The 18-decimal base denomination keeps the native WQPU coin compatible with common EVM-wallet amount conventions. Users see `WQPU`; `awqpu` is internal base-unit accounting.

Production genesis parameters are deliberately not fixed yet.

## External-chain independence

The first public WQPU network launches with:

- no dependency on Ethereum/Solana for settlement;
- no required bridge;
- no required oracle for the internal compute price;
- no required IBC route;
- no external token contract;
- no external validator set.

Optional bridges/interoperability may be added later but cannot be required for normal inference or payment.

## Wallet connection

Users connect an existing wallet. WQPU never generates or imports a user seed phrase or user private key.

The wallet explicitly authorizes a short-lived local WQPU session key. The signed delegation is bound to both WQPU chain ID and EVM chain ID, has a revocation nonce, expiry, maximum total spend, maximum single-job spend and permission bits.

The chain cryptographically recovers the authorizing wallet from the EIP-712 signature before any session state is created. Changing the wallet, chain, limits or session key invalidates that signature.

The session key is not the user's wallet key. The current client foundation keeps it only in process memory; closing WQPU destroys it. A future persistent session may use an OS keystore, but WQPU will not invent its own plaintext key store.

## Native WQPU coin

WQPU is the native denomination of the WQPU chain, not an ERC-20 deployed on a foreign network.

Normal inference does not mint coins. It transfers existing WQPU value from requester to providers according to accepted work receipts.

Initial supply/distribution and any future protocol emissions require a separate economics specification and cannot be silently added to compute accounting.

## `x/wqpu` consensus state

### 1. Session delegations

Keyed by wallet + session public key:

- chain ID and protocol version;
- expiry height;
- maximum total spend;
- maximum single job value;
- revocation nonce;
- permissions bitset;
- currently reserved and already settled spend.

Payment capacity is reserved before work begins, so one session cannot promise the same balance to several concurrent jobs.

### 2. Provider registry

Keyed by wallet/peer ID:

- WQPU peer identity;
- reachable transport endpoints;
- protocol version;
- model/capability commitments;
- advertised resource capacity;
- signed local load telemetry;
- heartbeat/expiry height.

Provider entries expire automatically. Duplicate peer IDs cannot be claimed by another wallet. The registry is the authoritative chain view of currently advertised compute.

### 3. Job reservations

Before paid compute starts, the requester creates a bounded reservation containing:

- job ID;
- model hash;
- global price epoch;
- maximum compute units/charge;
- chosen providers;
- reserved capacity per provider;
- timeout height.

Maximum payment and provider capacity are reserved **before** work starts. A failed reservation changes no state.

Reservations are the authoritative demand/load floor used by the global price. A provider cannot raise the network price simply by claiming to be busy.

### 4. Global compute price

There is one price for the whole WQPU network per epoch.

The controller uses chain-confirmed demand/reservations and active capacity, integer-only deterministic arithmetic, a target utilization and a strict maximum percentage move per epoch.

Provider-specific asking prices do not exist in v1.

### 5. Work receipts

Receipts bind:

- job ID;
- provider wallet/peer ID;
- strictly increasing sequence;
- accepted compute units;
- cumulative compute;
- cumulative payment at the immutable global job price;
- result/work commitment.

A job cannot be finalized by supplying an arbitrary charge. Normal finalization is derived only from the latest verified receipts. Replayed or price-manipulated receipts are rejected.

If a job times out, providers with already accepted receipts are still paid for that accepted work; unused requester payment and unused compute reservations are released.

### 6. Settlement

Settlement transfers native WQPU according to verified receipts and releases reservations atomically.

Token-by-token generation stays off-chain; the chain sees bounded reservations and cumulative settlement so inference is not blocked on a transaction for every generated token.

## Native WQPU EVM precompile

The wallet-facing precompile will be registered as an additional static precompile in `wqpud`. It will expose only narrow protocol operations such as:

- read global price;
- read active providers;
- authorize/revoke a wallet session;
- inspect a session/job/settlement.

Background compute messages remain signed by the limited WQPU session identity and are validated against `x/wqpu` state. The precompile does not become a coordinator, registry server or privileged actor.

## Consensus versus compute equality

Compute peers are equal: any node can originate a request or offer resources under the same rules.

Blockchain consensus has a validator set because a Byzantine-fault-tolerant chain requires nodes to agree on one ordered state. Validator status does **not** give a node privileged access to prompts, scheduling, compute jobs or pricing. Compute scheduling remains independent and permissionless.

The intended public validator policy is open staking/delegation rather than a hard-coded privileged operator set. Exact production staking/slashing parameters come after the local devnet is stable.

## Chain state is discovery truth, not magic first-contact transport

Once a WQPU client has synchronized chain state, it discovers active compute providers entirely from that state. There are no manual `/invite` codes in the target UX.

A brand-new Internet host still needs a transport path to synchronize its first chain headers/peers. That bootstrap path has no authority over chain truth and must be replaceable. Production chain bootstrap is separate from compute-provider discovery and must not create a permanent privileged compute server.

## Implementation order

1. deterministic reference state machine and tests;
2. sovereign WQPU devnet + pinned runtime;
3. wallet session EIP-712 verification;
4. provider registry + expiry;
5. reservation-backed single global price;
6. receipt-only settlement + timeout fairness;
7. native WQPU precompile + `wqpud` integration;
8. single Go WQPU client + local Connect Wallet;
9. chain-driven provider discovery;
10. secure peer transport;
11. distributed llama.cpp scheduling across multiple workers;
12. multi-node failure/load tests;
13. one-command release installer.
