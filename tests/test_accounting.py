import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wqpu_accounting
import wqpu_meter


def clean_stats(ops=123456):
    return {
        "meter_version": 2,
        "llama_rpc_op_count": wqpu_meter.GGML_OP_COUNT,
        "invalid_frames": 0,
        "trailing_bytes": 0,
        "estimated_scalar_ops": ops,
        "node_executions": 7,
        "graph_compute_calls": 1,
        "graph_recompute_calls": 0,
    }


class FakeMesh(object):
    def __init__(self):
        self.chain = object()
        self.peer_info = {
            "worker-1": {
                "wallet": "0x" + "22" * 20,
                "hostname": "worker-one",
            }
        }


class FakePaymentSession(object):
    issued = []

    def __init__(self, chain):
        self.chain = chain

    def validate(self):
        return True

    def issue(self, wallet, units):
        self.__class__.issued.append((wallet, units))
        return {"kind": "fake-voucher", "units": units}


class AccountingTests(unittest.TestCase):
    def test_clean_v2_meter_is_eligible(self):
        self.assertTrue(wqpu_accounting.meter_is_eligible(clean_stats()))

    def test_meter_fails_closed_on_any_integrity_problem(self):
        cases = [
            {"meter_version": 1},
            {"llama_rpc_op_count": wqpu_meter.GGML_OP_COUNT + 1},
            {"invalid_frames": 1},
            {"trailing_bytes": 1},
            {"estimated_scalar_ops": 0},
            {"graph_compute_calls": 0, "graph_recompute_calls": 0},
        ]
        for override in cases:
            stats = clean_stats()
            stats.update(override)
            self.assertFalse(wqpu_accounting.meter_is_eligible(stats), override)

    def test_receipt_uses_estimated_scalar_ops_not_node_count(self):
        mesh = FakeMesh()
        tmp = tempfile.TemporaryDirectory()
        old_dir = wqpu_accounting.USAGE_DIR
        try:
            wqpu_accounting.USAGE_DIR = Path(tmp.name)
            with mock.patch.dict(os.environ, {"WQPU_AUTO_VOUCHERS": "0"}, clear=False):
                receipt, path = wqpu_accounting.save_usage_receipt(
                    mesh,
                    {"worker-1": clean_stats(987654)},
                )
            self.assertIsNotNone(path)
            worker = receipt["workers"][0]
            self.assertEqual(worker["prototype_compute_units"], 987654)
            self.assertEqual(worker["rpc"]["node_executions"], 7)
            self.assertTrue(worker["meter_eligible_for_prototype_voucher"])
            self.assertNotIn("voucher", worker)
        finally:
            wqpu_accounting.USAGE_DIR = old_dir
            tmp.cleanup()

    def test_invalid_meter_never_issues_voucher_even_when_requested(self):
        mesh = FakeMesh()
        tmp = tempfile.TemporaryDirectory()
        old_dir = wqpu_accounting.USAGE_DIR
        FakePaymentSession.issued = []
        stats = clean_stats(5000)
        stats["invalid_frames"] = 1
        try:
            wqpu_accounting.USAGE_DIR = Path(tmp.name)
            with mock.patch.object(wqpu_accounting, "PaymentSession", FakePaymentSession), \
                 mock.patch.dict(os.environ, {"WQPU_AUTO_VOUCHERS": "1"}, clear=False):
                receipt, _ = wqpu_accounting.save_usage_receipt(mesh, {"worker-1": stats})
            worker = receipt["workers"][0]
            self.assertFalse(worker["meter_eligible_for_prototype_voucher"])
            self.assertIn("voucher_error", worker)
            self.assertNotIn("voucher", worker)
            self.assertEqual(FakePaymentSession.issued, [])
        finally:
            wqpu_accounting.USAGE_DIR = old_dir
            tmp.cleanup()

    def test_valid_meter_issues_exact_estimated_units_when_opted_in(self):
        mesh = FakeMesh()
        tmp = tempfile.TemporaryDirectory()
        old_dir = wqpu_accounting.USAGE_DIR
        FakePaymentSession.issued = []
        try:
            wqpu_accounting.USAGE_DIR = Path(tmp.name)
            with mock.patch.object(wqpu_accounting, "PaymentSession", FakePaymentSession), \
                 mock.patch.dict(os.environ, {"WQPU_AUTO_VOUCHERS": "1"}, clear=False):
                receipt, _ = wqpu_accounting.save_usage_receipt(
                    mesh,
                    {"worker-1": clean_stats(7654321)},
                )
            wallet = mesh.peer_info["worker-1"]["wallet"]
            self.assertEqual(FakePaymentSession.issued, [(wallet, 7654321)])
            self.assertEqual(receipt["workers"][0]["voucher"]["units"], 7654321)
        finally:
            wqpu_accounting.USAGE_DIR = old_dir
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
