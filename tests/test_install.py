import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.target, self.hook = installer.paths(self.home)
        self.calls = []
        self.fail_hook = False

    def run_command(self, argv):
        self.calls.append(argv)
        if argv[:4] == ["omarchy", "hook", "install", "theme-set"]:
            if self.fail_hook:
                raise subprocess.CalledProcessError(1, argv)
            self.hook.parent.mkdir(parents=True, exist_ok=True)
            self.hook.write_bytes(Path(argv[4]).read_bytes())
            self.hook.chmod(0o755)

    def install(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(ROOT, self.home, runner=self.run_command, **kwargs)

    def test_snapshot_and_hook_without_enable_or_auth(self):
        self.install()
        self.assertEqual(installer.inspect(self.target, self.hook)["id"], installer.ID)
        self.assertEqual(self.hook.read_bytes(), (ROOT / "hooks" / installer.HOOK).read_bytes())
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.home / ".local/state/omarchy-urbit-theme").exists())
        self.assertFalse((self.target / ".git").exists())
        for name in installer.FILES:
            self.assertEqual((self.target / name).read_bytes(), (ROOT / name).read_bytes())

    def test_enable_is_explicit(self):
        self.install(enable=True)
        self.assertEqual(self.calls[-2:], [
            ["omarchy-shell", "shell", "rescanPlugins"],
            ["omarchy", "plugin", "enable", installer.ID, "--section", "right"],
        ])

    def test_shell_restart_is_explicit(self):
        self.install(enable=True, restart_shell=True)
        self.assertEqual(self.calls[-1], ["omarchy", "restart", "shell"])

    def test_unrelated_hook_and_shell_config_survive(self):
        self.hook.parent.mkdir(parents=True)
        other = self.hook.parent / "another-plugin"
        other.write_text("user hook")
        config = self.home / ".config/omarchy/shell.json"
        config.write_text('{"user":"settings"}')
        self.install()
        with contextlib.redirect_stdout(io.StringIO()):
            installer.uninstall(self.home, runner=self.run_command)
        self.assertFalse(self.target.exists())
        self.assertFalse(self.hook.exists())
        self.assertEqual(other.read_text(), "user hook")
        self.assertEqual(json.loads(config.read_text()), {"user": "settings"})

    def test_existing_install_requires_replace(self):
        self.install()
        with self.assertRaisesRegex(RuntimeError, "Already installed"):
            self.install()
        self.install(replace=True)
        installer.inspect(self.target, self.hook)

    def test_user_edits_not_replaced_or_removed(self):
        self.install()
        (self.target / "Panel.qml").write_text("user edit")
        with self.assertRaisesRegex(RuntimeError, "modified"):
            self.install(replace=True)
        with self.assertRaisesRegex(RuntimeError, "modified"):
            installer.uninstall(self.home, runner=self.run_command)
        self.assertEqual((self.target / "Panel.qml").read_text(), "user edit")

    def test_added_files_and_nested_receipt_not_removed(self):
        self.install()
        (self.target / "user").mkdir()
        (self.target / "user" / installer.MARKER).write_text("user file")
        with self.assertRaisesRegex(RuntimeError, "file set"):
            installer.uninstall(self.home, runner=self.run_command)

    def test_user_hook_not_overwritten(self):
        self.hook.parent.mkdir(parents=True)
        self.hook.write_text("user hook")
        with self.assertRaisesRegex(RuntimeError, "already exists"):
            self.install()
        self.assertEqual(self.hook.read_text(), "user hook")
        self.assertFalse(self.target.exists())

    def test_symlink_refused(self):
        self.target.parent.mkdir(parents=True)
        other = self.home / "elsewhere"
        other.mkdir()
        self.target.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            self.install()

    def test_hook_failure_rolls_back_replacement(self):
        self.install()
        before = (self.target / installer.MARKER).read_bytes()
        self.fail_hook = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.install(replace=True)
        self.assertEqual((self.target / installer.MARKER).read_bytes(), before)
        installer.inspect(self.target, self.hook)

    def test_hook_failure_rolls_back_first_install(self):
        self.fail_hook = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.install()
        self.assertFalse(self.target.exists())
        self.assertFalse(self.hook.exists())


if __name__ == "__main__":
    unittest.main()
