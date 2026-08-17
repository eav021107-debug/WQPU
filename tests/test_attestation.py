import tempfile
import unittest
from pathlib import Path

import wqpu_attestation


class AttestationTests(unittest.TestCase):
    def test_worker_report_signature_is_bound_to_tls_fingerprint(self):
        wqpu = wqpu_attestation.wqpu
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = (wqpu.HOME, wqpu.CERT, wqpu.KEY, wqpu.RUNTIME, wqpu.LOGS)
            try:
                wqpu.HOME = root
                wqpu.CERT = root / "cert.pem"
                wqpu.KEY = root / "key.pem"
                wqpu.RUNTIME = root / "runtime"
                wqpu.LOGS = root / "logs"
                report = {
                    "version": 1,
                    "kind": "wqpu-worker-usage-attestation",
                    "request_id": "ab" * 16,
                    "requester_node_id": "requester",
                    "provider_node_id": "worker",
                    "provider_wallet": "0x" + "22" * 20,
                    "rpc": {"meter_version": 2, "estimated_scalar_ops": 12345},
                }
                signed = wqpu_attestation.sign_report(report)
                fingerprint = wqpu_attestation.certificate_fingerprint(signed["certificate"])
                self.assertTrue(wqpu_attestation.verify_report(signed, fingerprint))

                tampered = dict(signed)
                tampered["rpc"] = dict(signed["rpc"])
                tampered["rpc"]["estimated_scalar_ops"] += 1
                with self.assertRaises(wqpu_attestation.AttestationError):
                    wqpu_attestation.verify_report(tampered, fingerprint)

                with self.assertRaises(wqpu_attestation.AttestationError):
                    wqpu_attestation.verify_report(signed, "00" * 32)
            finally:
                wqpu.HOME, wqpu.CERT, wqpu.KEY, wqpu.RUNTIME, wqpu.LOGS = old


if __name__ == "__main__":
    unittest.main()
