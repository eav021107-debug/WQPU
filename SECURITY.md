# WQPU security foundation

Security is the first implementation gate for the sovereign WQPU network.

## Trust model

Assume every remote peer, provider advertisement, endpoint, work claim, and network packet may be malicious. Do not trust a peer because it owns tokens, has high uptime, appears in registry state, or previously behaved correctly.

The WQPU chain is authoritative only after normal consensus verification. A bootstrap endpoint is never authoritative by itself.

## Wallet rules

- Never request, receive, log, transmit or store a wallet seed phrase or private key.
- Wallet ownership is proven by an explicit signed challenge containing chain ID, protocol version, origin, nonce and expiration.
- Long-running compute uses a local ephemeral session key only after the wallet signs a narrowly scoped delegation.
- A delegation must include wallet address, session public key, chain ID, expiry, maximum spend, maximum job value and a revocation nonce.
- Session authorization must not authorize arbitrary token transfer or arbitrary wallet messages.

## Peer identity

A provider registry entry binds:

- wallet address;
- WQPU peer public key / peer ID;
- transport endpoints;
- capability commitment;
- current capacity/busy counters;
- heartbeat height/expiry;
- protocol version.

Endpoint changes require a fresh signed update. Old presence records expire automatically.

## Transport

- Never expose raw `llama.cpp` RPC directly to the public Internet.
- All remote RPC traffic goes through an authenticated WQPU tunnel.
- Peer sessions use modern authenticated encryption and forward-secret ephemeral keys.
- Every control message carries protocol version, session ID, monotonic sequence/nonce and bounded length.
- Reject replays, stale sessions, oversized frames and unknown critical fields.
- Relays forward opaque encrypted streams and do not gain permission to inspect prompts or model data solely by relaying.

## Compute containment

- Provider-side llama.cpp RPC listens on loopback/private IPC only.
- WQPU enforces maximum RAM/VRAM, CPU share, concurrent jobs, model size and cache size before accepting work.
- Model/tensor cache entries are content-addressed and verified against model hashes before use.
- A remote peer cannot choose arbitrary local filesystem paths or execute commands.
- Runtime binaries and protocol updates must be version-pinned or signature/hash verified before execution.

## Job integrity

Every job gets a unique cryptographic job ID and immutable request manifest containing at least:

- requester wallet/session key;
- model hash/version;
- prompt commitment where privacy permits;
- selected provider set;
- assigned compute units/shards;
- global price epoch;
- maximum charge;
- creation/expiry heights.

Work receipts are bound to that job ID and provider identity. A receipt cannot be replayed into another job or another price epoch.

## Accounting

- Prompt count is never proof of work.
- Self-requesting cannot mint new supply.
- Settlement uses monotonic cumulative amounts or uniquely numbered receipts so an old receipt cannot reduce or duplicate payment.
- Arithmetic uses deterministic integers; no floating-point consensus math.
- Global price updates are bounded per epoch to prevent one sudden demand spike from multiplying price without limit.

## Abuse resistance

- Per-session and per-wallet request limits exist even if a wallet has funds.
- Peers can disconnect malformed or abusive senders without global permission.
- Scheduler distrusts self-reported availability over time and compares it with observed completion/timeout history.
- Reputation is advisory for scheduling, not a source of consensus authority.
- Future anti-Sybil economics may require refundable job/provider collateral, but the initial network must not pretend wallet count equals independent humans.

## Failure behavior

- A worker disappearing must not corrupt the chain or permanently lock a requester's funds.
- Request coordinators retry on a replacement worker from the same model-compatible pool.
- Partial work is paid only according to protocol-defined accepted receipts.
- Chain reorg/finality rules determine when a payment/provider update is considered irreversible.
- Local state writes use atomic replace/journaling where loss would affect keys, accounting or peer identity.

## Secrets and logs

Never log:

- seed phrases/private keys;
- wallet session secrets;
- raw authentication tokens;
- full prompts by default;
- decrypted peer traffic.

Logs may contain job IDs, peer IDs, timing, resource counters and sanitized error classes.

## Release gates

A feature cannot move to `main` until it has tests for malformed input, replay, timeout, peer disconnect, duplicated receipt, stale registry entry, resource exhaustion boundary and safe recovery after process restart.
