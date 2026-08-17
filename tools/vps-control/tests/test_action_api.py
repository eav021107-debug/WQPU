import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient


class ActionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {
            "VPS_CONTROL_ROOT": str(self.root),
            "VPS_CONTROL_ACTION_TOKEN": "test-secret",
            "VPS_CONTROL_PUBLIC_URL": "https://vps.example.test",
        }, clear=False)
        self.env.start()
        import importlib
        import vps_control.action_api as action_api
        self.api = importlib.reload(action_api)
        self.client = TestClient(self.api.app)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_requires_auth(self):
        response = self.client.post("/v1/project/open", json={"task": "fix auth"})
        self.assertEqual(response.status_code, 401)

    def test_open_project_returns_living_context(self):
        (self.root / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
        response = self.client.post(
            "/v1/project/open",
            headers={"X-VPS-Control-Key": "test-secret"},
            json={"task": "fix login"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("constitution", data)
        self.assertIn("auth.py", data["selected_paths"])

    def test_openapi_points_at_public_url(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["servers"][0]["url"], "https://vps.example.test")
        self.assertIn("/v1/project/open", response.json()["paths"])


if __name__ == "__main__":
    unittest.main()
