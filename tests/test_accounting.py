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
        "requests": 3,
        "request_bytes": 4096,
        "invalid_frames": 0,
        "trailing_bytes": 0,
        "estimated_scalar_ops": ops,
        "node_executions": 7,
        "graph_compute_calls": 1,
        "graph_recompute_calls": 0,
        "graph_payload_bytes": 2048,
        "tensor_upload_bytes": 512,
        "matmul_scalar_ops": ops,
        "attention_scalar_ops": 0,
        "generic_scalar_ops": 0,
        "metadata_node_executions": 0,
    }


class FakeChain(object):
    network = {}


class FakeMesh(object):
    def __init__(self, provider_stats=None):
        self.chain = FakeChain()
        self.current_request_id = "ab" * 16
        self.peer_info = {
            "worker-1": {
                "wallet": "0x" + "22" * 20,
                "hostname": "worker-one",
            }
        }
        self.provider_usage_reports = {}
        if provider_stats is not None:
            self.provider_usage_reports[self.current_request_id] = {
                "worker-1": {
                    "provider_node_id": "worker-1",
                    "provider_wallet": self.peer_info["worker-1"]["wallet"],
                    "signature": "signed",
                    "rpc": dict(provider_stats),
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

    def test_matching_dual_meters_are_accepted(self):
        stats = clean_stats(999)
        self.assertTrue(wqpu_accounting.meters_match(stats, dict(stats)))
        changed = dict(stats)
        changed["estimated_scalar_ops"] += 1
        self.assertFalse(wqpu_accounting.meters_match(stats, changed))

    def test_receipt_uses_estimated_scalar_ops_not_node_count(self):
        stats = clean_stats(987654)
        mesh = FakeMesh(provider_stats=stats)
        tmp = tempfile.TemporaryDirectory()
        old_dir = wqpu_accounting.USAGE_DIR
        try:
            wqpu_accounting.USAGE_DIR = Path(tmp.name)
            with mock.patch.dict(os.environ, {"WQPU_AUTO_VOUCHERS": "0"}, clear=False):
                receipt, path = wqpu_accounting.save_usage_receipt(mesh, {"worker-1": stats})
            self.assertIsNotNone(path)
            worker = receipt["workers"][0]
            self.assertEqual(worker["prototype_compute_units"], 987654)
            self.assertEqual(worker["rpc"]["node_executions"], 7)
            self.assertTrue(worker["requester_meter_eligible"])
            self.assertTrue(worker["provider_attestation_received"])
            self.assertTrue(worker["dual_meter_match"])
            self.assertNotIn("voucher", worker)
        finally:
            wqpu_accounting.USAGE_DIR = old_dir
            tmp.cleanup()

    def test_invalid_meter_never_issues_voucher_even_when_requested(self):
        stats = clean_stats(5000)
        stats["invalid_frames"] = 1
        mesh = FakeMesh(provider_stats=stats)
        tmp = tempfile.TemporaryDirectory()
        old_dir = wqpu_accounting.USAGE_DIR
        FakePaymentSession.issued = []
        try:
            wqpu_accounting.USAGE_DIR = Path(tmp.name)
            with mock.patch.object(wqpu_accounting, "PaymentSession", FakePaymentSession), \
                 mock.patch.dict(os.environ, {"WQPU_AUTO_VOUCHERS": "1"}, clear=False):
                receipt, _ = wqpu_accounting.save_usage_receipt(mesh, {"worker-1": stats})
            worker = receipt["workers"][0]
            self.assertFalse(worker["requester_meter_eligible"])
            self.assertIn("voucher_error", worker)
            self.assertNotIn("voucher", worker)
            self.assertEqual(FakePaymentSession.issued, [])
        finally:
            wqpu_accounting.USAGE_DIR = old_dir
            tmp.cleanup()

    def test_missing_or_mismatched_worker_meter_blocks_voucher(self):
        requester_stats = clean_stats(7000)
        for provider_stats in (None, clean_stats(7001)):
            mesh = FakeMesh(provider_stats=provider_stats)
            tmp = tempfile.TemporaryDirectory()
            old_dir = wqpu_accounting.USAGE_DIR
            FakePaymentSession.issued = []
            try:
                wqpu_accounting.USAGE_DIR = Path(tmp.name)
                with mock.patch.object(wqpu_accounting, "PaymentSession", FakePaymentSession), \
                     mock.patch.dict(os.environ, {"WQPU_AUTO_VOUCHERS": "1"}, clear=False):
                    receipt, _ = wqpu_accounting.save_usage_receipt(
                        mesh, {"worker-1": requester_stats}
                    )
                worker = receipt["workers"][0]
                self.assertNotIn("voucher", worker)
                self.assertIn("voucher_error", worker)
                self.assertEqual(FakePaymentSession.issued, [])
            finally:
                wqpu_accounting.USAGE_DIR = old_dir
                tmp.cleanup()

    def test_valid_matching_dual_meter_issues_exact_units_when_opted_in(self):
        stats = clean_stats(7654321)
        mesh = FakeMesh(provider_stats=stats)
        tmp = tempfile.TemporaryDirectory()
        old_dir = wqpu_accounting.USAGE_DIR
        FakePaymentSession.issued = []
        try:
            wqpu_accounting.USAGE_DIR = Path(tmp.name)
            with mock.patch.object(wqpu_accounting, "PaymentSession", FakePaymentSession), \
                 mock.patch.dict(os.environ, {"WQPU_AUTO_VOUCHERS": "1"}, clear=False):
                receipt, _ = wqpu_accounting.save_usage_receipt(mesh, {"worker-1": stats})
            wallet = mesh.peer_info["worker-1"]["wallet"]
            self.assertEqual(FakePaymentSession.issued, [(wallet, 7654321)])
            self.assertEqual(receipt["workers"][0]["voucher"]["units"], 7654321)
        finally:
            wqpu_accounting.USAGE_DIR = old_dir
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
