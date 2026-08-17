# WQPU public-chain launch blockers

The blockchain runtime itself is now wired into the WQPU prototype. These items still prevent the final no-configuration public launch:

1. Choose/deploy the WQPU EVM chain or test network.
2. Deploy `WQPUToken`, then `WQPURegistry(initialPrice)`, then `WQPUComputeMarket(token, registry)`.
3. Publish default RPC URL, chain ID, token address, registry address and market address in the client release.
4. Add NAT traversal/relay policy for nodes whose registered endpoint is not directly reachable.
5. Define and implement per-provider llama.cpp compute units.
6. Generate EIP-712 cumulative vouchers from measured work and implement provider claims.
7. Add adversarial tests and audit contracts before real-value use.

Once item 1-3 are done, a fresh public-mode install can open the wallet connector automatically and discover nodes without a join code. Items 4-7 are required before calling the system production-ready.
