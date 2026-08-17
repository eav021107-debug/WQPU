# WQPU 0.6.0

WQPU is an experimental equal-peer distributed LLM network built around a pinned `llama.cpp` RPC protocol and an EVM accounting layer.

Target public-network UX:

```text
install WQPU -> connect existing wallet -> node joins automatically -> wqpu>
```

Every computer runs the same peer software. A computer that receives a prompt coordinates only that request and may combine several less-busy workers. There is no permanent inference coordinator.

## Install

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.sh | sh
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.ps1 | iex
```

Useful checks:

```bash
wqpu --version
wqpu doctor
wqpu claim
```

The installers are exercised in CI on Linux and Windows.

## Public-network flow

When `network-config.json` contains a published WQPU network, WQPU:

1. opens a localhost browser wallet connector;
2. connects an existing EVM wallet — WQPU never receives the seed phrase/private key;
3. registers the node wallet, endpoint, capacity and TLS fingerprint on-chain;
4. discovers other nodes from `WQPURegistry`;
5. connects directly or through TLS-pinned bootstrap relays when NAT/CGNAT blocks inbound traffic;
6. ranks live workers by load/capacity;
7. starts a local `llama-server` and distributes work through selected `ggml-rpc-server` workers.

The blockchain stores discovery/accounting state only. Prompts, model data and llama.cpp RPC traffic stay off-chain.

## Model runtime

WQPU pins `llama.cpp` to release `b10456` so every node speaks the same RPC protocol. WQPU also pins the exact supported CPU/GPU release assets and SHA-256 values so switching accelerator backends cannot silently switch RPC versions.

Default model:

```text
ggml-org/gemma-3-1b-it-GGUF:Q4_K_M
```

Override it with `WQPU_MODEL`.

### CPU / GPU workers

The default is `WQPU_ACCEL=auto`; normally there is nothing to configure. WQPU selects a pinned backend build for the machine and no longer forces `ggml-rpc-server --device CPU`:

- macOS uses the Metal-capable pinned build;
- Windows x64 with a compatible NVIDIA driver uses the CUDA 12.4 build and its pinned CUDA runtime bundle;
- Windows can fall back to Vulkan, then CPU;
- Linux x64/arm64 uses the pinned Vulkan build when a real Vulkan GPU path is available, otherwise CPU;
- CUDA workers advertise detected VRAM as provider capacity; accelerator metadata is also included in the live node status.

Manual overrides are available for troubleshooting or deterministic deployments:

```bash
WQPU_ACCEL=cpu wqpu
WQPU_ACCEL=vulkan wqpu
WQPU_ACCEL=metal wqpu
WQPU_ACCEL=cuda12 wqpu
WQPU_RPC_DEVICE=CUDA1 wqpu
```

`wqpu doctor` shows the selected accelerator, runtime variant and detected VRAM. WQPU still forces llama.cpp RPC traffic to remain TCP inside the authenticated/metered WQPU TLS tunnel; a GPU backend does not bypass node identity, NAT relay or dual-meter accounting.

## Wallet and payments

The public runtime implements:

- fixed-supply `WQPUToken`;
- one global compute price for all users;
- shared requester escrow;
- EIP-2612 permit funding;
- a local secp256k1 session key authorized once by the wallet with a maximum spend, price and expiry;
- on-chain reservation of the session limit before vouchers can be used;
- cumulative per-provider EIP-712 vouchers;
- replay protection and session/provider cumulative accounting;
- HTTP gas relayer for permit funding, session activation and provider claims;
- provider payouts that can only go to the provider wallet encoded in the voucher;
- durable voucher inbox/outbox and retry/ACK routing.

Provider payout:

```bash
wqpu claim --submit
```

The network operator can enable automatic voucher issuance from `network-config.json` with `payments_enabled=true`; environment variables remain available for development overrides.

## Compute metering

Meter v2 parses the pinned llama.cpp serialized RPC graph. It estimates scalar work from tensor shapes; matrix multiplication and flash attention receive shape-aware estimates instead of treating every graph node equally. Malformed, partial or protocol-mismatched streams fail closed and cannot create a voucher.

Real llama.cpp uses multiple physical RPC sockets for one logical request. WQPU meters each socket independently, verifies each worker-signed report against the provider wallet's on-chain TLS fingerprint, and aggregates only verified reports before creating a voucher.

This is still experimental accounting, not a formally fraud-proof FLOP oracle. Real-value automatic payment should only be enabled after adversarial testing and audit of the selected public deployment.

## NAT / relay

A node behind a home router or CGNAT can keep an outbound TLS connection to a configured WQPU bootstrap relay. A requester can reach that worker through the relay without opening an inbound router port.

The relay is transport only. Worker wallet identity and TLS fingerprint are still checked against the blockchain registry. CI includes a three-node integration test:

```text
requester -> relay -> outbound-only worker
```

with a real bidirectional RPC byte stream.

## Local EVM devnet

Install Foundry, then run:

```bash
python scripts/devnet.py 0xYOUR_EXISTING_WALLET
source .wqpu-devnet.env
wqpu
```

This starts Anvil and deploys:

```text
WQPUToken -> WQPURegistry -> WQPUComputeMarket
```

The test suite exercises the full gasless flow:

```text
permit -> escrow funding -> session reservation -> provider voucher -> HTTP relayer -> provider balance
```

Never expose the included Anvil development keys/RPC to the public Internet or use them with real funds.

## Legacy private mesh

Until public RPC/contracts/relays are published in `network-config.json`, the normal installer keeps the private mesh working:

```bash
wqpu --legacy
wqpu --join 'WQPU1...'
```

## Contracts

- `contracts/WQPUToken.sol` — fixed-supply ERC-20 + EIP-2612 permit.
- `contracts/WQPURegistry.sol` — node wallet/endpoint/TLS/capacity directory + one global price.
- `contracts/WQPUComputeMarket.sol` — shared escrow, bounded sessions, vouchers, claims and refunds.

## Validation

GitHub Actions covers:

- Python 3.8 and 3.11;
- unit/adversarial parser and payment tests;
- CPU/GPU backend selection and pinned runtime manifest tests;
- Linux installer;
- Windows installer;
- Solidity compilation and Foundry tests;
- local Token -> Registry -> Market deployment;
- Python -> EVM registry round-trip;
- OpenSSL wallet/session signatures;
- HTTP-relayed permit/session/provider-payment round-trip;
- replay rejection/reserved-session accounting;
- requester -> relay -> NAT worker integration;
- real pinned Gemma inference through one measured/paid worker;
- one real Gemma request split across two independently metered and paid WQPU workers.

A physical CUDA/Metal/Vulkan runner is still required to benchmark actual GPU execution; hosted CI validates accelerator selection and preserves the real CPU multiworker regression path.

## Deployment boundary

WQPU 0.6.0 contains the complete public-network prototype code path. A truly zero-configuration Internet-wide launch additionally requires external infrastructure that cannot be embedded into a Git repository by itself:

- deploy the chosen WQPU EVM chain/testnet;
- publish RPC + contract addresses in `network-config.json`;
- operate at least one public TLS-pinned transport relay;
- operate at least one funded gas relayer;
- complete security/adversarial review and an independent contract/network audit before real-value use.

See `PUBLIC_CHAIN_TODO.md` for the deployment checklist and `ECONOMY.md` for the payment model.