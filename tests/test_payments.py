import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wqpu_payments


REQUESTER = "0x" + "11" * 20
PROVIDER = "0x" + "22" * 20
SESSION_KEY = "0x" + "33" * 20
MARKET = "0x" + "44" * 20
SESSION_ID = "0x" + "55" * 32


class FakeChain(object):
    def __init__(self, price=1_000_000):
        self.price = price

    def chain_id(self):
        return "0x7a69"

    def global_price(self):
        return self.price

    def rpc(self, method, params):
        if method == "eth_getBlockByNumber":
            return {"timestamp": hex(1000)}
        raise AssertionError(method)


def session():
    return {
        "requester": REQUESTER,
        "market": MARKET,
        "chain_id": "0x7a69",
        "session_key": SESSION_KEY,
        "session_id": SESSION_ID,
        "max_amount": 10_000_000,
        "price_per_million_units": 1_000_000,
        "valid_until": 5000,
        "authorization_signature": "0x" + "aa" * 65,
    }


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_state = wqpu_payments.PAYMENT_STATE
        wqpu_payments.PAYMENT_STATE = Path(self.tmp.name) / "payments.json"

    def tearDown(self):
        wqpu_payments.PAYMENT_STATE = self.old_state
        self.tmp.cleanup()

    def test_cumulative_vouchers_only_add_new_work(self):
        chain = FakeChain()
        with mock.patch.object(wqpu_payments, "session_address", return_value=SESSION_KEY), \
             mock.patch.object(wqpu_payments, "escrow_balance", return_value=10_000_000), \
             mock.patch.object(wqpu_payments, "session_spent", return_value=0), \
             mock.patch.object(wqpu_payments, "sign_provider_voucher", return_value="0x" + "bb" * 64):
            payments = wqpu_payments.PaymentSession(chain, session())
            first = payments.issue(PROVIDER, 1_000_000)
            second = payments.issue(PROVIDER, 500_000)

        self.assertEqual(first["cumulative_units"], 1_000_000)
        self.assertEqual(first["cumulative_amount"], 1_000_000)
        self.assertEqual(second["cumulative_units"], 1_500_000)
        self.assertEqual(second["cumulative_amount"], 1_500_000)
        self.assertEqual(payments.local_spent(), 1_500_000)

    def test_outstanding_vouchers_cannot_overcommit_escrow(self):
        chain = FakeChain()
        with mock.patch.object(wqpu_payments, "session_address", return_value=SESSION_KEY), \
             mock.patch.object(wqpu_payments, "escrow_balance", return_value=1_000_000), \
             mock.patch.object(wqpu_payments, "session_spent", return_value=0), \
             mock.patch.object(wqpu_payments, "sign_provider_voucher", return_value="0x" + "bb" * 64):
            payments = wqpu_payments.PaymentSession(chain, session())
            payments.issue(PROVIDER, 750_000)
            with self.assertRaises(wqpu_payments.PaymentError):
                payments.issue("0x" + "66" * 20, 500_000)

    def test_network_price_change_invalidates_session(self):
        chain = FakeChain(price=2_000_000)
        with mock.patch.object(wqpu_payments, "session_address", return_value=SESSION_KEY):
            payments = wqpu_payments.PaymentSession(chain, session())
            with self.assertRaises(wqpu_payments.PaymentError):
                payments.validate()

    def test_local_state_behind_chain_is_rejected(self):
        chain = FakeChain()
        with mock.patch.object(wqpu_payments, "session_address", return_value=SESSION_KEY), \
             mock.patch.object(wqpu_payments, "escrow_balance", return_value=10_000_000), \
             mock.patch.object(wqpu_payments, "session_spent", return_value=500_000), \
             mock.patch.object(wqpu_payments, "sign_provider_voucher", return_value="0x" + "bb" * 64):
            payments = wqpu_payments.PaymentSession(chain, session())
            with self.assertRaises(wqpu_payments.PaymentError):
                payments.issue(PROVIDER, 1_000_000)


if __name__ == "__main__":
    unittest.main()
