import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wqpu_runtime_pin


class RuntimePinTests(unittest.TestCase):
    def test_desired_tag_prefers_environment_then_config_then_default(self):
        with mock.patch.object(wqpu_runtime_pin, "network_runtime_config", return_value={"llama_cpp_tag": "b200"}), \
             mock.patch.dict(os.environ, {"WQPU_LLAMA_TAG": "b300"}, clear=False):
            self.assertEqual(wqpu_runtime_pin.desired_tag(), "b300")

        with mock.patch.object(wqpu_runtime_pin, "network_runtime_config", return_value={"llama_cpp_tag": "b200"}), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(wqpu_runtime_pin.desired_tag(), "b200")

        with mock.patch.object(wqpu_runtime_pin, "network_runtime_config", return_value={}), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(wqpu_runtime_pin.desired_tag(), wqpu_runtime_pin.DEFAULT_LLAMA_TAG)

    def test_exact_cached_tag_never_queries_github(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "llama-server"
            rpc = root / "ggml-rpc-server"
            server.write_bytes(b"server")
            rpc.write_bytes(b"rpc")
            (root / "current.json").write_text(json.dumps({
                "tag": "b10456",
                "server": str(server),
                "rpc": str(rpc),
            }))

            old_runtime = wqpu_runtime_pin.wqpu.RUNTIME
            try:
                wqpu_runtime_pin.wqpu.RUNTIME = root
                with mock.patch.object(wqpu_runtime_pin, "desired_tag", return_value="b10456"), \
                     mock.patch.object(wqpu_runtime_pin.wqpu, "ensure_home"), \
                     mock.patch.object(wqpu_runtime_pin.wqpu, "api_json", side_effect=AssertionError("network lookup")):
                    actual_server, actual_rpc, tag = wqpu_runtime_pin.ensure_runtime()
                self.assertEqual(actual_server, server)
                self.assertEqual(actual_rpc, rpc)
                self.assertEqual(tag, "b10456")
            finally:
                wqpu_runtime_pin.wqpu.RUNTIME = old_runtime

    def test_wrong_cached_tag_forces_exact_release_lookup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            server = root / "old-server"
            rpc = root / "old-rpc"
            server.write_bytes(b"server")
            rpc.write_bytes(b"rpc")
            (root / "current.json").write_text(json.dumps({
                "tag": "b-old",
                "server": str(server),
                "rpc": str(rpc),
            }))

            old_runtime = wqpu_runtime_pin.wqpu.RUNTIME
            try:
                wqpu_runtime_pin.wqpu.RUNTIME = root
                with mock.patch.object(wqpu_runtime_pin, "desired_tag", return_value="b10456"), \
                     mock.patch.object(wqpu_runtime_pin.wqpu, "ensure_home"), \
                     mock.patch.object(wqpu_runtime_pin.wqpu, "asset_suffix", return_value="-test.zip"), \
                     mock.patch.object(wqpu_runtime_pin.wqpu, "api_json", return_value={
                         "tag_name": "b10456",
                         "assets": [],
                     }) as lookup:
                    with self.assertRaises(RuntimeError):
                        wqpu_runtime_pin.ensure_runtime()
                lookup.assert_called_once_with(
                    "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b10456"
                )
            finally:
                wqpu_runtime_pin.wqpu.RUNTIME = old_runtime

    def test_asset_sha256_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.zip"
            payload = b"pinned-llama-runtime"
            path.write_bytes(payload)
            digest = hashlib.sha256(payload).hexdigest()
            wqpu_runtime_pin._verify_asset(path, {"digest": "sha256:" + digest})
            with self.assertRaises(RuntimeError):
                wqpu_runtime_pin._verify_asset(path, {"digest": "sha256:" + "00" * 32})
            with self.assertRaises(RuntimeError):
                wqpu_runtime_pin._verify_asset(path, {"digest": "md5:deadbeef"})


if __name__ == "__main__":
    unittest.main()
