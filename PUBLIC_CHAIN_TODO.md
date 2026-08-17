# WQPU public deployment checklist

WQPU 0.6.0 contains the public-network prototype code path. The remaining work is external deployment/security infrastructure, not missing client plumbing.

## Implemented

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

## Publish a real public network

1. Deploy the chosen WQPU EVM chain/testnet.
2. Deploy `WQPUToken`, `WQPURegistry(initialPrice)`, then `WQPUComputeMarket(token, registry)`.
3. Run at least one public TLS transport relay and record its certificate fingerprint.
4. Run at least one funded gas relayer. Production should use a dedicated signer/HSM rather than an unlocked RPC account.
5. Fill `network-config.json` with chain ID, RPC, token, registry, market, gas-relayer URL and bootstrap relay(s), then set `public.enabled=true`.
6. Enable `payments_enabled`/`payment_enforcement` only after the deployment accepts meter v2 and passes adversarial testing.
7. Run the full CI/devnet suite against the deployed network and perform multi-host Internet testing.
8. Complete an independent contract/network audit before real-value production use.

After steps 1-5, a fresh install follows:

```text
install -> connect existing wallet -> register -> discover peers -> run
```

No join code or manual peer list is required in public mode.
