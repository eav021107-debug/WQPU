# WQPU sovereign network — target architecture v1

This document is the source of truth for the next WQPU architecture. The current `0.5.3` implementation remains untouched while this is built and tested separately.

## Product goal

A new user should ultimately do only this:

```text
run one installer command
-> connect an existing wallet
-> WQPU reads the live WQPU chain state
-> WQPU discovers available compute peers
-> wqpu>
```

No invite codes, manual IPs, permanent coordinator, privileged compute node, Ethereum, Solana, or another external settlement chain.

## Corrected 18-point plan

1. **One WQPU client.** The same client can originate prompts, contribute compute, relay authenticated WQPU traffic, read chain state, and submit chain transactions.
2. **External wallet only.** WQPU never creates a user wallet automatically. A user connects an existing supported wallet and proves control with a signature.
3. **Never request seed/private keys.** WQPU receives public address + signatures only. For background operation the wallet may authorize a limited local session key with explicit expiry and spending limits.
4. **Sovereign WQPU chain.** WQPU balances, provider presence, work receipts, payments, protocol parameters and global compute price live on the WQPU network itself, not Ethereum/Solana.
5. **Permissionless membership.** No account database and no privileged WQPU compute operator. Any valid wallet can publish a provider record and participate under the same protocol rules.
6. **Chain-backed discovery.** Active provider records are part of chain state. The client reads this state to learn which wallets/peer IDs currently advertise reachable WQPU endpoints and capacity. Presence expires unless refreshed.
7. **Equal compute peers.** A provider has no special authority because it is discoverable, highly available, or relays traffic. Consensus authority and compute scheduling are separate concepts.
8. **Request-local coordinator.** The computer where a prompt originates coordinates only that request. There is no permanent LLM coordinator.
9. **One network compute price.** Providers do not set individual prices. A deterministic protocol rule calculates one price for the current chain epoch from aggregate demand and available capacity, with bounded rate changes.
10. **Least-busy scheduling.** For a request, the coordinator selects compatible providers primarily by lowest utilization, while enforcing model/shard compatibility, enough free RAM/VRAM, health, and a small latency tie-breaker.
11. **One request uses multiple computers.** WQPU should not assume one provider contains the entire model. Model tensors/shards are placed across multiple workers and the local coordinator assembles one inference graph across them.
12. **Cache model tensors near compute.** Providers keep authenticated local tensor caches so repeated requests do not repeatedly transfer the same model data. Cache entries are content-addressed and model-hash verified.
13. **Fast path first.** Prefer direct peer paths. Relays are fallback only. Reuse secure sessions, batch control messages, avoid on-chain operations in the token-generation hot path, and use local tensor caches.
14. **Pay for verified useful work.** Compensation is based on signed job/work receipts tied to a job ID, model hash, assigned work, completion evidence and measured compute units—not merely number of prompts.
15. **No per-prompt minting.** A user cannot manufacture currency by sending themselves fake prompts. Normal request payment transfers existing WQPU value. Any future protocol emission must be separately specified and rate-limited by consensus.
16. **Safety before UI.** Authentication, encryption, replay protection, resource limits, sandbox boundaries, malformed-peer handling, accounting correctness and recovery are completed before polishing the interface.
17. **Backward-safe staged rollout.** New protocol work stays separate from `0.5.3` until each stage passes compatibility and failure tests. Upgrade gates are explicit protocol versions, not silent behavior changes.
18. **Every stage has a binary test.** Tests progress in this order: wallet signature -> chain state -> provider heartbeat -> discovery -> secure P2P -> two-worker model split -> scheduler -> work receipt -> test payment -> failure/retry -> multi-request load -> public testnet.

## Important physical bootstrap rule

The blockchain can be the authoritative list of online WQPU providers, but a brand-new machine still needs a network transport path to obtain the first chain blocks/headers. This is a networking requirement, not a privileged-server requirement.

WQPU will keep consensus and compute permissionless. Bootstrap transport must therefore be replaceable and non-authoritative: cached peers, LAN discovery, and deterministic network peer-discovery mechanisms can provide the first connection, but none of them defines chain truth. Chain consensus does.

## Distributed inference model

The target data path is:

```text
prompt on node A
  -> A reads provider state + current global price
  -> A chooses a set of least-busy compatible workers
  -> model tensors are mapped across A/B/C/... according to available memory
  -> authenticated WQPU tunnels carry llama.cpp RPC traffic
  -> one inference result returns to A
  -> signed work receipts are produced
  -> settlement happens outside the token hot path
```

The coordinator does not need every model tensor resident in its RAM. A local model manifest and content hashes are enough to plan placement; tensor data may be cached or loaded on remote workers.

## Layers

1. **Identity:** external wallet signatures + limited session delegation.
2. **Chain:** blocks, balances, provider registry, protocol parameters, work/payment records.
3. **Discovery:** read active providers from chain state.
4. **Secure transport:** encrypted authenticated peer sessions, NAT/path handling, relays only as fallback.
5. **Compute:** llama.cpp RPC behind WQPU transport, model/tensor caching, capability reporting.
6. **Scheduler:** global price + least-busy compatible worker selection.
7. **Accounting:** signed work receipts, replay protection, settlement.
8. **CLI/UI:** one installer, wallet-connect flow, then `wqpu>`.

## Non-goals for the foundation phase

- no real-money launch;
- no exchange listing logic;
- no automatic seed phrase generation;
- no public unauthenticated llama.cpp RPC exposure;
- no promise that a blockchain alone can discover the Internet without an initial transport connection;
- no merge into `main` until foundation tests exist.
