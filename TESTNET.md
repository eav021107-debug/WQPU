# WQPU testnet stack

`WQPU 0.6.x` can run a self-contained EVM testnet operator stack for multi-PC testing.
This is **not** the final sovereign WQPU L1 and must not hold real funds.

## One-command operator install

On a Linux/macOS testnet server, the intended operator path is:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install-testnet.sh | sh -s -- --public-host YOUR_SERVER_IP_OR_DNS
```

The installer works in user space. It prepares Python/OpenSSL/base tools when the local
package manager permits it, installs Foundry in the user's home if `anvil`/`forge`/`cast`
are missing, downloads WQPU, validates the Python modules, runs `forge build`, creates
`~/.local/bin/wqpu-testnet`, and starts the stack.

Re-running the installer updates operator code **without deleting** the existing
`~/.local/share/wqpu-operator/.wqpu-testnet/` chain/operator/deployment/relay identity.

For an Internet-facing domain with a trusted certificate:

```bash
curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install-testnet.sh | sh -s -- \
  --public-host testnet.example.com \
  --tls-cert /etc/letsencrypt/live/testnet.example.com/fullchain.pem \
  --tls-key /etc/letsencrypt/live/testnet.example.com/privkey.pem
```

For development from a specific branch/commit, set `WQPU_OPERATOR_REF` before running the
installer. The generated client join scripts use that same ref, so operator and clients
do not silently mix incompatible code.

## Manual start from a repository checkout

If Foundry (`anvil`, `forge`, `cast`) and OpenSSL are already installed:

```bash
python scripts/testnet_stack.py start --public-host YOUR_SERVER_IP_OR_DNS
```

The command starts:

- Anvil on loopback only (`127.0.0.1:28545` by default);
- a restricted public JSON-RPC gateway on port `8545`;
- the WQPU gas relayer + testnet faucet/config server on `8787`;
- the WQPU TLS transport relay on `7443`;
- Token, Registry and ComputeMarket contracts with a random testnet operator key.

The public RPC gateway deliberately blocks unlocked-account and development/admin RPCs
such as `eth_accounts`, `eth_sendTransaction`, `anvil_*`, `hardhat_*`, `debug_*` and
`personal_*`. Wallets can read chain state and publish their own signed raw transactions.
The underlying Anvil RPC is never bound publicly.

At the end, the stack prints one client command for Linux/macOS and one for Windows.
Those join scripts install WQPU, replace the bundled network config with the generated
config, and start the node. When `faucet_url` is present, the browser wallet connector
requests test ETH/WQPU before it asks the wallet to register the node.

The faucet tops a wallet up to configured test balances rather than blindly adding the
same amount on every request/restart. Automatic real-value-style vouchers remain disabled
in generated testnet config unless `--payments` is explicitly passed. Testnet faucet
funds have no real value.

## HTTPS for an Internet-facing testnet

Plain HTTP is useful for localhost/LAN testing. For a real Internet-facing domain, pass a
trusted PEM certificate and matching private key:

```bash
wqpu-testnet start \
  --public-host testnet.example.com \
  --tls-cert /etc/letsencrypt/live/testnet.example.com/fullchain.pem \
  --tls-key /etc/letsencrypt/live/testnet.example.com/privkey.pem
```

The RPC, relayer, faucet, config and generated join URLs then switch to `https://`.
The same certificate setting is remembered for normal stop/start cycles. Use a publicly
trusted certificate for browser wallets; a self-signed certificate is only suitable for
controlled development/testing.

The WQPU transport relay on port `7443` remains a separate pinned-TLS channel whose exact
certificate fingerprint is published in `network-config.json`.

## Persistence

Normal `stop` / `start` cycles preserve the same testnet. Anvil checkpoints its state to
`.wqpu-testnet/anvil-state.json`; the operator key and deployed Token/Registry/Market
addresses are also retained. Balances, registry membership and contract storage therefore
survive an operator restart.

With the operator launcher:

```bash
wqpu-testnet stop
wqpu-testnet start
```

To intentionally destroy that testnet and create a new chain/operator/contracts:

```bash
wqpu-testnet reset --yes
wqpu-testnet start --public-host YOUR_SERVER_IP_OR_DNS
```

`reset --yes` is destructive. Already connected clients must receive the new generated
network config after a reset.

## Backup and move the same testnet

Stop the stack first, then create a portable backup outside `.wqpu-testnet/`:

```bash
wqpu-testnet stop
wqpu-testnet backup ~/wqpu-testnet-backup.tar.gz
```

The backup contains:

- the persisted Anvil chain state;
- the testnet operator private key and deployment metadata;
- the WQPU transport relay certificate/private key/node identity.

That is enough to preserve the same Token/Registry/Market addresses, balances and
transport TLS fingerprint on another server. The archive is created with owner-only file
permissions (`0600` where supported), includes a manifest with SHA-256/size checks, and
restore only accepts the expected regular files (no symlinks/path traversal).

**Treat the backup as a secret.** Anyone who obtains it receives the testnet operator and
relay private keys. The archive is not a production key-management mechanism.

On the destination server, install the operator code, stop any empty/new stack, then:

```bash
wqpu-testnet restore ~/wqpu-testnet-backup.tar.gz --yes
wqpu-testnet start --public-host NEW_SERVER_IP_OR_DNS
```

If the Internet hostname/server changed, provide the appropriate trusted public HTTPS
certificate again with `--tls-cert/--tls-key`. External HTTPS certificate files are not
embedded in the WQPU backup.

## Operations

```bash
wqpu-testnet status
wqpu-testnet config
wqpu-testnet stop
wqpu-testnet backup ~/wqpu-testnet-backup.tar.gz
wqpu-testnet restore ~/wqpu-testnet-backup.tar.gz --yes
wqpu-testnet reset --yes
```

Runtime files are under the operator install root's `.wqpu-testnet/` directory. The
operator private key is stored only there with restricted file permissions and is marked
testnet-only.

## Ports

Defaults are `8545` (RPC gateway), `8787` (relayer/config/faucet), and `7443` (transport
relay). If the server is behind a router/firewall, those three ports must be reachable by
participating PCs. The internal Anvil port `28545` must stay private.
