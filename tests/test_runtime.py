import unittest

import wqpu_runtime


class RuntimeTests(unittest.TestCase):
    def test_registration_must_match_endpoint_and_tls_fingerprint(self):
        node = {
            "active": True,
            "endpoint": "worker.example:7443",
            "fingerprint": "0x" + "ab" * 32,
        }
        self.assertTrue(
            wqpu_runtime.registration_matches(
                node,
                "worker.example:7443",
                "0x" + "ab" * 32,
            )
        )
        self.assertFalse(
            wqpu_runtime.registration_matches(
                node,
                "other.example:7443",
                "0x" + "ab" * 32,
            )
        )
        self.assertFalse(
            wqpu_runtime.registration_matches(
                node,
                "worker.example:7443",
                "0x" + "cd" * 32,
            )
        )

    def test_public_secret_is_stable_per_chain_and_registry(self):
        a = wqpu_runtime.public_secret("0x7a69", "0x" + "11" * 20)
        b = wqpu_runtime.public_secret("0x7a69", "0x" + "11" * 20)
        c = wqpu_runtime.public_secret("0x1", "0x" + "11" * 20)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)


if __name__ == "__main__":
    unittest.main()
