# WQPU 0.5.3

WQPU is an experimental **equal-peer distributed LLM network** built around `llama.cpp` RPC.

There is no permanent coordinator and no dedicated relay program. Every computer runs the same `wqpu.py` and has the same rights and functions.

```text
Mac <----> Windows <----> Linux/VPS <----> other WQPU peers
  \____________ equal-peer WQPU mesh ______________/
```

## What every node does

Every online WQPU node:

- contributes part of its CPU/RAM through a localhost-only `ggml-rpc-server`;
- listens for WQPU peer connections when its network allows it;
- connects outbound to known peers;
- exchanges learned peer addresses and TLS fingerprints;
- can temporarily relay an RPC stream for two other peers;
- can ask its own questions.

When a question is typed on a computer, **that computer coordinates only its own request**. It temporarily connects available workers, starts its own local `llama-server`, gets the answer, then tears the request coordinator down.

Several computers can originate requests at the same time. There is no permanent leader election.

## Install and immediately enter the CLI

### macOS / Linux

```bash
curl -fsSL "https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.sh?v=0.5.3-r2" | sh
```

### Windows PowerShell

```powershell
irm 'https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.ps1?v=0.5.3-r2' | iex
```

The `r2` installer revision deliberately uses a new cache key and verifies that the downloaded core actually reports `WQPU 0.5.3` before it is allowed to start.

The installer immediately starts the interactive CLI in the **same terminal**. WQPU 0.5.3 restores console input inside the core itself if the installer was launched through a pipe, instead of relying on shell-specific stdin redirection.

```text
wqpu> hello
wqpu> /status
wqpu> /peers
wqpu> /exit
```

## Python compatibility

WQPU is compatible with Python 3.6+.

The installer does not depend on one exact Python version. It uses an already installed compatible Python when possible, tries the operating system package manager only when required, and can prepare a private Python runtime for WQPU without replacing the system Python.

## Creating and joining a network

The first node can start with no join code. It creates a private network secret locally.

To invite another computer, a reachable node types:

```text
/invite PUBLIC_HOST:7443
```

WQPU prints a private `WQPU1...` join code.

On macOS/Linux, pass the join code as an argument to the installer:

```bash
curl -fsSL "https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.sh?v=0.5.3-r2" | sh -s -- 'WQPU1...'
```

Do **not** use `WQPU_JOIN='...' curl ... | sh`: that environment variable belongs to `curl`, not to the `sh` process on the right side of the pipe.

On Windows:

```powershell
$env:WQPU_JOIN='WQPU1...'; irm 'https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.ps1?v=0.5.3-r2' | iex
```

Supplying a join code always switches the local node to that WQPU network and clears stale peer-cache entries from any previous test network. This makes repeat/recovery joins safe.

The first address is only an introduction. After connection, nodes exchange peer information and cache other reachable peers. Any reachable WQPU node can perform the same introduction/relay function; there is no special VPS role in the protocol.

## Internet/NAT reality

A brand-new computer cannot discover a private network on the global Internet from literally zero information. It needs at least one address of an existing peer for first contact.

WQPU avoids a dedicated relay service by making **every node capable of relaying**. Nodes reachable from the Internet naturally become useful routes for peers behind NAT, but they receive no extra permissions and run exactly the same software.

If every single peer is behind restrictive NAT/firewalls and none is reachable, the mesh needs a port-forwarded/publicly reachable ordinary WQPU peer before those isolated groups can meet. Reliable universal NAT hole punching without any external rendezvous infrastructure is not physically guaranteed, so WQPU does not pretend otherwise.

## Security

- WQPU peer traffic uses TLS.
- The join code carries a private network secret plus trusted bootstrap peer fingerprints.
- Learned fingerprints are propagated through authenticated peers.
- `llama.cpp` RPC stays on `127.0.0.1:50052` and is never intentionally exposed directly to the Internet.
- Keep `WQPU1...` join codes private.

This is still an experimental prototype, not a hardened public compute marketplace.

## Resource policy

By default WQPU uses about 50% of logical CPU threads for contribution. Override temporarily with:

```bash
WQPU_CPU_FRACTION=0.35 wqpu
```

Model quality is determined by the selected GGUF model/quantization. Distribution itself does not requantize the model. The default small Gemma model is only for connectivity testing.
