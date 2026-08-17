# WQPU compute economy

## Target UX

```text
install WQPU -> connect existing wallet -> node appears in registry -> compute/earn
```

WQPU never asks for a seed phrase or wallet private key. The browser wallet performs registration and signs narrowly scoped authorizations.

## Chain role

The chain is the shared discovery/accounting layer. Prompts, tensors and llama.cpp RPC traffic stay off-chain.

`WQPURegistry.sol` stores node wallet identity, endpoint, TLS fingerprint, capacity and the single global compute price. Registration is persistent; current liveness/load is learned over P2P.

## One network price

Providers do not choose individual prices. `globalPricePerMillionUnits` is the shared network price. A payment session snapshots the price it was authorized for, so a later network-price change does not silently reprice already-authorized work.

The prototype price controller should be transferred to the chosen production governance mechanism.

## Token

`WQPUToken.sol` is fixed supply and supports EIP-2612 permit. There is no post-deployment mint function. Compute rewards move already-existing WQPU from requester escrow to provider wallets.

## Shared escrow + bounded session

The requester does not open a transaction for every provider.

1. The wallet signs an EIP-2612 permit if escrow needs funding.
2. A gas relayer submits `depositWithPermit`.
3. WQPU creates a local secp256k1 session key.
4. The wallet signs one `SpendAuthorization` containing session key, session ID, maximum amount, price and expiry.
5. A relayer activates the session; the contract reserves that maximum from requester escrow.
6. The requester can now sign cumulative provider vouchers locally without wallet popups.
7. Any relayer may submit a valid claim, but the contract can transfer tokens only to the provider encoded in the voucher.
8. Unused reserved escrow is released after session expiry plus claim grace.

A compromised local session key is therefore limited by its on-chain maximum, price and expiry; it cannot transfer arbitrary wallet assets.

## Provider vouchers

Provider vouchers are cumulative. Replaying an old voucher cannot pay twice, cumulative units cannot move backwards, session/provider totals are tracked independently, and a voucher priced differently from the activated session is rejected.

Workers keep only the newest valid cumulative voucher per requester/session. Voucher delivery uses the WQPU control network with ACK/retry so temporary route loss does not silently destroy a payment promise.

## Metering

WQPU meter v2 parses the pinned llama.cpp RPC graph and estimates scalar work from tensor shapes. Matrix multiplication and flash attention use shape-aware estimates; malformed, partial or unknown protocol streams fail closed.

The worker also meters incoming work independently. Optional payment enforcement can refuse additional paid compute until previous measured work is covered.

This meter is still experimental rather than a formally fraud-proof FLOP oracle. Real-value automatic settlement should remain disabled until the chosen public network has completed adversarial testing/audit and explicitly accepts this meter version.

## Gas relayer

The HTTP relayer can submit permit funding, session activation and provider claims. Relayer gas sponsorship does not grant custody of requester/provider WQPU: signed data and contract rules fix the authorized amount and payout address.

Production relayers should use a dedicated signer/HSM, rate limits and redundant endpoints.

## Current status

Implemented in WQPU 0.6.0:

- public blockchain discovery;
- wallet/TLS node identity;
- one global price;
- shared escrow;
- permit funding;
- bounded local session authorization;
- automatic cumulative voucher signing;
- replay/limit/expiry protection;
- relayed claims;
- provider voucher inbox/outbox;
- NAT/CGNAT transport relay;
- meter v2;
- Linux/Windows installers;
- local Anvil deployment and end-to-end CI.

What remains before real public-value use is external deployment and security work: publish the actual WQPU chain/testnet + relays + funded gas relayer, run Internet-scale/adversarial tests, and complete an independent audit.
