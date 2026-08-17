import unittest

import wqpu_relayer


class FaucetPolicyTests(unittest.TestCase):
    def test_top_up_only_returns_missing_amount(self):
        self.assertEqual(wqpu_relayer.top_up_amount(1000, 0), 1000)
        self.assertEqual(wqpu_relayer.top_up_amount(1000, 250), 750)
        self.assertEqual(wqpu_relayer.top_up_amount(1000, 1000), 0)
        self.assertEqual(wqpu_relayer.top_up_amount(1000, 1500), 0)

    def test_top_up_never_goes_negative(self):
        self.assertEqual(wqpu_relayer.top_up_amount(-1, 0), 0)
        self.assertEqual(wqpu_relayer.top_up_amount(10, -5), 10)

    def test_hex_uint_parses_rpc_results(self):
        self.assertEqual(wqpu_relayer._hex_uint("0x10"), 16)
        self.assertEqual(wqpu_relayer._hex_uint("25"), 25)
        with self.assertRaises(wqpu_relayer.RelayerError):
            wqpu_relayer._hex_uint("bad-value")


if __name__ == "__main__":
    unittest.main()
