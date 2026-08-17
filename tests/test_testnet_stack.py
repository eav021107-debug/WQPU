import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "testnet_stack.py"
spec = importlib.util.spec_from_file_location("wqpu_testnet_stack", str(SCRIPT))
stack = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stack)


class TestnetStackTests(unittest.TestCase):
    def test_generated_operator_key_is_valid_secp256k1_scalar(self):
        value = stack.generate_private_key()
        self.assertTrue(value.startswith("0x"))
        self.assertEqual(len(value), 66)
        number = int(value, 16)
        self.assertGreater(number, 0)
        self.assertLess(number, stack.SECP256K1_N)

    def test_ipv6_url_host_is_bracketed(self):
        self.assertEqual(stack.url_host("2001:db8::1"), "[2001:db8::1]")
        self.assertEqual(stack.url_host("example.test"), "example.test")

    def test_network_config_contains_public_gateway_and_pinned_relay(self):
        cfg = stack.build_network_config(
            "10.0.0.5", 18545, 18787, 17443, "ab" * 32,
            "0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20,
            payments_enabled=False, faucet_enabled=True,
        )
        public = cfg["public"]
        self.assertTrue(public["enabled"])
        self.assertTrue(public["testnet"])
        self.assertEqual(public["rpc_url"], "http://10.0.0.5:18545")
        self.assertEqual(public["relayer_url"], "http://10.0.0.5:18787/relay")
        self.assertEqual(public["faucet_url"], "http://10.0.0.5:18787/faucet")
        self.assertEqual(public["relays"][0]["fingerprint"], "ab" * 32)
        self.assertFalse(public["payments_enabled"])
        self.assertEqual(public["llama_cpp_tag"], "b10456")

    def test_new_anvil_dumps_state_without_loading_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            cmd = stack.build_anvil_command(28545, path)
            self.assertIn("--dump-state", cmd)
            self.assertIn("--state-interval", cmd)
            self.assertNotIn("--load-state", cmd)

    def test_existing_anvil_state_is_loaded_and_dumped_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{}")
            cmd = stack.build_anvil_command(28545, path)
            self.assertIn("--load-state", cmd)
            self.assertIn("--dump-state", cmd)
            self.assertEqual(cmd[cmd.index("--load-state") + 1], str(path))
            self.assertEqual(cmd[cmd.index("--dump-state") + 1], str(path))


if __name__ == "__main__":
    unittest.main()
