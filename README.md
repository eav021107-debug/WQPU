# WQPU 0.3

**One LLM across computers connected through the WQPU protocol. No Tailscale required.**

WQPU uses one small public VPS as a TLS relay/coordinator. Contributor computers make outbound encrypted connections to the relay. `llama.cpp` RPC stays on localhost and is tunneled through WQPU, so port `50052` is never exposed to the Internet.

```text
Mac ─────┐
Windows ─┼── encrypted WQPU relay ── one llama-server
Linux ───┘
```

## 1. Install the relay once on the VPS

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install-relay.sh | sudo sh
```

The relay installer prints a **single node-install command** containing a private join token. Copy that command to each contributor computer.

## 2. Contributor computers

Linux/macOS command is printed automatically by the relay. It has this form:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install-node.sh | sh -s -- 'WQPU1....'
```

Windows PowerShell command is also printed by the relay.

Every connected node:

- contributes about 50% of its logical CPU threads by default;
- keeps inference processes at lower priority;
- gets local access to the shared LLM at `http://127.0.0.1:8080`;
- can ask from the terminal with `wqpu ask "your question"`;
- can check the cluster with `wqpu status`.

The computer with the most RAM becomes the inference coordinator. Other nodes expose only a localhost `ggml-rpc-server`; the WQPU relay tunnels RPC traffic between machines.

## Multi-user model

In this MVP, **an online authenticated node is a contributor and gets access to the shared model**. All users talk to the same `llama-server`, which supports concurrent requests. Precise contribution credits/accounting are intentionally not implemented yet; that comes after the network/inference path is proven stable.

## Security

The relay generates its own TLS certificate. The node join token contains the pinned SHA-256 certificate fingerprint and a random cluster secret. Nodes verify the relay certificate fingerprint before sending traffic. The join token is private and should not be posted publicly.

Only the WQPU relay port (`7443/tcp` by default) needs to be Internet-accessible. `llama.cpp` RPC and the local chat/API are bound to localhost.

## Model quality

WQPU distribution itself does not quantize or alter model weights. Quality is determined by the GGUF model you choose. The default small `gemma-3-1b-it Q4_K_M` model is only for the first connectivity test.

## Current MVP limitations

- CPU backend first; GPU auto-detection/acceleration is the next step.
- Internet RPC is functional but can be slow because transformer inference is latency/bandwidth sensitive.
- Join tokens are shared-cluster credentials; per-user accounts and contribution credits are not implemented yet.
- This remains an experimental prototype, not a hardened public compute marketplace.
