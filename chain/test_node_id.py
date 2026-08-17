#!/usr/bin/env python3

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import node_id


class NodeIDTests(unittest.TestCase):
    def test_derives_comet_id_and_seed_address(self):
        seed = bytes(range(32))
        public = bytes(range(32, 64))
        raw = seed + public
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "node_key.json"
            path.write_text(json.dumps({
                "priv_key": {
                    "type": "tendermint/PrivKeyEd25519",
                    "value": base64.b64encode(raw).decode("ascii"),
                }
            }), encoding="utf-8")
            expected = hashlib.sha256(public).digest()[:20].hex()
            actual = node_id.node_id_from_file(path)
            self.assertEqual(actual, expected)
            self.assertEqual(
                node_id.seed_address(actual, "tcp://seed.example.org:26656"),
                f"{expected}@seed.example.org:26656",
            )

    def test_rejects_short_private_key(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "node_key.json"
            path.write_text(json.dumps({
                "priv_key": {
                    "type": "tendermint/PrivKeyEd25519",
                    "value": base64.b64encode(b"x" * 32).decode("ascii"),
                }
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                node_id.node_id_from_file(path)


if __name__ == "__main__":
    unittest.main()
