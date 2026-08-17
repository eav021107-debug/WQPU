import subprocess
import tempfile
import unittest
from pathlib import Path

import wqpu_attestation


class AttestationNonceTests(unittest.TestCase):
    def test_identical_usage_snapshots_get_distinct_signed_report_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cert = root / "cert.pem"
            key = root / "key.pem"
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
                "-days", "1", "-subj", "/CN=WQPU Test Worker",
                "-keyout", str(key), "-out", str(cert),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            node = "nonce-test-worker"
            wqpu_attestation.register_identity(node, cert, key)
            try:
                report = {
                    "version": 1,
                    "kind": "wqpu-worker-usage-attestation",
                    "request_id": "ab" * 16,
                    "requester_node_id": "requester",
                    "provider_node_id": node,
                    "provider_wallet": "0x" + "11" * 20,
                    "rpc": {"estimated_scalar_ops": 24},
                }
                first = wqpu_attestation.sign_report(report)
                second = wqpu_attestation.sign_report(report)
                self.assertNotEqual(first["report_nonce"], second["report_nonce"])
                self.assertNotEqual(first["signature"], second["signature"])

                fingerprint = wqpu_attestation.certificate_fingerprint(cert.read_text())
                self.assertTrue(wqpu_attestation.verify_report(first, fingerprint))
                self.assertTrue(wqpu_attestation.verify_report(second, fingerprint))
            finally:
                wqpu_attestation.unregister_identity(node)


if __name__ == "__main__":
    unittest.main()
