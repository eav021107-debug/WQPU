import tempfile
import unittest
from pathlib import Path

from vps_control.constitution import CONSTITUTION
from vps_control.core import Controller, Settings
from vps_control.workspace import ProjectWorkspace


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctl = Controller(Settings(root=self.root, command_timeout=5))
        self.ws = ProjectWorkspace(self.ctl)

    def tearDown(self):
        self.tmp.cleanup()

    def test_context_contains_constitution_and_relevant_code(self):
        self.ctl.write_file("src/auth.py", "def connect_wallet():\n    return 'wallet'\n")
        self.ctl.write_file("src/payments.py", "def charge_card():\n    return 'card'\n")
        bundle = self.ws.context_for_task("замени wallet авторизацию")
        self.assertEqual(bundle["constitution"], CONSTITUTION)
        self.assertIn("src/auth.py", bundle["selected_paths"])
        contents = "\n".join(file["content"] for file in bundle["selected_files"])
        self.assertIn("connect_wallet", contents)

    def test_index_refreshes_after_change(self):
        self.ctl.write_file("app.py", "OLD_NAME = 1\n")
        first = self.ws.context_for_task("OLD_NAME")
        self.assertIn("OLD_NAME", first["selected_files"][0]["content"])
        self.ctl.write_file("app.py", "NEW_NAME = 1\n")
        self.ws.invalidate("app.py")
        second = self.ws.context_for_task("NEW_NAME")
        self.assertIn("NEW_NAME", second["selected_files"][0]["content"])

    def test_ignored_dependency_directory_is_not_indexed(self):
        (self.root / "node_modules/pkg").mkdir(parents=True)
        (self.root / "node_modules/pkg/index.js").write_text("secret_dependency_text", encoding="utf-8")
        self.ctl.write_file("src/index.js", "app_code")
        stats = self.ws.refresh()
        self.assertEqual(stats["indexed_files"], 1)
        self.assertNotIn("node_modules", self.ws.project_map())

    def test_map_is_available_before_task(self):
        self.ctl.write_file("README.md", "hello")
        self.ws.refresh()
        self.assertIn("README.md", self.ws.project_map())


if __name__ == "__main__":
    unittest.main()
