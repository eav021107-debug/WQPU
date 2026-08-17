#!/usr/bin/env python3

import tempfile
import unittest
from pathlib import Path

import p2p_config


SAMPLE = '''[p2p]
laddr = "tcp://0.0.0.0:26656"
external_address = ""
seeds = ""
persistent_peers = ""
addr_book_file = "config/addrbook.json"
addr_book_strict = true
max_num_inbound_peers = 40
max_num_outbound_peers = 10
pex = true
seed_mode = false
allow_duplicate_ip = false

[mempool]
type = "app"
'''


class P2PConfigTests(unittest.TestCase):
    def test_patch_enables_pex_and_sets_multiple_seeds(self):
        seeds = [
            "0123456789abcdef0123456789abcdef01234567@seed-a.example.org:26656",
            "89abcdef0123456789abcdef0123456789abcdef@203.0.113.8:26656",
        ]
        out = p2p_config.patch_p2p_config(
            SAMPLE,
            seeds=seeds,
            seed_mode=False,
            external_address="tcp://node.example.org:26656",
        )
        self.assertIn('seeds = "' + ",".join(seeds) + '"', out)
        self.assertIn("pex = true", out)
        self.assertIn("seed_mode = false", out)
        self.assertIn("addr_book_strict = true", out)
        self.assertIn('external_address = "tcp://node.example.org:26656"', out)
        self.assertIn('addr_book_file = "config/addrbook.json"', out)

    def test_manifest_deduplicates_and_rejects_bad_node_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "seeds.txt"
            good = "0123456789abcdef0123456789abcdef01234567@seed.example.org:26656"
            path.write_text(good + "\n" + good + "\n", encoding="utf-8")
            self.assertEqual(p2p_config.load_seeds(path), [good])
            path.write_text("abcd@seed.example.org:26656\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                p2p_config.load_seeds(path)

    def test_seed_mode_is_explicit(self):
        out = p2p_config.patch_p2p_config(SAMPLE, seeds=[], seed_mode=True)
        self.assertIn('seeds = ""', out)
        self.assertIn("seed_mode = true", out)
        self.assertIn("pex = true", out)


if __name__ == "__main__":
    unittest.main()
