import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import wqpu_node_identity as identity
import wqpu_node_status as status


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
class NodeStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.key = root / "key.pem"
        self.cert = root / "cert.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-days", "1", "-subj", "/CN=WQPU Status Test",
            "-keyout", str(self.key), "-out", str(self.cert),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.fp = identity.certificate_fingerprint_from_der(identity.certificate_der(self.cert))
        self.info = {
            "node_id": "worker-1",
            "wallet": "0x" + "22" * 20,
            "network": "wqpu-public-v1",
            "network_uid": "wqpu-" + "ab" * 16,
            "capacity": 32000,
            "load_bps": 1250,
            "model": "test-model",
            "version": "0.6.0",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def signed(self, issued_at=1700000000):
        info = dict(self.info)
        info["status_attestation"] = status.build_status_attestation(
            info, cert_path=self.cert, key_path=self.key, issued_at=issued_at,
        )
        return info

    def test_valid_status_verifies(self):
        info = self.signed()
        result = status.verify_status_attestation(
            info, self.fp, expected_network_uid=self.info["network_uid"], now=1700000000,
        )
        self.assertEqual(result["capacity"], 32000)
        self.assertEqual(result["load_bps"], 1250)
        self.assertEqual(result["wallet"], self.info["wallet"])

    def test_load_or_capacity_tampering_is_rejected(self):
        for field, value in (("load_bps", 1), ("capacity", 999999)):
            info = self.signed()
            info[field] = value
            with self.assertRaises(status.StatusError, msg=field):
                status.verify_status_attestation(info, self.fp, now=1700000000)

    def test_wrong_network_and_fingerprint_are_rejected(self):
        info = self.signed()
        with self.assertRaises(status.StatusError):
            status.verify_status_attestation(
                info, self.fp, expected_network_uid="wqpu-" + "cd" * 16, now=1700000000,
            )
        with self.assertRaises(status.StatusError):
            status.verify_status_attestation(info, "ff" * 32, now=1700000000)

    def test_stale_and_future_status_are_rejected(self):
        with self.assertRaises(status.StatusError):
            status.verify_status_attestation(self.signed(1000), self.fp, now=1100, max_age=20)
        with self.assertRaises(status.StatusError):
            status.verify_status_attestation(self.signed(1100), self.fp, now=1000, future_skew=10)

    def test_capacity_and_load_bounds_fail_closed(self):
        bad = dict(self.info)
        bad["capacity"] = 0
        with self.assertRaises(status.StatusError):
            status.canonical_status(bad)
        bad = dict(self.info)
        bad["load_bps"] = 10001
        with self.assertRaises(status.StatusError):
            status.canonical_status(bad)


if __name__ == "__main__":
    unittest.main()
