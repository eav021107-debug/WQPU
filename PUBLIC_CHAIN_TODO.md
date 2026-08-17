# WQPU public deployment checklist

The WQPU 0.6.0 code path is implemented. The remaining work is deployment/security infrastructure, not missing client plumbing.

## Already implemented

- wallet connector without private-key custody;
- on-chain node discovery and TLS fingerprint binding;
- one global network price;
- load-aware multi-worker scheduling;
- pinned `llama.cpp` RPC runtime;
- meter v2 with fail-closed parsing;
- shared escrow and EIP-2612 permit funding;
- bounded local session key;
- cumulative provider vouchers and replay protection;
- HTTP gas relayer;
- durable voucher delivery/claim flow;
- TLS-pinned bootstrap relay support for NAT/CGNAT nodes;
- Linux and Windows one-command installers;
- local EVM devnet and end-to-end CI.

## Required to publish a real public network

1. Choose/deploy the WQPU EVM chain or testnet.
2. Deploy `WQPUToken`, `WQPURegistry(initialPrice)`, then `WQPUComputeMarket(token, registry)`.
3. Run at least one public TLS transport relay and record its certificate fingerprint.
4. Run at least one funded gas relayer. Production should use a dedicated signer/HSM rather than an unlocked RPC account.
5. Fill `network-config.json` with chain ID, RPC, token, registry, market, gas-relayer URL and bootstrap relay(s), then set `public.enabled=true`.
6. Run the full CI/devnet suite against the chosen deployment and perform multi-host Internet testing.
7. Complete adversarial/security testing and an independent contract/network audit before enabling real-value automatic payments.

After steps 1-5, a fresh install follows the intended path:

```text
install -> connect existing wallet -> register -> discover peers -> run
```

No join code or manual peer list is required for public mode.
