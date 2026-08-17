# WQPU 0.6.0-dev

WQPU is an experimental equal-peer distributed LLM network built around `llama.cpp` RPC.

The target public-network flow is:

```text
install WQPU -> connect existing wallet -> discover workers from blockchain -> wqpu>
```

Every computer runs the same peer software. There is no permanent inference coordinator. The computer that receives a user prompt temporarily coordinates only that request and can combine several reachable workers.

## One-command install

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.sh | sh
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.ps1 | iex
```

The installer downloads `wqpu.py`, `wqpu_chain.py`, `wqpu_wallet.py` and `wqpu_runtime.py`.

Until the WQPU public chain is deployed and its RPC/contract addresses are published as defaults, the installer keeps the existing private join-code mesh working automatically.

## Local blockchain devnet

For an end-to-end public-chain test, install Foundry and run:

```bash
python scripts/devnet.py 0xYOUR_EXISTING_WALLET
source .wqpu-devnet.env
wqpu
```

The script starts a local Anvil chain, deploys `WQPUToken`, `WQPURegistry` and `WQPUComputeMarket`, and optionally gives the supplied existing wallet test ETH/WQPU. The browser connector can automatically add/switch to `WQPU Devnet`.

The local devnet binds to `127.0.0.1` by default. For an isolated two-PC LAN test it can be started with `--listen-host 0.0.0.0`, but the included Anvil development key is public, so that RPC must never be exposed to the Internet or used with real funds.

## Public-chain test mode

Set a JSON-RPC endpoint and deployed `WQPURegistry` address:

```bash
export WQPU_RPC_URL='http://127.0.0.1:8545'
export WQPU_REGISTRY='0x...'
wqpu
```

On first public-mode start WQPU opens a localhost browser page. MetaMask, Rabby or another injected EVM wallet submits the node-registration transaction. WQPU never receives the seed phrase or private key.

A registered node publishes its wallet address, reachable `HOST:PORT`, TLS fingerprint and offered capacity. Registration stays on-chain; live availability and load are checked over P2P, so the wallet is not asked to approve heartbeat transactions every few minutes.

The runtime reads the registry, verifies a peer against its registered TLS fingerprint and wallet, exchanges fresh utilization data and prefers less-busy workers. `WQPU_MAX_WORKERS` controls the maximum number of remote workers used for one request; the default is 8.

## Public endpoint

By default WQPU tries to infer a local endpoint. Override it when the machine has a public hostname/IP or port forwarding:

```bash
export WQPU_PUBLIC_ENDPOINT='example.net:7443'
```

Universal NAT traversal is not solved yet. A node behind restrictive NAT may need port forwarding or a future WQPU relay/hole-punching layer before other Internet peers can use it directly.

## CLI

Public mode:

```text
wqpu> hello
wqpu> /status
wqpu> /peers
wqpu> /chain
wqpu> /wallet
wqpu> /exit
```

Legacy private mode remains available:

```bash
wqpu --legacy
wqpu --join 'WQPU1...'
```

## Model execution

Each node contributes a localhost-only `ggml-rpc-server`. For a prompt, the requester starts its own local `llama-server` and passes the selected remote RPC endpoints to llama.cpp. Distribution itself does not requantize the model; model quality is determined by the selected GGUF model/quantization.

Default model:

```text
ggml-org/gemma-3-1b-it-GGUF:Q4_K_M
```

Override it with `WQPU_MODEL`.

## Blockchain contracts

- `contracts/WQPUToken.sol` — fixed-supply WQPU token.
- `contracts/WQPURegistry.sol` — wallet/node directory, TLS fingerprint, capacity/load and one global compute price.
- `contracts/WQPUComputeMarket.sol` — escrowed payment channels. Each channel snapshots the global network price when it opens and rejects vouchers priced differently.

The blockchain stores discovery/accounting data only. Prompts, model data and llama.cpp RPC traffic remain off-chain.

## Security status

Current public-chain prototype verifies registry endpoints against their registered TLS fingerprints and never accepts wallet private keys. It is still experimental and not ready for real-value funds.

Before a public launch WQPU still needs production compute metering, EIP-712 voucher generation/claiming, NAT strategy, deployment to the chosen EVM chain, adversarial testing and contract audit.

See `ECONOMY.md` and `PUBLIC_CHAIN_TODO.md` for the current design and remaining launch blockers.
