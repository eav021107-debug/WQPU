#!/usr/bin/env python3
"""Protocol-aware requester-side meter for the pinned llama.cpp RPC stream.

Meter v2 parses serialized rpc_tensor graphs. Generic operations are estimated by
output tensor elements; matrix multiplication and flash attention get shape-aware
scalar-operation estimates. It remains prototype accounting, but a tiny graph node and
a huge matrix multiply are no longer charged as the same amount of work.

A logical llama.cpp request may use multiple TCP RPC sockets concurrently. Each physical
socket is parsed by its own RPCRequestMeter; UsageBook combines only completed meter
snapshots per provider. This prevents bytes from independent TCP streams from ever being
interleaved inside one frame parser.
"""

from __future__ import print_function

import hashlib
import struct
import time


RPC_HEADER_SIZE = 9
RPC_CMD_SET_TENSOR = 6
RPC_CMD_SET_TENSOR_HASH = 7
RPC_CMD_GRAPH_COMPUTE = 10
RPC_CMD_HELLO = 14
RPC_CMD_GRAPH_RECOMPUTE = 16
MAX_FRAME_SIZE = 1024 * 1024 * 1024
MAX_GRAPH_NODES = 1_000_000
MAX_GRAPH_TENSORS = 2_000_000
MAX_ESTIMATED_OPS = (1 << 63) - 1

# WQPU pins llama.cpp b10456 / RPC protocol major 5.
RPC_TENSOR_SIZE = 296
GGML_OP_COUNT = 101
GGML_OP_MUL_MAT = 29
GGML_OP_MUL_MAT_ID = 30
GGML_OP_OUT_PROD = 31
GGML_OP_RESHAPE = 36
GGML_OP_VIEW = 37
GGML_OP_PERMUTE = 38
GGML_OP_TRANSPOSE = 39
GGML_OP_FLASH_ATTN_EXT = 74

METADATA_ONLY_OPS = {
    0,
    GGML_OP_RESHAPE,
    GGML_OP_VIEW,
    GGML_OP_PERMUTE,
    GGML_OP_TRANSPOSE,
}
MATMUL_OPS = {GGML_OP_MUL_MAT, GGML_OP_MUL_MAT_ID, GGML_OP_OUT_PROD}

# These are the fields that are mathematically safe to merge across independent RPC
# sockets belonging to the same logical request/provider. Invariants describe the meter
# implementation itself; all work/error counters are additive. Diagnostic fields that are
# not used for payment matching are merged separately below.
METER_INVARIANT_FIELDS = ("meter_version", "llama_rpc_op_count")
METER_ADDITIVE_FIELDS = (
    "requests",
    "request_bytes",
    "graph_compute_calls",
    "graph_recompute_calls",
    "graph_payload_bytes",
    "tensor_upload_bytes",
    "node_executions",
    "estimated_scalar_ops",
    "matmul_scalar_ops",
    "attention_scalar_ops",
    "generic_scalar_ops",
    "metadata_node_executions",
    "invalid_frames",
    "trailing_bytes",
)


class MeterError(RuntimeError):
    pass


def _u32(data, offset):
    if offset < 0 or offset + 4 > len(data):
        raise MeterError("short uint32")
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data, offset):
    if offset < 0 or offset + 8 > len(data):
        raise MeterError("short uint64")
    return struct.unpack_from("<Q", data, offset)[0]


def _bounded_mul(*values):
    result = 1
    for value in values:
        value = int(value)
        if value < 0:
            raise MeterError("negative work dimension")
        if value == 0:
            return 0
        if result > MAX_ESTIMATED_OPS // value:
            raise MeterError("estimated compute overflow")
        result *= value
    return result


def _bounded_add(left, right):
    left, right = int(left), int(right)
    if left < 0 or right < 0 or left > MAX_ESTIMATED_OPS - right:
        raise MeterError("estimated compute overflow")
    return left + right


def _tensor_elements(ne):
    return _bounded_mul(*ne)


def _parse_tensor(raw):
    if len(raw) != RPC_TENSOR_SIZE:
        raise MeterError("bad rpc_tensor size")
    tensor_id = _u64(raw, 0)
    ne = struct.unpack_from("<4I", raw, 20)
    op = _u32(raw, 52)
    src = struct.unpack_from("<10Q", raw, 124)
    if tensor_id == 0:
        raise MeterError("zero tensor id")
    if op >= GGML_OP_COUNT:
        raise MeterError("unsupported ggml op {}".format(op))
    return {
        "id": tensor_id,
        "ne": tuple(int(value) for value in ne),
        "op": op,
        "src": tuple(int(value) for value in src),
    }


