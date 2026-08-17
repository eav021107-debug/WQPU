import base64
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import wqpu_node_identity as identity


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required")
class NodeIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.key = root / "key.pem"
        self.cert = root / "cert.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256", "-nodes",
            "-days", "1", "-subj", "/CN=WQPU Test Node",
            "-keyout", str(self.key), "-out", str(self.cert),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.uid = "wqpu-" + "ab" * 16
        self.wallet = "0x" + "22" * 20
        self.node_id = "node-123"
        self.fp = identity.certificate_fingerprint_from_der(identity.certificate_der(self.cert))

    def tearDown(self):
        self.tmp.cleanup()

    def proof(self, role="control", now=1700000000, nonce="11" * 16):
        return identity.build_identity_proof(
            self.uid, self.node_id, self.wallet, role,
            cert_path=self.cert, key_path=self.key,
            issued_at=now, nonce=nonce,
        )

    def test_valid_proof_verifies_against_registered_fingerprint(self):
        proof = self.proof()
        result = identity.verify_identity_proof(
            proof, self.uid, self.node_id, self.wallet, self.fp, "control",
            now=1700000000,
        )
        self.assertEqual(result["wallet"], self.wallet)
        self.assertEqual(result["fingerprint"], self.fp)
        self.assertEqual(result["nonce"], "11" * 16)

    def test_tampering_is_rejected(self):
        fields = {
            "network_uid": "wqpu-" + "cd" * 16,
            "node_id": "other-node",
            "wallet": "0x" + "33" * 20,
            "role": "dial",
        }
        for key, value in fields.items():
            proof = self.proof()
            proof[key] = value
            with self.assertRaises(Exception, msg=key):
                identity.verify_identity_proof(
                    proof, self.uid, self.node_id, self.wallet, self.fp, "control",
                    now=1700000000,
                )

    def test_wrong_registered_fingerprint_is_rejected(self):
        with self.assertRaises(identity.IdentityError):
            identity.verify_identity_proof(
                self.proof(), self.uid, self.node_id, self.wallet, "ff" * 32, "control",
                now=1700000000,
            )

    def test_stale_and_future_proofs_are_rejected(self):
        with self.assertRaises(identity.IdentityError):
            identity.verify_identity_proof(
                self.proof(now=1000), self.uid, self.node_id, self.wallet, self.fp, "control",
                now=2000, max_age=300,
            )
        with self.assertRaises(identity.IdentityError):
            identity.verify_identity_proof(
                self.proof(now=2100), self.uid, self.node_id, self.wallet, self.fp, "control",
                now=2000, future_skew=60,
            )

    def test_signature_corruption_is_rejected(self):
        proof = self.proof()
        raw = bytearray(base64.b64decode(proof["signature"]))
        raw[0] ^= 1
        proof["signature"] = base64.b64encode(bytes(raw)).decode("ascii")
        with self.assertRaises(identity.IdentityError):
            identity.verify_identity_proof(
                proof, self.uid, self.node_id, self.wallet, self.fp, "control",
                now=1700000000,
            )

    def test_registry_verifier_rejects_replayed_nonce(self):
        fp = self.fp
        wallet = self.wallet

        class FakeClient(object):
            configured = True
            def find_wallet(self, value, max_nodes):
                self.last = (value, max_nodes)
                return {
                    "wallet": wallet,
                    "active": True,
                    "fingerprint": "0x" + fp,
                }

        verifier = identity.RegistryIdentityVerifier(self.uid, client=FakeClient(), cache_ttl=30)
        proof = self.proof(role="dial", now=1700000000, nonce="44" * 16)
        hello = {
            "role": "dial",
            "node_id": self.node_id,
            "wallet": self.wallet,
            "network_uid": self.uid,
            "identity_proof": proof,
        }
        result = verifier.verify_hello(hello, now=1700000000)
        self.assertEqual(result["role"], "dial")
        with self.assertRaises(identity.IdentityError):
            verifier.verify_hello(hello, now=1700000001)

    def test_registry_verifier_requires_active_registered_wallet(self):
        class FakeClient(object):
            configured = True
            def find_wallet(self, value, max_nodes):
                return None

        verifier = identity.RegistryIdentityVerifier(self.uid, client=FakeClient())
        hello = {
            "role": "control",
            "node_id": self.node_id,
            "wallet": self.wallet,
            "network_uid": self.uid,
            "identity_proof": self.proof(now=int(time.time())),
        }
        with self.assertRaises(identity.IdentityError):
            verifier.verify_hello(hello)


if __name__ == "__main__":
    unittest.main()
