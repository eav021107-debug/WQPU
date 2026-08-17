import asyncio
import os
import unittest
from unittest import mock

import wqpu_autopay


class AutoPayTests(unittest.TestCase):
    def make_mesh(self):
        mesh = wqpu_autopay.AutoPayChainMesh.__new__(wqpu_autopay.AutoPayChainMesh)
        mesh.me = "node-local"
        mesh.wallet = "0x" + "11" * 20
        mesh.controls = {}
        mesh.routes = {}
        mesh.outbound = {}
        mesh.chain = object()
        return mesh

    def test_local_payment_service_stores_voucher(self):
        mesh = self.make_mesh()
        with mock.patch.object(wqpu_autopay, "accept_voucher", return_value=True) as accept, \
             mock.patch.dict(os.environ, {"WQPU_AUTO_CLAIM": "0"}, clear=False):
            ok = asyncio.run(mesh._route_payment(mesh.me, {"kind": "voucher"}))
        self.assertTrue(ok)
        accept.assert_called_once_with(mesh.wallet, {"kind": "voucher"})

    def test_payment_prefers_direct_control_route(self):
        mesh = self.make_mesh()
        ctrl = object()
        mesh.controls["worker-1"] = ctrl
        sent = []

        async def fake_send(actual_ctrl, message):
            sent.append((actual_ctrl, message))

        mesh.send = fake_send
        ok = asyncio.run(mesh._route_payment("worker-1", {"kind": "voucher"}))
        self.assertTrue(ok)
        self.assertEqual(sent[0][0], ctrl)
        self.assertEqual(sent[0][1]["type"], "open")
        self.assertEqual(sent[0][1]["service"], "payment")
        self.assertEqual(sent[0][1]["target"], "worker-1")

    def test_payment_uses_existing_rpc_route_when_not_direct(self):
        mesh = self.make_mesh()
        ctrl = object()
        mesh.routes["worker-2"] = {"route-a"}
        mesh.outbound["route-a"] = ctrl
        sent = []

        async def fake_send(actual_ctrl, message):
            sent.append((actual_ctrl, message))

        mesh.send = fake_send
        ok = asyncio.run(mesh._route_payment("worker-2", {"kind": "voucher"}))
        self.assertTrue(ok)
        self.assertEqual(sent[0][0], ctrl)
        self.assertEqual(sent[0][1]["target"], "worker-2")

    def test_trace_breaks_payment_loops(self):
        mesh = self.make_mesh()
        ok = asyncio.run(mesh._route_payment("worker-x", {}, trace=[mesh.me]))
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
