import asyncio
import json
import os
import unittest
from unittest import mock

import wqpu_autopay


FP_A = "aa" * 32
FP_B = "bb" * 32


class FakeChain(object):
    def __init__(self, relays=None):
        self.network = {"relays": list(relays or [])}


class AutoPayTests(unittest.TestCase):
    def make_mesh(self):
        mesh = wqpu_autopay.AutoPayChainMesh.__new__(wqpu_autopay.AutoPayChainMesh)
        mesh.me = "node-local"
        mesh.wallet = "0x" + "11" * 20
        mesh.controls = {}
        mesh.routes = {}
        mesh.outbound = {}
        mesh.chain = object()
        mesh.bootstrap_relays = []
        mesh.chain_peers = {}
        mesh.chain_nodes = {}
        mesh.verified_node_ids = set()
        return mesh

    def test_configured_relays_accepts_valid_pinned_relays_and_deduplicates(self):
        chain = FakeChain([
            {"host": "relay.example", "port": 7443, "fingerprint": "0x" + FP_A},
            {"host": "relay.example", "port": 7443, "fingerprint": FP_B},
            {"host": "bad.example", "port": 7443, "fingerprint": "short"},
            {"host": "", "port": 7443, "fingerprint": FP_A},
        ])
        with mock.patch.dict(os.environ, {"WQPU_RELAYS_JSON": ""}, clear=False):
            relays = wqpu_autopay.configured_relays(chain)
        self.assertEqual(len(relays), 1)
        self.assertEqual(relays[0]["host"], "relay.example")
        self.assertEqual(relays[0]["port"], 7443)
        self.assertEqual(relays[0]["fingerprint"], FP_A)
        self.assertTrue(relays[0]["relay"])

    def test_environment_relays_override_published_config(self):
        chain = FakeChain([
            {"host": "published.example", "port": 7443, "fingerprint": FP_A},
        ])
        override = json.dumps([
            {"host": "override.example", "port": 9443, "fingerprint": FP_B},
        ])
        with mock.patch.dict(os.environ, {"WQPU_RELAYS_JSON": override}, clear=False):
            relays = wqpu_autopay.configured_relays(chain)
        self.assertEqual(len(relays), 1)
        self.assertEqual(relays[0]["host"], "override.example")
        self.assertEqual(relays[0]["port"], 9443)
        self.assertEqual(relays[0]["fingerprint"], FP_B)

    def test_relay_advertised_worker_is_verified_against_chain_identity(self):
        mesh = self.make_mesh()
        wallet = "0x" + "22" * 20
        mesh.chain_nodes = {
            wallet: {"fingerprint": "0x" + FP_A},
        }
        nodes = [
            {"node_id": "worker-good", "wallet": wallet, "fingerprint": FP_A},
            {"node_id": "worker-bad", "wallet": wallet, "fingerprint": FP_B},
        ]
        with mock.patch.object(wqpu_autopay.runtime.ChainMesh, "merge_nodes", return_value=None):
            mesh.merge_nodes("relay.example:7443", nodes)
        self.assertIn("worker-good", mesh.verified_node_ids)
        self.assertNotIn("worker-bad", mesh.verified_node_ids)

    def test_hub_for_route_finds_bootstrap_relay(self):
        mesh = self.make_mesh()
        relay = {"host": "relay.example", "port": 7443, "fingerprint": FP_A}
        mesh.bootstrap_relays = [relay]
        with mock.patch.object(wqpu_autopay.wqpu, "load_peer_cache", return_value={}):
            found = mesh._hub_for_route("relay.example:7443")
        self.assertEqual(found, relay)

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
