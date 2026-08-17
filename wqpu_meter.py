#!/usr/bin/env python3
"""Protocol-aware llama.cpp RPC usage meter for WQPU.

The meter parses only requester->worker RPC frames. It attributes graph execution
work to a concrete peer without inspecting prompts/model tensors semantically.
`node_executions` is a prototype accounting unit, not a fraud-proof FLOP measure.
"""

from __future__ import print_function

import time


RPC_HEADER_SIZE = 9  # uint8 command + native little-endian uint64 payload size
RPC_CMD_SET_TENSOR = 6
RPC_CMD_GRAPH_COMPUTE = 10
RPC_CMD_HELLO = 14
RPC_CMD_GRAPH_RECOMPUTE = 16
MAX_FRAME_SIZE = 1024 * 1024 * 1024


class MeterError(RuntimeError):
    pass


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
        self.last_graph_nodes = 0
        self.protocol_seen = False
        self.invalid_frames = 0

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
            self._frame(cmd, payload)

    def _frame(self, cmd, payload):
        self.requests += 1
        self.request_bytes += RPC_HEADER_SIZE + len(payload)
        if cmd == RPC_CMD_HELLO:
            self.protocol_seen = True
            return
        if cmd == RPC_CMD_SET_TENSOR:
            self.tensor_upload_bytes += len(payload)
            return
        if cmd == RPC_CMD_GRAPH_COMPUTE:
            self.graph_compute_calls += 1
            self.graph_payload_bytes += len(payload)
            if len(payload) >= 8:
                # serialize_graph(): device:uint32, n_nodes:uint32, then node ids/tensors.
                n_nodes = int.from_bytes(payload[4:8], "little")
                self.last_graph_nodes = n_nodes
                self.node_executions += n_nodes
            else:
                self.invalid_frames += 1
            return
        if cmd == RPC_CMD_GRAPH_RECOMPUTE:
            self.graph_recompute_calls += 1
            if self.last_graph_nodes:
                self.node_executions += self.last_graph_nodes
            else:
                self.invalid_frames += 1

    def snapshot(self):
        return {
            "requests": self.requests,
            "request_bytes": self.request_bytes,
            "graph_compute_calls": self.graph_compute_calls,
            "graph_recompute_calls": self.graph_recompute_calls,
            "graph_payload_bytes": self.graph_payload_bytes,
            "tensor_upload_bytes": self.tensor_upload_bytes,
            "node_executions": self.node_executions,
            "protocol_seen": self.protocol_seen,
            "invalid_frames": self.invalid_frames,
            "active_seconds": max(0.0, self.last_seen_at - self.started_at),
            "trailing_bytes": len(self.buffer),
        }


class UsageBook(object):
    def __init__(self):
        self.meters = {}

    def meter(self, peer_id):
        key = str(peer_id)
        if key not in self.meters:
            self.meters[key] = RPCRequestMeter()
        return self.meters[key]

    def snapshot(self):
        return {key: meter.snapshot() for key, meter in self.meters.items()}

    def prototype_units(self, peer_id):
        meter = self.meters.get(str(peer_id))
        return int(meter.node_executions) if meter else 0
