import tempfile
import unittest
from pathlib import Path

from vps_control.core import Controller, Settings, VPSControlError


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctl = Controller(Settings(root=self.root, command_timeout=5))

    def tearDown(self):
        self.tmp.cleanup()

    def test_path_escape_is_blocked(self):
        with self.assertRaises(VPSControlError):
            self.ctl.resolve("../outside.txt")

    def test_write_and_read(self):
        result = self.ctl.write_file("a/b.txt", "one\ntwo\nthree\n")
        self.assertEqual(result["path"], "a/b.txt")
        read = self.ctl.read_file("a/b.txt", 2, 3)
        self.assertEqual(read["content"], "two\nthree")

    def test_list_dir(self):
        self.ctl.write_file("x.txt", "x")
        listing = self.ctl.list_dir(".")
        self.assertEqual(listing["entries"][0]["name"], "x.txt")

    def test_search_text(self):
        self.ctl.write_file("src/main.py", "hello world\nother")
        found = self.ctl.search_text("WORLD", "src")
        self.assertEqual(found["matches"][0]["line"], 1)

    def test_run_command(self):
        result = self.ctl.run_command("printf hello")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout"], "hello")

    def test_cwd_escape_is_blocked(self):
        with self.assertRaises(VPSControlError):
            self.ctl.run_command("pwd", "../")


if __name__ == "__main__":
    unittest.main()
