import os
import tempfile
import unittest
from pathlib import Path

import wqpu_session


class SessionSignerTests(unittest.TestCase):
    def test_der_signature_parser(self):
        # DER SEQUENCE(INTEGER 1, INTEGER 2)
        r, s = wqpu_session.parse_der_signature(bytes.fromhex("3006020101020102"))
        self.assertEqual(r, 1)
        self.assertEqual(s, 2)

    def test_local_key_generation_and_compact_signature(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "session.pem"
            wqpu_session.ensure_session_key(key)
            self.assertTrue(key.exists())
            self.assertGreater(key.stat().st_size, 0)
            point = wqpu_session.public_point(key)
            self.assertEqual(len(point), 65)
            self.assertEqual(point[0], 4)
            signature = wqpu_session.sign_digest("0x" + "11" * 32, key)
            self.assertTrue(signature.startswith("0x"))
            self.assertEqual(len(signature), 130)  # 0x + r(32) + s(32)

            if os.name != "nt":
                self.assertEqual(key.stat().st_mode & 0o077, 0)

    def test_invalid_digest_is_rejected(self):
        with self.assertRaises(wqpu_session.SessionError):
            wqpu_session.sign_digest("0x1234")


if __name__ == "__main__":
    unittest.main()
