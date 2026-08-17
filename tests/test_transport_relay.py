import asyncio
import unittest

import wqpu_transport_relay


class TransportRelayTests(unittest.TestCase):
    def make_relay(self):
        relay = wqpu_transport_relay.TransportRelayMesh.__new__(
            wqpu_transport_relay.TransportRelayMesh
        )
        relay.me = "relay"
        relay.controls = {}
        return relay

    def test_usage_report_is_forwarded_to_connected_target(self):
        relay = self.make_relay()
        ctrl = object()
        relay.controls["requester"] = ctrl
        sent = []

        async def fake_send(actual, message):
            sent.append((actual, message))

        relay.send = fake_send
        message = {
            "type": "open",
            "service": "usage_report",
            "target": "requester",
            "report": {"kind": "signed"},
        }
        asyncio.run(relay.handle_open_request(message))
        self.assertEqual(sent, [(ctrl, message)])

    def test_payment_voucher_is_forwarded_to_connected_target(self):
        relay = self.make_relay()
        ctrl = object()
        relay.controls["worker"] = ctrl
        sent = []

        async def fake_send(actual, message):
            sent.append((actual, message))

        relay.send = fake_send
        message = {
            "type": "open",
            "service": "payment",
            "target": "worker",
            "voucher": {"kind": "voucher"},
        }
        asyncio.run(relay.handle_open_request(message))
        self.assertEqual(sent, [(ctrl, message)])

    def test_signed_status_is_fanned_out_except_to_source(self):
        relay = self.make_relay()
        source_ctrl = object()
        requester_ctrl = object()
        observer_ctrl = object()
        relay.controls.update({
            "worker": source_ctrl,
            "requester": requester_ctrl,
            "observer": observer_ctrl,
        })
        sent = []

        async def fake_send(actual, message):
            sent.append((actual, message))

        relay.send = fake_send
        info = {
            "wallet": "0x" + "11" * 20,
            "network_uid": "wqpu-" + "22" * 16,
            "load_bps": 2500,
            "status_attestation": {"kind": "signed"},
        }
        asyncio.run(relay.handle_open_request({
            "type": "open",
            "service": "status",
            "source": "worker",
            "info": info,
        }))
        self.assertEqual(len(sent), 2)
        self.assertEqual({row[0] for row in sent}, {requester_ctrl, observer_ctrl})
        for _, message in sent:
            self.assertEqual(message["type"], "nodes")
            self.assertEqual(message["nodes"][0]["node_id"], "worker")
            self.assertEqual(message["nodes"][0]["load_bps"], 2500)
        self.assertFalse(any(row[0] is source_ctrl for row in sent))

    def test_malformed_status_is_not_forwarded(self):
        relay = self.make_relay()
        relay.controls["requester"] = object()
        sent = []

        async def fake_send(actual, message):
            sent.append((actual, message))

        relay.send = fake_send
        asyncio.run(relay.handle_open_request({
            "type": "open",
            "service": "status",
            "source": "worker",
            "info": "not-an-object",
        }))
        self.assertEqual(sent, [])

    def test_unknown_or_missing_target_is_not_forwarded(self):
        relay = self.make_relay()
        sent = []

        async def fake_send(actual, message):
            sent.append((actual, message))

        relay.send = fake_send
        asyncio.run(relay.handle_open_request({
            "type": "open",
            "service": "payment",
            "target": "missing",
        }))
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
