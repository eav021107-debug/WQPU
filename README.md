# WQPU 0.4

**Equal-peer distributed LLM over the WQPU protocol. No Tailscale and no permanent coordinator.**

All contributor computers have the same role. The VPS is only a small encrypted relay/meeting point so machines behind NAT can reach each other. It does not run the model and does not choose a leader.

```text
Mac ─────┐
Windows ─┼── encrypted WQPU relay ── peer network
Linux ───┘
```

## How a question works

If a user asks from PC A, PC A coordinates only that request:

```text
question on A -> A connects B/C/D as helpers -> answer returns to A -> temporary coordinator disappears
```

If the next question is asked from PC C, then C coordinates that request. There is no permanent main computer and no node has extra rights.

Every online node keeps only a localhost `ggml-rpc-server` running and contributes about 50% of its logical CPU threads. A temporary `llama-server` is started only on the machine that asks a question, uses the other online peers through encrypted WQPU tunnels, returns the answer, then stops.

## 1. Relay on the VPS

Run once:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install-relay.sh | sudo sh
```

The installer prints private join commands. The relay only needs TCP `7443` reachable from the Internet.

## 2. Contributor computers

Linux/macOS uses the command printed by the relay, shaped like:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install-node.sh | sh -s -- 'WQPU1....'
```

Windows gets a PowerShell command from the relay installer.

Keep WQPU running while contributing. In another terminal:

```bash
wqpu status
wqpu ask "Hello, who are you?"
```

## Access rule

The MVP rule is simple: an authenticated node that is online and contributing can ask questions. Precise credits/accounting are not implemented yet.

## Security

The join token contains the relay address, a random cluster secret, and the pinned SHA-256 fingerprint of the relay TLS certificate. Nodes verify that fingerprint before using the relay. The token is private.

`llama.cpp` RPC stays bound to `127.0.0.1`; WQPU carries it through the encrypted relay, so TCP `50052` is not exposed publicly.

## Model quality

Distribution does not change or requantize the model. Quality is determined by the chosen GGUF model. The default `gemma-3-1b-it Q4_K_M` is only a connectivity test model.

## MVP limitations

- CPU backend first; GPU acceleration comes later.
- WAN RPC can be slow because inference is sensitive to latency and bandwidth.
- Simultaneous requests from many peers still need stress testing and resource admission controls.
- Shared join tokens are temporary MVP authentication; per-user identities/credits come later.
- Experimental prototype, not yet a hardened public compute marketplace.
