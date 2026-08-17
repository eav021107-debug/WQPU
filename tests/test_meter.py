import struct
import unittest

import wqpu_meter


def frame(cmd, payload=b""):
    return bytes([cmd]) + len(payload).to_bytes(8, "little") + payload


def rpc_tensor(tensor_id, ne=(1, 1, 1, 1), op=2, src=()):
    raw = bytearray(wqpu_meter.RPC_TENSOR_SIZE)
    struct.pack_into("<Q", raw, 0, tensor_id)
    struct.pack_into("<I", raw, 8, 0)  # type
    struct.pack_into("<Q", raw, 12, 0)  # buffer
    struct.pack_into("<4I", raw, 20, *ne)
    struct.pack_into("<4I", raw, 36, 1, 1, 1, 1)
    struct.pack_into("<I", raw, 52, op)
    srcs = list(src)[:10] + [0] * (10 - len(src))
    struct.pack_into("<10Q", raw, 124, *srcs)
    return bytes(raw)


def graph_payload(nodes, tensors, device=0):
    return b"".join([
        int(device).to_bytes(4, "little"),
        len(nodes).to_bytes(4, "little"),
        b"".join(int(node).to_bytes(8, "little") for node in nodes),
        len(tensors).to_bytes(4, "little"),
        b"".join(tensors),
    ])


class MeterTests(unittest.TestCase):
    def test_fragmented_graph_compute_and_recompute(self):
        meter = wqpu_meter.RPCRequestMeter()
        graph = graph_payload(
            [2],
            [
                rpc_tensor(1, ne=(4, 3, 1, 1), op=0),
                rpc_tensor(2, ne=(5, 3, 1, 1), op=wqpu_meter.GGML_OP_MUL_MAT, src=(1,)),
            ],
        )
        raw = (
            frame(wqpu_meter.RPC_CMD_HELLO, b"hello")
            + frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph)
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
        # 15 output values * 4-wide dot product * (mul + add) = 120 scalar ops.
        self.assertTrue(stats["protocol_seen"])
        self.assertEqual(stats["graph_compute_calls"], 1)
        self.assertEqual(stats["graph_recompute_calls"], 2)
        self.assertEqual(stats["node_executions"], 3)
        self.assertEqual(stats["estimated_scalar_ops"], 120 * 3)
        self.assertEqual(stats["matmul_scalar_ops"], 120 * 3)
        self.assertEqual(stats["invalid_frames"], 0)
        self.assertEqual(stats["trailing_bytes"], 0)

    def test_generic_ops_scale_with_output_tensor_size(self):
        meter = wqpu_meter.RPCRequestMeter()
        graph = graph_payload([7], [rpc_tensor(7, ne=(2, 3, 4, 1), op=2)])
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph))
        stats = meter.snapshot()
        self.assertEqual(stats["node_executions"], 1)
        self.assertEqual(stats["estimated_scalar_ops"], 24)
        self.assertEqual(stats["generic_scalar_ops"], 24)

    def test_metadata_only_ops_are_not_billed(self):
        meter = wqpu_meter.RPCRequestMeter()
        graph = graph_payload([
            9,
        ], [rpc_tensor(9, ne=(1024, 1024, 1, 1), op=wqpu_meter.GGML_OP_RESHAPE)])
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph))
        stats = meter.snapshot()
        self.assertEqual(stats["node_executions"], 1)
        self.assertEqual(stats["estimated_scalar_ops"], 0)
        self.assertEqual(stats["metadata_node_executions"], 1)

    def test_flash_attention_uses_sequence_shapes(self):
        meter = wqpu_meter.RPCRequestMeter()
        graph = graph_payload(
            [3],
            [
                rpc_tensor(1, ne=(8, 2, 4, 1), op=0),
                rpc_tensor(2, ne=(8, 3, 4, 1), op=0),
                rpc_tensor(3, ne=(8, 2, 4, 1), op=wqpu_meter.GGML_OP_FLASH_ATTN_EXT, src=(1, 2)),
            ],
        )
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph))
        stats = meter.snapshot()
        self.assertEqual(stats["estimated_scalar_ops"], 4 * 8 * 2 * 3 * 4)
        self.assertEqual(stats["attention_scalar_ops"], stats["estimated_scalar_ops"])

    def test_recompute_is_tracked_per_device(self):
        meter = wqpu_meter.RPCRequestMeter()
        graph = graph_payload([4], [rpc_tensor(4, ne=(10, 1, 1, 1), op=2)], device=7)
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, graph))
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_RECOMPUTE, (8).to_bytes(4, "little")))
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_RECOMPUTE, (7).to_bytes(4, "little")))
        stats = meter.snapshot()
        self.assertEqual(stats["invalid_frames"], 1)
        self.assertEqual(stats["graph_recompute_calls"], 1)
        self.assertEqual(stats["estimated_scalar_ops"], 20)

    def test_tensor_upload_is_separate_from_compute_units(self):
        meter = wqpu_meter.RPCRequestMeter()
        meter.feed(frame(wqpu_meter.RPC_CMD_SET_TENSOR, b"a" * 123))
        stats = meter.snapshot()
        self.assertEqual(stats["tensor_upload_bytes"], 123)
        self.assertEqual(stats["estimated_scalar_ops"], 0)

    def test_malformed_graph_is_not_billable(self):
        meter = wqpu_meter.RPCRequestMeter()
        # Claims one node, but does not include its serialized tensor.
        bad = (0).to_bytes(4, "little") + (1).to_bytes(4, "little") + (99).to_bytes(8, "little") + (0).to_bytes(4, "little")
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_COMPUTE, bad))
        stats = meter.snapshot()
        self.assertEqual(stats["estimated_scalar_ops"], 0)
        self.assertEqual(stats["invalid_frames"], 1)
        self.assertEqual(stats["graph_compute_calls"], 0)

    def test_recompute_without_previous_graph_is_not_billable(self):
        meter = wqpu_meter.RPCRequestMeter()
        meter.feed(frame(wqpu_meter.RPC_CMD_GRAPH_RECOMPUTE, b"\x00" * 4))
        stats = meter.snapshot()
        self.assertEqual(stats["estimated_scalar_ops"], 0)
        self.assertEqual(stats["invalid_frames"], 1)

    def test_huge_frame_is_rejected(self):
        meter = wqpu_meter.RPCRequestMeter()
        bad = bytes([1]) + (wqpu_meter.MAX_FRAME_SIZE + 1).to_bytes(8, "little")
        with self.assertRaises(wqpu_meter.MeterError):
            meter.feed(bad)


if __name__ == "__main__":
    unittest.main()
