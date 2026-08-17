import unittest

import wqpu_rpc_gateway as gateway


class GatewayPolicyTests(unittest.TestCase):
    def test_wallet_and_read_methods_are_allowed(self):
        for method in (
            "eth_chainId", "eth_call", "eth_estimateGas", "eth_getBalance",
            "eth_getTransactionReceipt", "eth_sendRawTransaction", "web3_sha3",
        ):
            self.assertTrue(gateway.method_allowed(method), method)

    def test_unlocked_and_admin_methods_are_blocked(self):
        for method in (
            "eth_accounts", "eth_sendTransaction", "eth_sign", "personal_unlockAccount",
            "anvil_setBalance", "hardhat_impersonateAccount", "debug_traceTransaction",
            "engine_forkchoiceUpdatedV3", "txpool_content",
        ):
            self.assertFalse(gateway.method_allowed(method), method)

    def test_unknown_method_fails_closed(self):
        self.assertFalse(gateway.method_allowed("wqpu_magicAdmin"))

    def test_blocked_request_returns_jsonrpc_error_without_forwarding(self):
        g = gateway.Gateway("http://127.0.0.1:1")
        result = g.handle({"jsonrpc": "2.0", "id": 7, "method": "eth_accounts", "params": []})
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["error"]["code"], -32601)

    def test_batch_size_is_bounded(self):
        g = gateway.Gateway("http://127.0.0.1:1")
        result = g.handle([{}] * (gateway.MAX_BATCH + 1))
        self.assertEqual(result["error"]["code"], -32600)


if __name__ == "__main__":
    unittest.main()