def _estimate_node(tensor, tensors):
    output_elements = _tensor_elements(tensor["ne"])
    op = tensor["op"]
    if op in METADATA_ONLY_OPS or output_elements == 0:
        return 0, "metadata"

    if op in MATMUL_OPS:
        src0 = tensors.get(tensor["src"][0])
        if src0:
            inner = int(src0["ne"][0])
            # Each result element has roughly `inner` multiply-accumulates.
            return _bounded_mul(2, output_elements, inner), "matmul"
        return output_elements, "generic"

    if op == GGML_OP_FLASH_ATTN_EXT:
        query = tensors.get(tensor["src"][0])
        key = tensors.get(tensor["src"][1])
        if query and key:
            d = int(query["ne"][0])
            nq = int(query["ne"][1])
            hq = int(query["ne"][2])
            batch = int(query["ne"][3])
            nk = int(key["ne"][1])
            # Approximate Q*K^T + score*V; uncertain softmax overhead is omitted.
            work = _bounded_mul(4, d, nq, nk, hq, batch)
            return max(output_elements, work), "attention"
        return output_elements, "generic"

    # Conservative lower estimate for all other compute ops.
    return output_elements, "generic"


def parse_graph(payload):
    if len(payload) < 12:
        raise MeterError("short graph payload")
    device = _u32(payload, 0)
    n_nodes = _u32(payload, 4)
    if n_nodes == 0 or n_nodes > MAX_GRAPH_NODES:
        raise MeterError("invalid graph node count")

    nodes_start = 8
    nodes_end = nodes_start + n_nodes * 8
    if nodes_end + 4 > len(payload):
        raise MeterError("truncated graph node list")
    node_ids = struct.unpack_from("<{}Q".format(n_nodes), payload, nodes_start)
    n_tensors = _u32(payload, nodes_end)
    if n_tensors == 0 or n_tensors > MAX_GRAPH_TENSORS:
        raise MeterError("invalid graph tensor count")

    tensor_start = nodes_end + 4
    expected_size = tensor_start + n_tensors * RPC_TENSOR_SIZE
    if expected_size != len(payload):
        raise MeterError("graph payload size mismatch")

    tensors = {}
    for index in range(n_tensors):
        start = tensor_start + index * RPC_TENSOR_SIZE
        tensor = _parse_tensor(payload[start:start + RPC_TENSOR_SIZE])
        if tensor["id"] in tensors:
            raise MeterError("duplicate tensor id")
        tensors[tensor["id"]] = tensor

    estimated = 0
    matmul = 0
    attention = 0
    generic = 0
    metadata_nodes = 0
    for node_id in node_ids:
        tensor = tensors.get(int(node_id))
        if not tensor:
            raise MeterError("graph node missing serialized tensor")
        work, category = _estimate_node(tensor, tensors)
        estimated = _bounded_add(estimated, work)
        if category == "matmul":
            matmul = _bounded_add(matmul, work)
        elif category == "attention":
            attention = _bounded_add(attention, work)
        elif category == "generic":
            generic = _bounded_add(generic, work)
        else:
            metadata_nodes += 1

    return {
        "device": device,
        "n_nodes": n_nodes,
        "n_tensors": n_tensors,
        "estimated_scalar_ops": estimated,
        "matmul_scalar_ops": matmul,
        "attention_scalar_ops": attention,
        "generic_scalar_ops": generic,
        "metadata_nodes": metadata_nodes,
        "fingerprint": hashlib.sha256(payload).hexdigest(),
    }


def merge_meter_snapshots(previous, current):
    """Combine independent physical-stream snapshots for one logical provider request.

    Any malformed/trailing stream remains visible in the aggregate, so the accounting
    predicate still fails closed. Meter implementation/version mismatches are rejected
    instead of silently combining incompatible units.
    """
    left = dict(previous or {})
    right = dict(current or {})
    if not left:
        return right
    if not right:
        return left

    for field in METER_INVARIANT_FIELDS:
        if int(left.get(field) or 0) != int(right.get(field) or 0):
            raise MeterError("meter snapshot invariant mismatch: {}".format(field))

    merged = dict(left)
    for field in METER_INVARIANT_FIELDS:
        merged[field] = int(left.get(field) or 0)
    for field in METER_ADDITIVE_FIELDS:
        merged[field] = _bounded_add(
            int(left.get(field) or 0), int(right.get(field) or 0)
        )

    merged["protocol_seen"] = bool(left.get("protocol_seen")) or bool(right.get("protocol_seen"))
    merged["active_seconds"] = float(left.get("active_seconds") or 0.0) + float(right.get("active_seconds") or 0.0)
    merged["last_graph_nodes"] = int(right.get("last_graph_nodes") or left.get("last_graph_nodes") or 0)
    merged["tracked_devices"] = max(
        int(left.get("tracked_devices") or 0), int(right.get("tracked_devices") or 0)
    )
    return merged


