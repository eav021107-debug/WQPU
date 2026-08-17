import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wqpu_runtime


PROVIDER = "0x" + "11" * 20
REQUESTER = "0x" + "22" * 20
MARKET = "0x" + "33" * 20
SESSION = "0x" + "44" * 32


def package(amount=100, units=1000, provider=PROVIDER):
    return {
        "version": 2,
        "kind": "wqpu-provider-voucher",
        "market": MARKET,
        "requester": REQUESTER,
        "provider": provider,
        "session_key": "0x" + "55" * 20,
        "session_id": SESSION,
        "max_amount": 1000000,
        "price_per_million_units": 100000,
        "valid_until": 9999999999,
        "cumulative_amount": amount,
        "cumulative_units": units,
        "voucher_signature": "0x" + "aa" * 64,
        "authorization_signature": "0x" + "bb" * 65,
    }


class DummyChain(object):
    network = {"market": MARKET}


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_file = wqpu_runtime.VOUCHER_INBOX_FILE
        wqpu_runtime.VOUCHER_INBOX_FILE = Path(self.tmp.name) / "inbox.json"
        self.mesh = object.__new__(wqpu_runtime.ChainMesh)
        self.mesh.wallet = PROVIDER
        self.mesh.chain = DummyChain()

    def tearDown(self):
        wqpu_runtime.VOUCHER_INBOX_FILE = self.old_file
        self.tmp.cleanup()

    def test_only_latest_cumulative_voucher_is_kept(self):
        with mock.patch.object(wqpu_runtime, "configured_market", return_value=MARKET):
            self.assertTrue(self.mesh.receive_payment_voucher(package(100, 1000)))
            self.assertFalse(self.mesh.receive_payment_voucher(package(90, 900)))
            self.assertTrue(self.mesh.receive_payment_voucher(package(150, 1500)))

        inbox = wqpu_runtime.load_voucher_inbox()
        self.assertEqual(len(inbox), 1)
        saved = next(iter(inbox.values()))
        self.assertEqual(saved["cumulative_amount"], 150)
        self.assertEqual(saved["cumulative_units"], 1500)

    def test_voucher_for_another_provider_is_rejected(self):
        other = "0x" + "66" * 20
        with mock.patch.object(wqpu_runtime, "configured_market", return_value=MARKET):
            self.assertFalse(self.mesh.receive_payment_voucher(package(provider=other)))
        self.assertEqual(wqpu_runtime.load_voucher_inbox(), {})

    def test_wrong_market_is_rejected(self):
        with mock.patch.object(wqpu_runtime, "configured_market", return_value="0x" + "77" * 20):
            self.assertFalse(self.mesh.receive_payment_voucher(package()))
        self.assertEqual(wqpu_runtime.load_voucher_inbox(), {})


if __name__ == "__main__":
    unittest.main()
