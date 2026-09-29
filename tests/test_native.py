"""Native Git installation startup: complete hooks, clean checkout, no credentials."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from client.native import HOOKS, PLUGIN, ensure_hooks
from client.support import Failure

ROOT = Path(__file__).resolve().parents[1]
URL = "https://github.com/thelifeandtimes/omarchy-urbit-theme.git"


class NativeHooksTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "home"
        self.source = self.home / ".config/omarchy/plugins" / PLUGIN
        (self.source / ".git").mkdir(parents=True)
        (self.source / "hooks").mkdir()
        self.state = Path(temp.name) / "state"
        self.calls = []
        for _, name in HOOKS:
            shutil.copyfile(ROOT / "hooks" / name, self.source / "hooks" / name)

    def destination(self, event, name):
        return self.home / ".config/omarchy/hooks" / (event + ".d") / name

    def runner(self, args, **kwargs):
        self.calls.append(args)
        event, source = args[3], Path(args[4])
        target = self.destination(event, source.name)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        return 0, b""

    def setup_hooks(self):
        return ensure_hooks(self.source, self.home, self.state, self.runner)

    def test_first_enable_installs_both_and_restart_is_idempotent(self):
        self.assertTrue(self.setup_hooks())
        self.assertEqual(len(self.calls), 2)
        receipt = (self.state / "native-hooks.json").read_bytes()
        for event, name in HOOKS:
            self.assertEqual(self.destination(event, name).read_bytes(), (self.source / "hooks" / name).read_bytes())
        self.setup_hooks()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((self.state / "native-hooks.json").read_bytes(), receipt)
        self.assertFalse((self.source / "native-hooks.json").exists())

    def test_source_checkout_and_snapshot_do_not_self_install(self):
        self.assertFalse(ensure_hooks(ROOT, self.home, self.state, self.runner))
        (self.source / ".git").rmdir()
        self.assertFalse(self.setup_hooks())
        self.assertEqual(self.calls, [])
        self.assertFalse(self.state.exists())

    def test_native_update_replaces_only_owned_unmodified_hook(self):
        self.setup_hooks()
        source = self.source / "hooks" / HOOKS[0][1]
        source.write_text(source.read_text() + "# future update\n")
        self.setup_hooks()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.destination(*HOOKS[0]).read_bytes(), source.read_bytes())
        self.destination(*HOOKS[0]).write_text("my customization\n")
        with self.assertRaises(Failure):
            self.setup_hooks()
        self.assertEqual(self.destination(*HOOKS[0]).read_text(), "my customization\n")

    def test_conflicting_second_hook_prevents_installing_first(self):
        conflict = self.destination(*HOOKS[1])
        conflict.parent.mkdir(parents=True)
        conflict.write_text("user hook\n")
        with self.assertRaises(Failure):
            self.setup_hooks()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.destination(*HOOKS[0]).exists())

    def test_identical_manual_hooks_can_be_adopted_without_overwrite(self):
        for event, name in HOOKS:
            target = self.destination(event, name)
            target.parent.mkdir(parents=True)
            shutil.copyfile(self.source / "hooks" / name, target)
        self.setup_hooks()
        self.assertEqual(self.calls, [])
        self.assertEqual(len(json.loads((self.state / "native-hooks.json").read_text())["hooks"]), 2)

    def test_partial_install_can_resume_after_command_failure(self):
        def failing(args, **kwargs):
            if args[3] == "font-set":
                return 1, b""
            return self.runner(args, **kwargs)
        with self.assertRaises(Failure):
            ensure_hooks(self.source, self.home, self.state, failing)
        self.setup_hooks()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.destination(*HOOKS[1]).exists())

    def test_symlink_and_invalid_receipt_are_preserved(self):
        target = self.destination(*HOOKS[0])
        target.parent.mkdir(parents=True)
        other = self.home / "user-file"
        other.write_text("keep")
        target.symlink_to(other)
        with self.assertRaises(Failure):
            self.setup_hooks()
        self.assertEqual(other.read_text(), "keep")
        target.unlink()
        self.state.mkdir()
        (self.state / "native-hooks.json").write_text('{"version":999,"hooks":{}}')
        with self.assertRaises(Failure):
            self.setup_hooks()
        self.assertEqual(self.calls, [])


@unittest.skipUnless(Path("/usr/share/omarchy/bin/omarchy-plugin-add").is_file() and shutil.which("jq"),
                     "Installed Omarchy native installer is an integration check")
class NativeCommandTests(unittest.TestCase):
    def test_native_command_then_first_start_sets_up_hooks_without_manual_script(self):
        with tempfile.TemporaryDirectory(prefix="urbit-native-install-") as temp:
            root = Path(temp)
            home, binaries, state = root / "home", root / "bin", root / "state"
            home.mkdir(); binaries.mkdir(); state.mkdir()
            # Exercise the installed Omarchy command and validator. Only the
            # network clone and live desktop IPC are replaced with inert fixtures.
            git_shim = binaries / "git"
            git_shim.write_text("#!" + sys.executable + "\n" + '''
import os, pathlib, shutil, sys
assert sys.argv[1:4] == ["clone", "--", os.environ["FIXTURE_URL"]]
source = pathlib.Path(os.environ["FIXTURE_SOURCE"])
target = pathlib.Path(sys.argv[4])
shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
(target / ".git").mkdir()
''')
            git_shim.chmod(0o755)
            catalog = binaries / "omarchy-plugin-catalog"
            catalog.write_text("#!" + sys.executable + "\nprint('[]')\n")
            catalog.chmod(0o755)
            ipc = binaries / "omarchy-shell"
            ipc.write_text("#!" + sys.executable + "\n" + '''
import json, sys
assert sys.argv[1] == "shell"
method = sys.argv[2]
if method == "listPlugins": print(json.dumps([{"id":"thelifeandtimes.urbit-theme","enabled":True}]))
elif method in ("rescanPlugins", "enablePlugin", "setPluginEnabled"): print("ok")
else: raise AssertionError(method)
''')
            ipc.chmod(0o755)
            env = dict(HOME=str(home), PATH=str(binaries) + ":/usr/share/omarchy/bin:/usr/bin:/bin",
                       OMARCHY_PATH="/usr/share/omarchy", XDG_STATE_HOME=str(state),
                       XDG_CONFIG_HOME=str(home / ".config"), XDG_CACHE_HOME=str(root / "cache"),
                       FIXTURE_SOURCE=str(ROOT), FIXTURE_URL=URL, LANG="C.UTF-8")
            result = subprocess.run(["/usr/share/omarchy/bin/omarchy", "plugin", "add", URL, "--enable", "--yes"],
                                    env=env, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            installed = home / ".config/omarchy/plugins" / PLUGIN
            self.assertTrue((installed / ".git").is_dir())
            before = {p.relative_to(installed): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in installed.rglob("*") if p.is_file()}
            self.assertFalse((home / ".config/omarchy/hooks/theme-set.d/omarchy-urbit-theme").exists())
            result = subprocess.run([sys.executable, "-B", str(installed / "client/sync.py"), "serve"],
                                    input="", env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stderr, "")
            for event, name in HOOKS:
                self.assertEqual((home / ".config/omarchy/hooks" / (event + ".d") / name).read_bytes(),
                                 (ROOT / "hooks" / name).read_bytes())
            after = {p.relative_to(installed): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in installed.rglob("*") if p.is_file()}
            self.assertEqual(before, after)
            self.assertEqual(json.loads((state / "omarchy-urbit-theme/desktop.json").read_text())["hub"], None)
            self.assertTrue((state / "omarchy-urbit-theme/native-hooks.json").is_file())


if __name__ == "__main__":
    unittest.main()
