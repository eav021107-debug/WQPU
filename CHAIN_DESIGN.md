# WQPU chain design

## Decision

WQPU will be a sovereign application-specific L1. It will not deploy its token or settlement contracts onto Ethereum, Solana, or another external chain.

For the consensus/execution foundation, use **CometBFT + Cosmos SDK**, with **Cosmos EVM compatibility enabled only as a wallet/tooling interface**. These are software components, not external settlement networks. WQPU controls its own genesis, validator set, native coin, fees, protocol rules, upgrades and chain state.

This avoids implementing Byzantine consensus, transaction ordering, validator slashing, state commitment and EVM wallet compatibility from scratch.

## Initial chain identity

Development/test values:

```text
chain-id: wqpu-dev-1
native display denom: WQPU
native base denom: uwqpu
1 WQPU = 1,000,000 uwqpu
```

Production genesis and final denomination parameters are deliberately not fixed yet.

## External-chain independence

The first public WQPU network should launch with:

- no dependency on Ethereum/Solana for settlement;
- no required bridge;
- no required oracle for the internal compute price;
- no required IBC route;
- no external token contract;
- no external validator set.

Optional bridges/interoperability may be added later but cannot be required for normal inference or payment.

## Wallet connection

Users connect an existing wallet. WQPU never generates or imports a seed phrase.

The wallet performs a one-time explicit authorization for a short-lived WQPU session key. That delegation is verified by the WQPU chain application and has strict expiry/spend/job limits.

The local WQPU client then uses the delegated session key for background provider heartbeats, job reservations and bounded micropayment settlement without repeatedly asking the wallet to pop up for every token or tensor operation.

## Native WQPU coin

WQPU is the native denomination of the WQPU chain, not an ERC-20 deployed on a foreign network.

Normal inference does not mint coins. It transfers WQPU value from requester to providers according to accepted work receipts.

Initial supply/distribution and any future protocol emissions require a separate economics specification and cannot be silently added to compute accounting.

## Custom chain module: x/wqpu

The WQPU-specific application module owns the following consensus state.

### 1. Session delegations

Keyed by wallet + session public key:

- expiry height;
- maximum total spend;
- maximum single job value;
- revocation nonce;
- permissions bitset.

### 2. Provider registry

Keyed by wallet/peer ID:

- WQPU peer public key;
- reachable transport endpoints;
- protocol version;
- model/capability commitments;
- advertised resource capacity;
- signed local load telemetry;
- heartbeat/expiry height.

Provider entries expire automatically. The registry is the authoritative chain view of who is currently advertising compute.

### 3. Job reservations

Before paid compute starts, the requester creates a bounded reservation containing:

- job ID;
- model hash;
- global price epoch;
- maximum compute units/charge;
- chosen providers;
- reserved capacity per provider;
- timeout height.

Reservations are what the chain uses as the authoritative demand/load floor. A provider cannot move the global price merely by claiming to be busy.

### 4. Global compute price

There is one price for the whole WQPU network per epoch.

The price controller uses chain-confirmed demand/reservations and active capacity, integer-only deterministic arithmetic, a target utilization and a strict maximum percentage move per epoch.

Provider-specific asking prices do not exist in v1.

### 5. Work receipts

Receipts bind:

- job ID;
- provider wallet/peer ID;
- receipt sequence;
- accepted compute units;
- cumulative compute;
- cumulative payment;
- result/work commitment.

Old receipts cannot be replayed to duplicate payment.

### 6. Settlement

Settlement transfers native WQPU according to valid accepted receipts and releases reservations on completion/timeout.

Token-by-token generation stays off-chain; the chain sees reservations and aggregated/cumulative settlement so inference is not blocked on one transaction for every generated token.

## Consensus versus compute equality

Compute peers are equal: any node can originate a request or offer resources under the same rules.

Blockchain consensus has a validator set because a Byzantine-fault-tolerant chain requires nodes to agree on one ordered state. Validator status does **not** give a node privileged access to prompts, scheduling, compute jobs or pricing. Compute scheduling remains independent and permissionless.

The intended public validator policy is open staking/delegation rather than a hard-coded privileged operator set. Exact production staking/slashing parameters come after the local devnet is stable.

## Chain state is discovery truth, not magic first-contact transport

Once a WQPU client has synchronized chain state, it can discover active compute providers entirely from that state.

A completely new Internet host still needs a transport path to synchronize its first chain headers/peers. That bootstrap path has no authority over chain truth and must be replaceable. The production bootstrap mechanism will be designed separately and must not create a permanent privileged compute server.

## Implementation order

1. deterministic Python reference state machine and tests;
2. define protobuf/message/state schema for `x/wqpu`;
3. create local sovereign chain devnet;
4. implement wallet session delegation verification;
5. implement provider registry + expiry;
6. implement reservation-backed global price;
7. implement work receipts/settlement;
8. connect Python WQPU client to chain queries/transactions;
9. secure peer transport;
10. distributed llama.cpp worker scheduling;
11. multi-node failure/load tests;
12. only then integrate one-command installer + wallet-connect UX.
