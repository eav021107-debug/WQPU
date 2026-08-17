import tempfile
import unittest
from pathlib import Path

import wqpu_claim
import wqpu_vouchers


REQUESTER = "0x" + "11" * 20
PROVIDER = "0x" + "22" * 20
SESSION_KEY = "0x" + "33" * 20
MARKET = "0x" + "44" * 20
SESSION_ID = "0x" + "55" * 32


def package(amount=100, units=1000):
    return {
        "version": 1,
        "kind": "wqpu-provider-voucher",
        "market": MARKET,
        "requester": REQUESTER,
        "provider": PROVIDER,
        "session_key": SESSION_KEY,
        "session_id": SESSION_ID,
        "max_amount": 1_000_000,
        "price_per_million_units": 100_000,
        "valid_until": 9999999999,
        "cumulative_amount": amount,
        "cumulative_units": units,
        "voucher_signature": "0x" + "aa" * 64,
        "authorization_signature": "0x" + "bb" * 65,
    }


class FakeClient(object):
    def rpc(self, method, params):
        if method == "web3_sha3":
            return "0x12345678" + "00" * 28
        if method == "eth_call":
            return "0x"
        raise AssertionError(method)


class ClaimTests(unittest.TestCase):
    def test_struct_claim_calldata_has_correct_dynamic_offsets(self):
        data = wqpu_claim.claim_calldata(FakeClient(), package())
        self.assertTrue(data.startswith("0x12345678"))
        args = data[10:]
        words = [args[i:i + 64] for i in range(0, 11 * 64, 64)]
        self.assertEqual(int(words[9], 16), 11 * 32)
        # 64-byte voucher = one length word + two data words = 96 bytes.
        self.assertEqual(int(words[10], 16), 11 * 32 + 96)

    def test_simulation_uses_market_contract(self):
        self.assertTrue(wqpu_claim.simulate_claim(FakeClient(), package()))

    def test_provider_inbox_keeps_only_newer_cumulative_voucher(self):
        tmp = tempfile.TemporaryDirectory()
        old_home = wqpu_vouchers.HOME
        old_file = wqpu_vouchers.INBOX_FILE
        try:
            wqpu_vouchers.HOME = Path(tmp.name)
            wqpu_vouchers.INBOX_FILE = Path(tmp.name) / "inbox.json"
            self.assertTrue(wqpu_vouchers.accept(PROVIDER, package(100, 1000)))
            self.assertTrue(wqpu_vouchers.accept(PROVIDER, package(150, 1500)))
            self.assertFalse(wqpu_vouchers.accept(PROVIDER, package(150, 1500)))
            with self.assertRaises(wqpu_vouchers.VoucherInboxError):
                wqpu_vouchers.accept(PROVIDER, package(120, 1200))
            rows = wqpu_vouchers.pending(PROVIDER)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["cumulative_amount"], 150)
            self.assertEqual(rows[0]["cumulative_units"], 1500)
        finally:
            wqpu_vouchers.HOME = old_home
            wqpu_vouchers.INBOX_FILE = old_file
            tmp.cleanup()

    def test_provider_inbox_rejects_other_wallet(self):
        with self.assertRaises(wqpu_vouchers.VoucherInboxError):
            wqpu_vouchers.accept("0x" + "99" * 20, package())


if __name__ == "__main__":
    unittest.main()
