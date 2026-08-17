import json
import os
import unittest
from unittest import mock

import wqpu_transport_relay as relay


class Reader(object):
    def __init__(self, lines):
        self.lines = list(lines)

    async def readline(self, *args, **kwargs):
        return self.lines.pop(0) if self.lines else b""


class Writer(object):
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.closed = True


class FakeVerifier(object):
    def __init__(self):
        self.seen = []

    def verify_hello(self, hello):
        self.seen.append(dict(hello))
        return {"ok": True}


class TransportNetworkUidTests(unittest.IsolatedAsyncioTestCase):
    def test_uid_is_read_from_stream_and_control_info(self):
        self.assertEqual(relay.hello_network_uid({"network_uid": "WQPU-ABC"}), "wqpu-abc")
        self.assertEqual(relay.hello_network_uid({"info": {"network_uid": "WQPU-DEF"}}), "wqpu-def")
        self.assertEqual(relay.hello_network_uid({"info": {}}), "")
        self.assertEqual(relay.hello_network_uid(None), "")

    async def test_replay_reader_returns_exact_first_line_once(self):
        base = Reader([b"second\n"])
        replay = relay._ReplayReader(base, b"first\n")
        self.assertEqual(await replay.readline(), b"first\n")
        self.assertEqual(await replay.readline(), b"second\n")

    async def _exercise_guard(self, hello, expected, should_call):
        original_mesh = relay.wqpu.Mesh
        calls = []
        verifier = FakeVerifier()

        class FakeMesh(object):
            async def handle_inbound(self, reader, writer):
                calls.append(await reader.readline())
                return "ok"

        try:
            relay.wqpu.Mesh = FakeMesh
            with mock.patch.dict(os.environ, {"WQPU_NETWORK_UID": expected}, clear=False), \
                 mock.patch.object(relay, "registry_identity_verifier", return_value=verifier):
                relay._install_network_uid_transport_guard()
                raw = (json.dumps(hello, separators=(",", ":")) + "\n").encode()
                writer = Writer()
                result = await FakeMesh().handle_inbound(Reader([raw]), writer)
            if should_call:
                self.assertEqual(result, "ok")
                self.assertEqual(calls, [raw])
                self.assertEqual(verifier.seen, [hello])
                self.assertEqual(bytes(writer.data), b"")
            else:
                self.assertEqual(calls, [])
                self.assertEqual(verifier.seen, [])
                self.assertIn(b"WQPU network identity mismatch", bytes(writer.data))
                self.assertTrue(writer.closed)
        finally:
            relay.wqpu.Mesh = original_mesh

    async def test_matching_uid_reaches_identity_verifier_and_existing_mesh(self):
        uid = "wqpu-" + "11" * 16
        await self._exercise_guard({"role": "dial", "network_uid": uid}, uid, True)

    async def test_wrong_uid_is_rejected_before_identity_verifier(self):
        await self._exercise_guard(
            {"role": "accept", "network_uid": "wqpu-" + "22" * 16},
            "wqpu-" + "11" * 16,
            False,
        )

    async def test_control_uid_can_be_carried_in_info(self):
        uid = "wqpu-" + "33" * 16
        await self._exercise_guard({"role": "control", "info": {"network_uid": uid}}, uid, True)


if __name__ == "__main__":
    unittest.main()
