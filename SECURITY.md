# WQPU security foundation

Security is the first implementation gate for the sovereign WQPU network.

## Trust model

Assume every remote peer, provider advertisement, endpoint, work claim, and network packet may be malicious. Do not trust a peer because it owns tokens, has high uptime, appears in registry state, or previously behaved correctly.

The WQPU chain is authoritative only after normal consensus verification. A bootstrap endpoint is never authoritative by itself.

## Wallet rules

- Never request, receive, log, transmit or store a wallet seed phrase or wallet private key.
- Wallet ownership is proven by an explicit EIP-712 signature bound to the WQPU chain ID and protocol version.
- Long-running compute uses a local temporary secp256k1 session key only after the wallet signs a narrowly scoped delegation.
- A delegation binds the wallet, temporary EVM session address, issue/expiry heights, maximum lifetime spend, maximum job value, permission mask, revocation nonce and protocol version.
- Session action signatures include an action kind, monotonically increasing nonce and payload hash, so a signature for one action cannot be replayed or moved to another action.
- Session authorization must not authorize arbitrary token transfer or arbitrary wallet messages.

## Peer identity

A wallet may own multiple computers. Wallet identity is therefore not provider identity.

A provider registry entry binds:

- owner wallet address;
- WQPU `peer_id` for the concrete machine;
- exact temporary control-session address for that peer;
- transport endpoints;
- capability commitment;
- current capacity/busy counters;
- heartbeat height/expiry;
- protocol version.

Endpoint/capability updates require a fresh session-signed provider envelope. Old presence records expire automatically. Another wallet cannot take over an already owned `peer_id`.

## Transport

- Never expose raw `llama.cpp` RPC directly to the public Internet.
- All remote RPC traffic goes through an authenticated WQPU tunnel.
- Peer sessions use modern authenticated encryption and forward-secret ephemeral keys.
- Every control message carries protocol version, session ID, monotonic sequence/nonce and bounded length.
- Reject replays, stale sessions, oversized frames and unknown critical fields.
- Relays forward opaque encrypted streams and do not gain permission to inspect prompts or model data solely by relaying.

The authenticated P2P transport is still a release gate on `next-foundation`; chain registration alone must never be treated as transport authentication.

## Compute containment

- Provider-side llama.cpp RPC listens on loopback/private IPC only.
- WQPU enforces maximum RAM/VRAM, CPU share, concurrent jobs, model size and cache size before accepting work.
- Model/tensor cache entries are content-addressed and verified against model hashes before use.
- A remote peer cannot choose arbitrary local filesystem paths or execute commands.
- Runtime binaries and protocol updates must be version-pinned or signature/hash verified before execution.

## Job integrity

Every job gets a unique cryptographic job ID and immutable request manifest containing at least:

- requester wallet/session address;
- model hash/version;
- prompt commitment where privacy permits;
- selected provider/peer set;
- assigned compute units/model bytes;
- global price epoch and exact global price;
- maximum charge;
- creation/expiry heights.

Money and every selected peer's compute capacity are reserved before useful work starts. If any validation or reservation fails, the EVM snapshot is reverted so partial state is not left behind.

Work receipts are bound to the job ID, concrete `peer_id`, provider wallet and result commitment. A receipt cannot be replayed into another job or another price epoch.

## Accounting and settlement

- Prompt count is never proof of work.
- Self-requesting cannot mint new supply.
- Requester spend must be backed by actually funded native WQPU session escrow before a job can reserve it.
- Settlement uses monotonic cumulative receipts so an old/reordered receipt cannot reduce or duplicate payment.
- Accepted payment requires both provider/control-session and requester/session signatures.
- Only accepted work is paid; unused requester reservation is released.
- Timeout settlement is permissionless after the deadline so a vanished requester cannot permanently lock provider capacity or escrow.
- Arithmetic uses deterministic checked integers; no floating-point consensus math.

## Global-price attack model

Provider-reported `busy` is not price demand. Demand is chain-reserved compute from active jobs.

Raw advertised capacity is not price supply. For each active peer:

```text
price supply = min(advertised capacity, bonded capacity)
```

The provider bond is native WQPU and is keyed to the exact `peer_id`. One wallet cannot reuse the same bond to give many Sybil peers free downward price pressure. Bond cannot be removed while the peer has reserved work.

Global price movement is bounded per epoch. Protocol v1 targets 70% utilization, caps one price move at 5%, and uses 20-block epochs.

The bond makes fake supply economically costly, but it does **not** prove that the advertised hardware exists or that a capability benchmark is honest. Capability verification, slashing/reputation policy and bond calibration remain release gates.

The demand side also remains adversarial: funded self-jobs/wash demand may spend real fees to push utilization upward. Before public-value deployment, the protocol needs explicit analysis and mitigation for economically rational demand manipulation rather than assuming every paid job represents independent demand.

## Abuse resistance

- Per-session and per-wallet request limits exist even if a wallet has funds.
- Peers can disconnect malformed or abusive senders without global permission.
- Scheduler distrusts self-reported availability over time and compares it with observed completion/timeout history.
- Reputation may assist scheduling but is never consensus authority by itself.
- Wallet count, peer count and IP count must never be treated as counts of independent humans or independent physical machines.

## Failure behavior

- A worker disappearing must not corrupt the chain or permanently lock a requester's funds.
- Request coordinators retry on a replacement worker from the same model-compatible pool where protocol state permits it.
- Partial work is paid only according to protocol-defined accepted receipts.
- Chain reorg/finality rules determine when a payment/provider update is considered irreversible.
- Local state writes use atomic replace/journaling where loss would affect session keys, accounting or peer identity.

## Native-balance invariants

Native bond, escrow and provider payout mutate the Cosmos EVM `StateDB`. The pinned runtime commits dirty EVM account balances through the Cosmos EVM keeper into bank state. Any future runtime upgrade must re-prove this integration before release; WQPU must not assume ordinary upstream geth balance semantics are sufficient for a Cosmos EVM fork.

## Secrets and logs

Never log:

- seed phrases/private keys;
- temporary session private keys;
- raw authentication tokens;
- full prompts by default;
- decrypted peer traffic.

Logs may contain job IDs, peer IDs, timing, resource counters, public wallet/session addresses and sanitized error classes.

## Release gates

A feature cannot move to `main` until it has tests for malformed input, replay, timeout, peer disconnect, duplicated receipt, stale registry entry, resource exhaustion boundary and safe recovery after process restart.

The `next-foundation` branch additionally remains non-production until these end-to-end gates are green:

- pinned patched `wqpud` starts with the sovereign WQPU genesis;
- native `0x0900` read and signed-write JSON-RPC transactions execute on the live devnet;
- `fundSession -> reserveJob -> receipt -> finalize -> native payout/refund` succeeds and its failure paths remain atomic;
- authenticated P2P transport and multi-peer `llama.cpp` inference work under peer loss/retry;
- capability/Sybil/wash-demand economics have an explicit adversarial policy.
