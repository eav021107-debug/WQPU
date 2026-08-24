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
