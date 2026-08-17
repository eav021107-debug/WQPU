import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wqpu_runtime_pin


class FakeHttpError(RuntimeError):
    def __init__(self, code):
        super(FakeHttpError, self).__init__("http {}".format(code))
        self.code = code


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

    def test_pinned_b10456_asset_never_needs_release_api(self):
        with mock.patch.object(
            wqpu_runtime_pin.wqpu, "api_json", side_effect=AssertionError("release API")
        ):
            asset = wqpu_runtime_pin._asset_for("b10456", "-bin-ubuntu-x64.tar.gz")
        self.assertEqual(asset["name"], "llama-b10456-bin-ubuntu-x64.tar.gz")
        self.assertEqual(
            asset["digest"],
            "sha256:d07b3f80f3a1ed1de46bfba5671b4af40a87417e1dbf35d0603ad2d623ddc577",
        )
        self.assertEqual(asset["source"], "wqpu-pinned-manifest")
        self.assertIn("/releases/download/b10456/", asset["browser_download_url"])

    def test_manifest_covers_every_client_platform_suffix(self):
        suffixes = {
            "-bin-ubuntu-x64.tar.gz",
            "-bin-ubuntu-arm64.tar.gz",
            "-bin-macos-arm64.tar.gz",
            "-bin-macos-x64.tar.gz",
            "-bin-win-cpu-x64.zip",
            "-bin-win-cpu-arm64.zip",
        }
        self.assertEqual(set(wqpu_runtime_pin.PINNED_ASSETS["b10456"]), suffixes)
        for suffix in suffixes:
            self.assertEqual(len(wqpu_runtime_pin.PINNED_ASSETS["b10456"][suffix]), 64)
            int(wqpu_runtime_pin.PINNED_ASSETS["b10456"][suffix], 16)

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

    def test_release_lookup_retries_transient_5xx(self):
        release = {"tag_name": "b10456", "assets": []}
        with mock.patch.object(
            wqpu_runtime_pin.wqpu,
            "api_json",
            side_effect=[FakeHttpError(504), FakeHttpError(502), release],
        ) as lookup, mock.patch.object(wqpu_runtime_pin, "_backoff") as backoff:
            actual = wqpu_runtime_pin._release_json("b10456")
        self.assertEqual(actual, release)
        self.assertEqual(lookup.call_count, 3)
        self.assertEqual(backoff.call_count, 2)

    def test_release_lookup_does_not_retry_exact_4xx(self):
        with mock.patch.object(
            wqpu_runtime_pin.wqpu, "api_json", side_effect=FakeHttpError(404)
        ) as lookup, mock.patch.object(wqpu_runtime_pin, "_backoff") as backoff:
            with self.assertRaises(RuntimeError):
                wqpu_runtime_pin._release_json("missing")
        self.assertEqual(lookup.call_count, 1)
        backoff.assert_not_called()

    def test_asset_download_retries_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.zip"
            calls = {"n": 0}

            def retry_download(_url, target):
                calls["n"] += 1
                if calls["n"] == 1:
                    target.write_bytes(b"partial")
                    raise FakeHttpError(503)
                target.write_bytes(b"complete")

            with mock.patch.object(wqpu_runtime_pin.wqpu, "download", side_effect=retry_download), \
                 mock.patch.object(wqpu_runtime_pin, "_backoff"):
                wqpu_runtime_pin._download_asset("https://example.invalid/asset", path)
            self.assertEqual(calls["n"], 2)
            self.assertEqual(path.read_bytes(), b"complete")

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
            with self.assertRaises(RuntimeError):
                wqpu_runtime_pin._verify_asset(path, {})


if __name__ == "__main__":
    unittest.main()
