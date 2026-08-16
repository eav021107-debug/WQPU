# WQPU

**One LLM across several home computers.**

WQPU is a small experimental launcher around the `llama.cpp` RPC backend. Run the same installer on several computers on the same trusted local network. The nodes discover each other, elect one coordinator, and run one `llama-server` that can offload parts of the model to the other computers.

## Windows: one command

Open **PowerShell** and run this on every computer:

```powershell
irm https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.ps1 | iex
```

Keep that PowerShell window open while the computer is contributing resources.

## Linux / macOS: one command

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.sh | sh
```

## What it does

- detects CPU count and RAM automatically;
- uses about 50% of logical CPU threads by default;
- runs inference processes at lower priority;
- keeps a large RAM safety margin (`llama.cpp --fit`);
- downloads an official prebuilt `llama.cpp` release automatically;
- finds other WQPU nodes by LAN broadcast;
- starts `ggml-rpc-server` on workers;
- starts one Web UI + OpenAI-compatible `llama-server` on the elected coordinator;
- restarts the coordinator automatically when nodes join/leave so the model can be redistributed.

The first run uses `ggml-org/gemma-3-1b-it-GGUF:Q4_K_M` only as a small test model. You can switch to a larger GGUF model later.

## Commands

```text
wqpu start
wqpu status
wqpu doctor
wqpu model
wqpu model <huggingface-user/repo:quant>
wqpu update
wqpu stop
```

Example:

```text
wqpu model ggml-org/GLM-4.7-Flash-GGUF:Q4_K_M
```

## Resource limits

By default WQPU reserves roughly half the CPU threads for your normal work and asks llama.cpp to retain at least 30% of RAM (minimum 4 GiB) as headroom. These are best-effort limits rather than a hard virtual-machine quota.

Temporary CPU override:

```powershell
$env:WQPU_CPU_FRACTION="0.35"
wqpu start
```

or on Linux/macOS:

```bash
WQPU_CPU_FRACTION=0.35 wqpu start
```

## Network

Default ports:

- UDP `51111` — WQPU discovery
- TCP `50052` — llama.cpp RPC
- TCP `8080` — coordinator Web UI / API

Ethernet is strongly preferred to Wi-Fi for large models.

## Important security note

`llama.cpp` currently describes its RPC backend as experimental/proof-of-concept and insecure. **Use WQPU only on a trusted private LAN. Never expose TCP 50052 to the public Internet.**

## How the split works

`llama.cpp` can distribute model weights and KV cache across local and RPC devices. WQPU does not create several independent copies of the chatbot. It launches one coordinator inference graph and remote worker devices. Distribution itself does not add an extra quantization step; the quality is determined by the GGUF model/quantization you choose.

## MVP limitations

This first version intentionally prioritizes reliable mixed-PC setup over maximum speed. Windows/Linux use the official CPU build so different GPU brands/drivers do not break first-run setup. CPU load is limited; GPU acceleration can be added as a next layer after the cluster is confirmed working.
