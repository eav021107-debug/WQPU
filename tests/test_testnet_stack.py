import importlib.util
import json
import os
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

    def test_https_config_switches_all_public_http_endpoints(self):
        cfg = stack.build_network_config(
            "testnet.example", 8545, 8787, 7443, "cd" * 32,
            "0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20,
            scheme="https",
        )
        public = cfg["public"]
        self.assertEqual(public["rpc_url"], "https://testnet.example:8545")
        self.assertEqual(public["relayer_url"], "https://testnet.example:8787/relay")
        self.assertEqual(public["faucet_url"], "https://testnet.example:8787/faucet")

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

    def test_bad_public_url_scheme_is_rejected(self):
        with self.assertRaises(ValueError):
            stack.build_network_config(
                "example.test", 1, 2, 3, "ef" * 32,
                "0x" + "11" * 20, "0x" + "22" * 20, "0x" + "33" * 20,
                scheme="ftp",
            )

    def _fake_persistent_stack(self, root):
        root = Path(root)
        relay = root / "relay-home"
        relay.mkdir(parents=True)
        operator = "0x" + "44" * 20
        (root / "operator.json").write_text(json.dumps({
            "address": operator,
            "private_key": "0x" + "11" * 32,
        }))
        (root / "deployment.json").write_text(json.dumps({
            "chain_id": "0x7a69",
            "operator": operator,
            "token": "0x" + "11" * 20,
            "registry": "0x" + "22" * 20,
            "market": "0x" + "33" * 20,
        }))
        (root / "anvil-state.json").write_bytes(b'{"persisted":true}')
        (relay / "cert.pem").write_bytes(b"TEST-CERT")
        (relay / "key.pem").write_bytes(b"TEST-KEY")
        (relay / "node-id").write_bytes(b"relay-node\n")
        return operator

    def test_backup_restore_round_trip_preserves_secret_identity_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            source = base / "source"
            restored = base / "restored"
            source.mkdir()
            self._fake_persistent_stack(source)
            archive = base / "network-backup.tar.gz"

            path, manifest = stack.create_backup_archive(archive, source)
            self.assertEqual(path, archive.resolve())
            self.assertEqual(manifest["registry"], "0x" + "22" * 20)
            self.assertTrue(archive.is_file())
            if os.name != "nt":
                self.assertEqual(archive.stat().st_mode & 0o777, 0o600)

            restored_manifest = stack.restore_backup_archive(archive, restored)
            self.assertEqual(restored_manifest["registry"], manifest["registry"])
            for rel in (
                "operator.json", "deployment.json", "anvil-state.json",
                "relay-home/cert.pem", "relay-home/key.pem", "relay-home/node-id",
            ):
                self.assertEqual((restored / rel).read_bytes(), (source / rel).read_bytes())

    def test_backup_archive_cannot_be_written_inside_live_state_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            self._fake_persistent_stack(source)
            with self.assertRaises(RuntimeError):
                stack.create_backup_archive(source / "backup.tar.gz", source)

    def test_backup_member_path_validation_blocks_traversal(self):
        self.assertFalse(stack._safe_backup_member_name("../operator.json"))
        self.assertFalse(stack._safe_backup_member_name("relay-home/../../operator.json"))
        self.assertFalse(stack._safe_backup_member_name("/etc/passwd"))
        self.assertTrue(stack._safe_backup_member_name("operator.json"))
        self.assertTrue(stack._safe_backup_member_name("relay-home/cert.pem"))


if __name__ == "__main__":
    unittest.main()
