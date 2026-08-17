import os
import unittest
from unittest import mock

import wqpu_wallet


class WalletFaucetTests(unittest.TestCase):
    def test_env_faucet_override_is_exposed(self):
        with mock.patch.dict(os.environ, {"WQPU_FAUCET_URL": "http://127.0.0.1:8787/faucet"}, clear=False):
            self.assertEqual(
                wqpu_wallet._network_faucet_url(),
                "http://127.0.0.1:8787/faucet",
            )

    def test_wallet_page_requests_faucet_before_registration(self):
        page = wqpu_wallet._html({
            "endpoint": "127.0.0.1:7443",
            "registerNode": True,
            "session": None,
            "faucetUrl": "http://127.0.0.1:8787/faucet",
        })
        self.assertIn("async function ensureFaucet(account)", page)
        self.assertIn("await ensureFaucet(account);", page)
        self.assertLess(
            page.index("await ensureFaucet(account);"),
            page.index("Confirm node registration"),
        )
        self.assertIn("eth_getTransactionReceipt", page)

    def test_no_faucet_url_is_noop_in_page_logic(self):
        page = wqpu_wallet._html({
            "endpoint": "127.0.0.1:7443",
            "registerNode": False,
            "session": None,
            "faucetUrl": "",
        })
        self.assertIn("if(!CFG.faucetUrl)return;", page)


if __name__ == "__main__":
    unittest.main()
