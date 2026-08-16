# WQPU 0.5

**Equal-peer distributed LLM over the WQPU protocol. No Tailscale and no permanent coordinator.**

Every contributor computer has the same role. The VPS is only an encrypted meeting point/relay for machines behind NAT. It does not run the model and it does not choose a leader.

```text
Mac ─────┐
Windows ─┼── encrypted WQPU relay ── equal peers
Linux ───┘
```

## How a question works

If a question is asked on PC A, only PC A temporarily coordinates that request:

```text
question on A -> A connects online peers as helpers -> answer returns to A -> temporary coordinator stops
```

If the next question is asked on PC C, PC C does the same. There is no permanent main computer and no peer has extra rights.

Each online peer keeps only a localhost `ggml-rpc-server` running and contributes about 50% of its logical CPU threads. A temporary `llama-server` exists only while that computer is answering its own user's request.

## 1. Install the relay on the VPS

Run once:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install-relay.sh | sudo sh
```

The installer prints private join commands for Linux/macOS and Windows. Only TCP `7443` needs to be reachable on the VPS.

## 2. Join contributor computers

Use the command printed by the relay. Linux/macOS looks like:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install-node.sh | sh -s -- 'WQPU1....'
```

Keep that terminal open while the computer contributes. In another terminal:

```bash
wqpu status
wqpu ask "Hello, who are you?"
```

## Access rule

The MVP rule is: a node that is authenticated, online, and running its contributor worker can ask questions. Precise contribution accounting/credits are not implemented yet.

## Security

The join token contains the relay address, a random shared cluster secret, and a pinned SHA-256 fingerprint of the relay TLS certificate. Peers verify that fingerprint before sending traffic.

`llama.cpp` RPC is bound only to `127.0.0.1`. WQPU carries RPC through the encrypted relay, so TCP `50052` is not exposed to the public Internet.

## Model

The default test model is:

```text
ggml-org/gemma-3-1b-it-GGUF:Q4_K_M
```

Set `WQPU_MODEL` before asking if you want another compatible Hugging Face GGUF repo. Distribution itself does not change or requantize the weights.

## Current files

The project intentionally stays small:

- `wqpu.py` — the only peer client;
- `relay.py` — relay/rendezvous only;
- `install-node.sh` — Linux/macOS peer installer;
- `install-node.ps1` — Windows peer installer;
- `install-relay.sh` — VPS relay installer;
- `README.md` — this documentation.

## Current limitations

- CPU backend first; automatic GPU acceleration is not implemented yet.
- WAN RPC can be slow because inference is sensitive to latency and bandwidth.
- Multiple simultaneous request-originators can compete for the same contributor resources; admission control still needs stress testing.
- Authentication uses one shared cluster join token in this MVP; per-user identities/credits are not implemented yet.
- This is an experimental prototype, not yet a hardened public compute marketplace.
