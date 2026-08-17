import unittest

import wqpu_meter


def frame(cmd, payload=b""):
    return bytes([cmd]) + len(payload).to_bytes(8, "little") + payload


def graph_payload(nodes):
    return (0).to_bytes(4, "little") + int(nodes).to_bytes(4, "little") + b"x" * 16


class MeterTests(unittest.TestCase):
    def test_fragmented_graph_compute_and_recompute(self):
        meter = wqpu_meter.RPCRequestMeter()
        raw = (
            frame(wqpu_meter.RPC_CMD_HELLO, b"hello")
            + frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph_payload(37))
            + frame(wqpu_meter.RPC_CMD_GRAPH_RECOMPUTE, (0).to_bytes(4, "little"))
            + frame(wqpu_meter.RPC_CMD_GRAPH_RECOMPUTE, (0).to_bytes(4, "little"))
        )
        for cut in (1, 2, 7, 3, 19, 5, 1000):
            if not raw:
                break
            meter.feed(raw[:cut])
            raw = raw[cut:]
        if raw:
            meter.feed(raw)

        stats = meter.snapshot()
        self.assertTrue(stats["protocol_seen"])
        self.assertEqual(stats["graph_compute_calls"], 1)
        self.assertEqual(stats["graph_recompute_calls"], 2)
        self.assertEqual(stats["node_executions"], 37 * 3)
        self.assertEqual(stats["invalid_frames"], 0)
        self.assertEqual(stats["trailing_bytes"], 0)

    def test_tensor_upload_is_separate_from_compute_units(self):
        meter = wqpu_meter.RPCRequestMeter()
        meter.feed(frame(wqpu_meter.RPC_CMD_SET_TENSOR, b"a" * 123))
        stats = meter.snapshot()
        self.assertEqual(stats["tensor_upload_bytes"], 123)
        self.assertEqual(stats["node_executions"], 0)

    def test_recompute_without_previous_graph_is_not_billable(self):
        meter = wqpu_meter.RPCRequestMeter()
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_RECOMPUTE, b"\x00" * 4))
        stats = meter.snapshot()
        self.assertEqual(stats["node_executions"], 0)
        self.assertEqual(stats["invalid_frames"], 1)

    def test_huge_frame_is_rejected(self):
        meter = wqpu_meter.RPCRequestMeter()
        bad = bytes([1]) + (wqpu_meter.MAX_FRAME_SIZE + 1).to_bytes(8, "little")
        with self.assertRaises(wqpu_meter.MeterError):
            meter.feed(bad)


if __name__ == "__main__":
    unittest.main()
