# WQPU testnet stack

`WQPU 0.6.x` can run a self-contained EVM testnet operator stack for multi-PC testing.
This is **not** the final sovereign WQPU L1 and must not hold real funds.

## Start

Install Foundry (`anvil`, `forge`, `cast`) and OpenSSL, then from the repository:

```bash
python scripts/testnet_stack.py start --public-host YOUR_SERVER_IP_OR_DNS
```

The command starts:

- Anvil on loopback only (`127.0.0.1:28545` by default);
- a restricted public JSON-RPC gateway on port `8545`;
- the WQPU gas relayer + testnet faucet/config server on `8787`;
- the WQPU TLS transport relay on `7443`;
- Token, Registry and ComputeMarket contracts with a fresh random testnet operator key.

The public RPC gateway deliberately blocks unlocked-account and development/admin RPCs
such as `eth_accounts`, `eth_sendTransaction`, `anvil_*`, `hardhat_*`, `debug_*` and
`personal_*`. Wallets can read chain state and publish their own signed raw transactions.
The underlying Anvil RPC is never bound publicly.

At the end, the stack prints one client command for Linux/macOS and one for Windows.
Those join scripts install WQPU, replace the bundled network config with the generated
config, and start the node.

Automatic real-value-style vouchers remain disabled in generated testnet config unless
`--payments` is explicitly passed. Testnet faucet funds have no real value.

## Operations

```bash
python scripts/testnet_stack.py status
python scripts/testnet_stack.py config
python scripts/testnet_stack.py stop
```

Runtime files are under `.wqpu-testnet/`. The operator private key is generated fresh,
stored only there with restricted file permissions, and is marked testnet-only.

## Ports

Defaults are `8545` (RPC gateway), `8787` (relayer/config/faucet), and `7443` (transport
relay). If the server is behind a router/firewall, those three ports must be reachable by
participating PCs. The internal Anvil port `28545` must stay private.