class RPCRequestMeter(object):
    def __init__(self):
        self.buffer = bytearray()
        self.started_at = time.time()
        self.last_seen_at = self.started_at
        self.requests = 0
        self.request_bytes = 0
        self.graph_compute_calls = 0
        self.graph_recompute_calls = 0
        self.graph_payload_bytes = 0
        self.tensor_upload_bytes = 0
        self.node_executions = 0
        self.estimated_scalar_ops = 0
        self.matmul_scalar_ops = 0
        self.attention_scalar_ops = 0
        self.generic_scalar_ops = 0
        self.metadata_node_executions = 0
        self.last_graph_nodes = 0
        self.protocol_seen = False
        self.invalid_frames = 0
        self.graphs = {}

    def feed(self, data):
        if not data:
            return
        self.last_seen_at = time.time()
        self.buffer.extend(data)
        while True:
            if len(self.buffer) < RPC_HEADER_SIZE:
                return
            cmd = int(self.buffer[0])
            size = int.from_bytes(self.buffer[1:9], "little")
            if size > MAX_FRAME_SIZE:
                self.invalid_frames += 1
                self.buffer.clear()
                raise MeterError("llama.cpp RPC frame is too large")
            frame_size = RPC_HEADER_SIZE + size
            if len(self.buffer) < frame_size:
                return
            payload = bytes(self.buffer[RPC_HEADER_SIZE:frame_size])
            del self.buffer[:frame_size]
            self.requests += 1
            self.request_bytes += frame_size
            try:
                self._frame(cmd, payload)
            except MeterError:
                # Preserve transport semantics but make the meter unambiguously ineligible.
                self.invalid_frames += 1
                raise

    def _add_graph_work(self, graph):
        self.node_executions = _bounded_add(self.node_executions, graph["n_nodes"])
        self.estimated_scalar_ops = _bounded_add(self.estimated_scalar_ops, graph["estimated_scalar_ops"])
        self.matmul_scalar_ops = _bounded_add(self.matmul_scalar_ops, graph["matmul_scalar_ops"])
        self.attention_scalar_ops = _bounded_add(self.attention_scalar_ops, graph["attention_scalar_ops"])
        self.generic_scalar_ops = _bounded_add(self.generic_scalar_ops, graph["generic_scalar_ops"])
        self.metadata_node_executions = _bounded_add(self.metadata_node_executions, graph["metadata_nodes"])
        self.last_graph_nodes = graph["n_nodes"]

    def _frame(self, cmd, payload):
        if cmd == RPC_CMD_HELLO:
            self.protocol_seen = True
            return
        if cmd in (RPC_CMD_SET_TENSOR, RPC_CMD_SET_TENSOR_HASH):
            self.tensor_upload_bytes += len(payload)
            return
        if cmd == RPC_CMD_GRAPH_COMPUTE:
            self.graph_payload_bytes += len(payload)
            try:
                graph = parse_graph(payload)
                self.graphs[graph["device"]] = graph
                self.graph_compute_calls += 1
                self._add_graph_work(graph)
            except MeterError:
                self.invalid_frames += 1
            return
        if cmd == RPC_CMD_GRAPH_RECOMPUTE:
            if len(payload) < 4:
                self.invalid_frames += 1
                return
            device = _u32(payload, 0)
            graph = self.graphs.get(device)
            if not graph:
                self.invalid_frames += 1
                return
            self.graph_recompute_calls += 1
            self._add_graph_work(graph)

    def snapshot(self):
        return {
            "meter_version": 2,
            "requests": self.requests,
            "request_bytes": self.request_bytes,
            "graph_compute_calls": self.graph_compute_calls,
            "graph_recompute_calls": self.graph_recompute_calls,
            "graph_payload_bytes": self.graph_payload_bytes,
            "tensor_upload_bytes": self.tensor_upload_bytes,
            "node_executions": self.node_executions,
            "estimated_scalar_ops": self.estimated_scalar_ops,
            "matmul_scalar_ops": self.matmul_scalar_ops,
            "attention_scalar_ops": self.attention_scalar_ops,
            "generic_scalar_ops": self.generic_scalar_ops,
            "metadata_node_executions": self.metadata_node_executions,
            "protocol_seen": self.protocol_seen,
            "invalid_frames": self.invalid_frames,
            "last_graph_nodes": self.last_graph_nodes,
            "tracked_devices": len(self.graphs),
            "llama_rpc_op_count": GGML_OP_COUNT,
            "active_seconds": max(0.0, self.last_seen_at - self.started_at),
            "trailing_bytes": len(self.buffer),
        }


class UsageBook(object):
    def __init__(self):
        # Each list entry is one physical TCP RPC socket. This is deliberate: feeding
        # chunks from independent sockets into one parser can create synthetic frames.
        self.meters = {}

    def meter(self, peer_id):
        key = str(peer_id)
        meter = RPCRequestMeter()
        self.meters.setdefault(key, []).append(meter)
        return meter

    def snapshot(self):
        result = {}
        for key, meters in self.meters.items():
            aggregate = {}
            for meter in meters:
                aggregate = merge_meter_snapshots(aggregate, meter.snapshot())
            result[key] = aggregate
        return result

    def prototype_units(self, peer_id):
        meters = self.meters.get(str(peer_id)) or []
        return sum(int(meter.estimated_scalar_ops) for meter in meters)
