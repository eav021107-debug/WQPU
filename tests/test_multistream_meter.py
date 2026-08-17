import asyncio
import unittest

import wqpu_accounting
import wqpu_multistream


def stats(requests=1, ops=10, graphs=1, op_count=101, invalid=0, trailing=0):
    return {
        "meter_version": 2,
        "llama_rpc_op_count": op_count,
        "requests": requests,
        "request_bytes": requests * 100,
        "graph_compute_calls": graphs,
        "graph_recompute_calls": 0,
        "graph_payload_bytes": graphs * 50,
        "tensor_upload_bytes": requests * 20,
        "node_executions": graphs * 2,
        "estimated_scalar_ops": ops,
        "matmul_scalar_ops": ops,
        "attention_scalar_ops": 0,
        "generic_scalar_ops": 0,
        "metadata_node_executions": 0,
        "invalid_frames": invalid,
        "trailing_bytes": trailing,
        "protocol_seen": True,
        "active_seconds": 0.1,
        "last_graph_nodes": 2,
        "tracked_devices": 1,
    }


class FakeMesh(object):
    def __init__(self):
        self.me = "requester"
        self.provider_usage_reports = {}
        self._snapshot = {}

    def _receive_usage_report(self, report):
        if not report.get("valid", True):
            return False
        request_id = report["request_id"]
        provider = report["provider_node_id"]
        self.provider_usage_reports.setdefault(request_id, {})[provider] = dict(report)
        return True

    def end_usage(self):
        return dict(self._snapshot)


wqpu_multistream.install(FakeMesh)


class MultiStreamTests(unittest.TestCase):
    def report(self, stream_id, rpc, valid=True, request_id="a" * 32):
        return {
            "kind": "wqpu-worker-usage-attestation",
            "request_id": request_id,
            "stream_id": stream_id,
            "provider_node_id": "worker",
            "provider_wallet": "0x" + "11" * 20,
            "rpc": dict(rpc),
            "signature": "sig-" + stream_id,
            "valid": valid,
        }

    def test_verified_streams_are_aggregated(self):
        mesh = FakeMesh()
        self.assertTrue(mesh._receive_usage_report(self.report("s1", stats(1, 10, 1))))
        self.assertTrue(mesh._receive_usage_report(self.report("s2", stats(2, 20, 2))))
        saved = mesh.provider_usage_reports["a" * 32]["worker"]
        self.assertEqual(saved["verified_stream_count"], 2)
        self.assertEqual(saved["rpc"]["requests"], 3)
        self.assertEqual(saved["rpc"]["estimated_scalar_ops"], 30)
        self.assertEqual(saved["rpc"]["graph_compute_calls"], 3)

    def test_replayed_stream_is_idempotent(self):
        mesh = FakeMesh()
        report = self.report("same", stats(1, 10, 1))
        self.assertTrue(mesh._receive_usage_report(report))
        self.assertTrue(mesh._receive_usage_report(dict(report)))
        saved = mesh.provider_usage_reports["a" * 32]["worker"]
        self.assertEqual(saved["verified_stream_count"], 1)
        self.assertEqual(saved["rpc"]["estimated_scalar_ops"], 10)

    def test_unverified_stream_is_never_aggregated(self):
        mesh = FakeMesh()
        self.assertTrue(mesh._receive_usage_report(self.report("good", stats(1, 10, 1))))
        self.assertFalse(mesh._receive_usage_report(self.report("bad", stats(9, 900, 9), valid=False)))
        saved = mesh.provider_usage_reports["a" * 32]["worker"]
        self.assertEqual(saved["verified_stream_count"], 1)
        self.assertEqual(saved["rpc"]["estimated_scalar_ops"], 10)

    def test_invariant_mismatch_restores_previous_aggregate(self):
        mesh = FakeMesh()
        self.assertTrue(mesh._receive_usage_report(self.report("good", stats(1, 10, 1))))
        self.assertFalse(mesh._receive_usage_report(self.report("wrong", stats(1, 20, 1, op_count=999))))
        saved = mesh.provider_usage_reports["a" * 32]["worker"]
        self.assertEqual(saved["verified_stream_count"], 1)
        self.assertEqual(saved["rpc"]["llama_rpc_op_count"], 101)
        self.assertEqual(saved["rpc"]["estimated_scalar_ops"], 10)

    def test_wait_requires_exact_requester_aggregate(self):
        async def scenario():
            mesh = FakeMesh()
            request_id = "b" * 32
            first = stats(1, 10, 1)
            second = stats(2, 20, 2)
            expected = wqpu_multistream.merge_rpc_stats(first, second)
            mesh._snapshot = {"worker": expected}
            mesh.end_usage()  # stores final requester snapshot in patched method
            mesh._receive_usage_report(self.report("s1", first, request_id=request_id))

            task = asyncio.create_task(mesh.wait_provider_reports(["worker"], request_id, 1.0))
            await asyncio.sleep(0.1)
            self.assertFalse(task.done(), "wait returned after only a partial worker stream")
            mesh._receive_usage_report(self.report("s2", second, request_id=request_id))
            self.assertTrue(await task)
            actual = mesh.provider_usage_reports[request_id]["worker"]["rpc"]
            self.assertTrue(wqpu_accounting.meters_match(expected, actual))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
