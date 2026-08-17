import copy
import unittest

from chain.devnet_config import (
    NATIVE_PRECOMPILE,
    WQPU_PRECOMPILE,
    evm_hex_to_bech32,
    patch_app_toml,
    patch_comet_toml,
    patch_genesis,
)


class GenesisPatchTests(unittest.TestCase):
    def sample_genesis(self):
        return {
            "app_state": {
                "staking": {"params": {"bond_denom": "stake"}},
                "mint": {"params": {"mint_denom": "stake"}},
                "evm": {"params": {"evm_denom": "atest", "active_static_precompiles": []}},
                "gov": {
                    "params": {
                        "min_deposit": [{"denom": "stake", "amount": "1"}],
                        "expedited_min_deposit": [{"denom": "stake", "amount": "2"}],
                    }
                },
                "bank": {"denom_metadata": []},
                "erc20": {"native_precompiles": [], "token_pairs": []},
            },
            "consensus": {"params": {"block": {"max_gas": "-1"}}},
        }

    def test_native_coin_is_wqpu_everywhere_that_matters(self):
        out = patch_genesis(self.sample_genesis(), "awqpu", "WQPU", 18)
        app = out["app_state"]
        self.assertEqual(app["staking"]["params"]["bond_denom"], "awqpu")
        self.assertEqual(app["mint"]["params"]["mint_denom"], "awqpu")
        self.assertEqual(app["evm"]["params"]["evm_denom"], "awqpu")
        self.assertEqual(app["gov"]["params"]["min_deposit"][0]["denom"], "awqpu")
        self.assertEqual(app["bank"]["denom_metadata"][0]["symbol"], "WQPU")
        self.assertEqual(app["bank"]["denom_metadata"][0]["denom_units"][1]["exponent"], 18)
        self.assertEqual(app["erc20"]["token_pairs"][0]["denom"], "awqpu")
        self.assertEqual(app["erc20"]["token_pairs"][0]["erc20_address"], NATIVE_PRECOMPILE)

    def test_wqpu_precompile_is_consensus_active(self):
        out = patch_genesis(self.sample_genesis(), "awqpu", "WQPU", 18)
        self.assertEqual(
            out["app_state"]["evm"]["params"]["active_static_precompiles"],
            [WQPU_PRECOMPILE],
        )

    def test_patch_is_deterministic(self):
        src = self.sample_genesis()
        a = patch_genesis(copy.deepcopy(src), "awqpu", "WQPU", 18)
        b = patch_genesis(copy.deepcopy(src), "awqpu", "WQPU", 18)
        self.assertEqual(a, b)

    def test_missing_evm_params_fails_closed(self):
        src = self.sample_genesis()
        del src["app_state"]["evm"]["params"]
        with self.assertRaises(ValueError):
            patch_genesis(src, "awqpu", "WQPU", 18)


class AddressConversionTests(unittest.TestCase):
    def test_ci_evm_address_has_pinned_cosmos_bech32_form(self):
        self.assertEqual(
            evm_hex_to_bech32("0x69e839c39103813cd198767E0567254C0624a240"),
            "cosmos1d85rnsu3qwqne5vcwelq2ee9fsrzfgjqmfsa9f",
        )

    def test_address_conversion_rejects_wrong_length(self):
        with self.assertRaises(ValueError):
            evm_hex_to_bech32("0x1234")


class AppTomlPatchTests(unittest.TestCase):
    SAMPLE = """[evm]\nevm-chain-id = 262144\nmin-tip = 0\n\n[json-rpc]\nenable = false\naddress = \"0.0.0.0:8545\"\nws-address = \"0.0.0.0:8546\"\napi = \"eth,txpool,personal,net,debug,web3\"\nallow-insecure-unlock = true\nallow-unprotected-txs = true\n"""

    def test_wallet_rpc_is_local_and_replay_protected(self):
        out = patch_app_toml(self.SAMPLE, 711711)
        self.assertIn("evm-chain-id = 711711", out)
        self.assertIn("enable = true", out)
        self.assertIn('address = "127.0.0.1:8545"', out)
        self.assertIn('ws-address = "127.0.0.1:8546"', out)
        self.assertIn('api = "eth,net,web3"', out)
        self.assertIn("allow-insecure-unlock = false", out)
        self.assertIn("allow-unprotected-txs = false", out)
        self.assertNotIn("personal", out)
        self.assertNotIn("debug", out)

    def test_patch_does_not_change_other_sections(self):
        sample = "[api]\nenable = false\n\n" + self.SAMPLE
        out = patch_app_toml(sample, 711711)
        self.assertTrue(out.startswith("[api]\nenable = false\n"))


class CometTomlPatchTests(unittest.TestCase):
    SAMPLE = """proxy_app = \"tcp://127.0.0.1:26658\"\n\n[mempool]\nversion = \"v0\"\ntype = \"flood\"\nrecheck = true\n\n[p2p]\nladdr = \"tcp://0.0.0.0:26656\"\n"""

    def test_evm_uses_application_mempool(self):
        out = patch_comet_toml(self.SAMPLE)
        self.assertIn('[mempool]\nversion = "v0"\ntype = "app"\nrecheck = true', out)
        self.assertNotIn('type = "flood"', out)

    def test_patch_does_not_change_other_sections(self):
        out = patch_comet_toml(self.SAMPLE)
        self.assertIn('[p2p]\nladdr = "tcp://0.0.0.0:26656"', out)
        self.assertTrue(out.startswith('proxy_app = "tcp://127.0.0.1:26658"'))

    def test_patch_fails_closed_if_upstream_field_disappears(self):
        with self.assertRaises(ValueError):
            patch_comet_toml("[mempool]\nversion = \"v0\"\n")


if __name__ == "__main__":
    unittest.main()