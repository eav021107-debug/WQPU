import unittest

import wqpu_network_guard as guard


class FakeChain(object):
    def __init__(self, uid):
        self.network = {"network_uid": uid}


class FakeRuntime(object):
    class ChainMesh(object):
        def __init__(self, uid):
            self.chain = FakeChain(uid)
            self.merged = None
            self.peer_values = []

        def my_info(self):
            return {"node_id": "self", "network": "wqpu-public-v1"}

        def merge_nodes(self, route_key, nodes):
            self.merged = (route_key, list(nodes))
            return self.merged

        def peers(self):
            return list(self.peer_values)


class NetworkGuardTests(unittest.TestCase):
    def test_peer_match_requires_exact_uid_and_protocol_in_v3(self):
        uid = "wqpu-" + "11" * 16
        self.assertTrue(guard.peer_matches_network(uid, {
            "network": "wqpu-public-v1",
            "network_uid": uid,
        }))
        self.assertFalse(guard.peer_matches_network(uid, {
            "network": "wqpu-public-v1",
            "network_uid": "wqpu-" + "22" * 16,
        }))
        self.assertFalse(guard.peer_matches_network(uid, {
            "network": "other-protocol",
            "network_uid": uid,
        }))
        self.assertFalse(guard.peer_matches_network(uid, {}))

    def test_legacy_without_uid_remains_compatible(self):
        self.assertTrue(guard.peer_matches_network("", {}))
        self.assertTrue(guard.peer_matches_network(None, {"network_uid": "anything"}))

    def test_install_publishes_uid_and_filters_gossip(self):
        runtime = FakeRuntime()
        guard.install(runtime)
        uid = "wqpu-" + "ab" * 16
        mesh = runtime.ChainMesh(uid)
        info = mesh.my_info()
        self.assertEqual(info["network_uid"], uid)
        self.assertEqual(info["network"], "wqpu-public-v1")

        good = {"node_id": "good", "network": "wqpu-public-v1", "network_uid": uid}
        old = {"node_id": "old", "network": "wqpu-public-v1", "network_uid": "wqpu-" + "cd" * 16}
        missing = {"node_id": "missing"}
        route, merged = mesh.merge_nodes("relay", [good, old, missing])
        self.assertEqual(route, "relay")
        self.assertEqual(merged, [good])

    def test_peers_from_other_network_are_never_schedulable(self):
        runtime = FakeRuntime()
        guard.install(runtime)
        uid = "wqpu-" + "ab" * 16
        mesh = runtime.ChainMesh(uid)
        good = ("good", {"network": "wqpu-public-v1", "network_uid": uid})
        stale = ("stale", {"network": "wqpu-public-v1", "network_uid": "wqpu-" + "ef" * 16})
        mesh.peer_values = [stale, good]
        self.assertEqual(mesh.peers(), [good])

    def test_install_is_idempotent(self):
        runtime = FakeRuntime()
        cls1 = guard.install(runtime)
        first = cls1.my_info
        cls2 = guard.install(runtime)
        self.assertIs(cls1, cls2)
        self.assertIs(first, cls2.my_info)


if __name__ == "__main__":
    unittest.main()
