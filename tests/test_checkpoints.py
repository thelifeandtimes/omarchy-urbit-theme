"""Offline rollback: real files, locks and activation ordering; inert commands."""
import copy
import fcntl
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from client import checkpoints as module
from client.checkpoints import Checkpoints, digest, durable
from client.profile import Desktop, dumps
from client.support import Failure
from client.sync import Disk, fresh, serve
import test_appearance_owner as owners
import test_profile as fixtures
from test_profile import profile


class CheckpointTests(unittest.TestCase):
    setUp = fixtures.DesktopTests.setUp
    desktop = owners.AppearanceOwnerTests.desktop

    def runner(self, args, **kwargs):
        result = fixtures.DesktopTests.runner(self, args, **kwargs)
        if args[:3] == ["omarchy", "theme", "set"]:
            target = self.current / "theme"
            if target.exists():
                shutil.rmtree(target)
            target.mkdir()
            source = self.theme.parent / args[3]
            for p in source.iterdir():
                if p.is_file() and not p.name.startswith("."):
                    shutil.copyfile(p, target / p.name)
        return result

    def fixture(self):
        self.d = self.desktop("default")
        self.p = profile()
        self.d.apply(self.p)
        self.local = copy.deepcopy(self.p)
        self.d.capture = lambda _: copy.deepcopy(self.local)
        self.store = Disk(self.home / "separate-sync-state")
        self.state = fresh()
        self.state.update(observed=self.p, pending=profile(color="#ABCDEF"), attempted=True, baseId="f" * 32)
        self.store.save(self.state)
        self.original = self.store.path.read_bytes()
        self.c = Checkpoints(self.d)
        self.checkpoint = self.c.capture(self.state)["id"]
        self.calls.clear()
        return self.checkpoint

    def effects(self):
        return [args for args, _ in self.calls if args[:3] in (["omarchy", "theme", "set"], ["omarchy", "font", "set"])
                or args[:2] == ["hyprctl", "reload"] or args[:3] == ["omarchy-shell", "shell", "applyTheme"]]

    def row(self, request="a" * 32):
        return json.loads((self.c.receipts / (request + ".json")).read_text())

    def test_frozen_restore_does_not_need_source_theme_or_ship_and_preserves_pending(self):
        checkpoint = self.fixture()
        snapshot = self.c.load(checkpoint)
        self.assertNotIn("hub", snapshot)
        self.assertEqual(snapshot["backgroundPolicy"], "preserve-current")
        candidate = self.desktop("dev")
        candidate.apply(profile(color="#FF0000"))
        shutil.rmtree(self.theme)
        self.calls.clear()
        with patch("client.sync.Keyring", side_effect=AssertionError("No keyring")), patch("client.sync.Eyre", side_effect=AssertionError("No Eyre")):
            result = self.c.restore(checkpoint, "a" * 32)
        self.assertEqual(result["phase"], "verified")
        self.assertEqual(self.store.path.read_bytes(), self.original)
        self.assertEqual((self.current / "theme/neovim.lua").read_text(), "-- trusted local theme\n")
        self.assertEqual(self.d.ownership.read("owner"), self.d.instance.state_name)
        native = next(kwargs for args, kwargs in self.calls if args[:3] == ["omarchy", "theme", "set"])
        self.assertEqual(native["env"]["OMARCHY_THEME_SKIP_BACKGROUND"], "1")

    def test_completed_request_reads_without_reapplying_and_changed_desktop_refuses(self):
        checkpoint = self.fixture()
        self.c.restore(checkpoint, "a" * 32)
        self.calls.clear()
        self.assertEqual(self.c.restore(checkpoint, "a" * 32)["phase"], "verified")
        self.assertFalse(self.effects())
        self.local["font"] = "Different Mono"
        with self.assertRaises(Failure): self.c.restore(checkpoint, "a" * 32)
        self.assertFalse(self.effects())

    def test_intent_is_durable_before_effect_failure_and_exact_retry_verifies(self):
        checkpoint = self.fixture()
        activate = self.d.activate
        def fail(*args, **kwargs):
            self.assertEqual(self.row()["phase"], "applying")
            activate(*args, **kwargs)
            raise Failure("desktop", "after effect")
        with patch.object(self.d, "activate", side_effect=fail):
            with self.assertRaises(Failure): self.c.restore(checkpoint, "a" * 32)
        self.assertEqual(self.row()["phase"], "applying")
        self.assertEqual(self.store.path.read_bytes(), self.original)
        with self.assertRaises(Failure): self.c.assert_no_pending()
        self.calls.clear()
        with self.assertRaises(Failure): self.c.restore(checkpoint, "b" * 32)
        self.assertFalse(self.effects())
        self.assertEqual(self.c.restore(checkpoint, "a" * 32)["phase"], "verified")
        self.c.assert_no_pending()

    def test_failed_intent_write_prevents_activation(self):
        checkpoint = self.fixture()
        original = module.durable
        def fail(path, value):
            if path.parent == self.c.receipts: raise OSError("disk full")
            return original(path, value)
        with patch.object(module, "durable", side_effect=fail):
            with self.assertRaises(OSError): self.c.restore(checkpoint, "a" * 32)
        self.assertFalse(self.effects())
        self.assertEqual(self.store.path.read_bytes(), self.original)

    def test_failed_final_receipt_keeps_request_incomplete_until_retry(self):
        checkpoint = self.fixture()
        original = module.durable
        def fail(path, value):
            if value.get("phase") == "verified": raise OSError("disk full")
            return original(path, value)
        with patch.object(module, "durable", side_effect=fail):
            with self.assertRaises(OSError): self.c.restore(checkpoint, "a" * 32)
        self.assertEqual(self.row()["phase"], "applying")
        self.assertEqual(self.c.restore(checkpoint, "a" * 32)["phase"], "verified")

    def test_readback_mismatch_retains_incomplete_receipt(self):
        checkpoint = self.fixture()
        self.local["colors"]["background"] = "#FF0000"
        with self.assertRaises(Failure): self.c.restore(checkpoint, "a" * 32)
        self.assertEqual(self.row()["phase"], "applying")
        self.assertEqual(self.store.path.read_bytes(), self.original)

    def test_pending_restore_blocks_serve_before_hooks_credentials_or_state_write(self):
        checkpoint = self.fixture()
        self.c.directory(self.c.receipts)
        durable(self.c.receipts / ("a" * 32 + ".json"), dict(version=1, owner=self.c.owner, checkpoint=checkpoint, request="a" * 32, phase="applying"))
        with patch("client.sync.Desktop", return_value=self.d), patch("client.sync.ensure_hooks") as hooks, patch("client.sync.Keyring") as keys:
            with self.assertRaises(Failure): serve()
        hooks.assert_not_called(); keys.assert_not_called()
        self.assertEqual(self.store.path.read_bytes(), self.original)

    def test_edited_configuration_and_managed_theme_are_preserved(self):
        checkpoint = self.fixture()
        config = self.home / ".config/hypr/hyprland.lua"
        saved = config.read_bytes(); config.write_bytes(saved + b"-- new personal setting\n")
        with self.assertRaises(Failure): self.c.restore(checkpoint, "a" * 32)
        self.assertTrue(config.read_bytes().endswith(b"-- new personal setting\n"))
        self.assertFalse(self.effects())
        config.write_bytes(saved)
        self.c.restore(checkpoint, "a" * 32)
        managed = self.theme.parent / ("urbit-restored-" + checkpoint)
        (managed / "neovim.lua").write_text("-- personal edit\n")
        self.calls.clear()
        with self.assertRaises(Failure): self.c.restore(checkpoint, "b" * 32)
        self.assertEqual((managed / "neovim.lua").read_text(), "-- personal edit\n")
        self.assertFalse(self.effects())

    def test_unknown_version_owner_hash_and_symlink_refuse_without_effect(self):
        checkpoint = self.fixture()
        value = self.c.load(checkpoint)
        for field, v in (("version", 2), ("owner", "omarchy-urbit-theme.dev")):
            candidate = dict(value, **{field:v}); key = digest(dumps(candidate).encode())
            durable(self.c.root / (key + ".json"), candidate)
            with self.assertRaises(Failure): self.c.restore(key, "a" * 32)
        path = self.c.root / (checkpoint + ".json")
        saved = path.read_bytes();path.write_bytes(saved + b" ")
        with self.assertRaises(Failure): self.c.restore(checkpoint, "a" * 32)
        path.unlink(); other = self.home / "saved-checkpoint";other.write_bytes(saved);path.symlink_to(other)
        with self.assertRaises(OSError): self.c.restore(checkpoint, "a" * 32)
        self.assertFalse(self.effects())
        self.assertEqual(other.read_bytes(), saved)

    def test_same_request_cannot_bind_another_checkpoint_and_history_is_bounded(self):
        checkpoint = self.fixture()
        self.c.restore(checkpoint, "a" * 32)
        value = self.c.load(checkpoint);value["profile"]["updateId"] = "d" * 32
        second = digest(dumps(value).encode());durable(self.c.root / (second + ".json"), value)
        self.calls.clear()
        with self.assertRaises(Failure): self.c.restore(second, "a" * 32)
        with patch.object(module, "MAX_RECEIPTS", 1):
            with self.assertRaises(Failure): self.c.restore(checkpoint, "b" * 32)
        self.assertFalse(self.effects())

    def test_capture_refuses_unverified_owner_edits_and_special_assets(self):
        self.fixture()
        with self.assertRaises(Failure): self.c.capture(dict(self.state, applying=self.p))
        self.local["font"] = "Wrong Mono"
        with self.assertRaises(Failure): self.c.capture(self.state)
        self.local = copy.deepcopy(self.p)
        with self.d.session(): self.d.ownership.write("owner", "-")
        with self.assertRaises(Failure): self.c.capture(self.state)
        with self.d.session(): self.d.ownership.write("owner", self.c.owner)
        os.mkfifo(self.current / "theme/unsafe")
        with self.assertRaises(Failure): self.c.capture(self.state)

    def test_ancillary_file_readback_failure_is_incomplete_and_replay_detects_edits(self):
        checkpoint = self.fixture()
        activate = self.d.activate
        def altered(*args, **kwargs):
            activate(*args, **kwargs)
            (self.current / "theme/neovim.lua").write_text("-- changed downstream\n")
        with patch.object(self.d, "activate", side_effect=altered):
            with self.assertRaises(Failure): self.c.restore(checkpoint, "a" * 32)
        self.assertEqual(self.row()["phase"], "applying")
        self.c.restore(checkpoint, "a" * 32)
        (self.current / "theme/neovim.lua").write_text("-- later edit\n")
        self.calls.clear()
        with self.assertRaises(Failure): self.c.restore(checkpoint, "a" * 32)
        self.assertFalse(self.effects())

    def test_pending_receipt_directory_symlink_refuses_instead_of_appearing_empty(self):
        self.fixture()
        self.c.receipts.symlink_to(self.home / "missing")
        with self.assertRaises(Failure): self.c.assert_no_pending()

    def test_native_theme_transition_prevents_mixed_checkpoint(self):
        self.fixture()
        runtime = self.home / "runtime"
        runtime.mkdir()
        with patch.dict(os.environ, XDG_RUNTIME_DIR=str(runtime)):
            with (runtime / "omarchy-theme-set.lock").open("a") as native:
                fcntl.flock(native, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(Failure) as caught: self.c.capture(self.state)
                self.assertEqual(caught.exception.code, "busy")
                self.assertFalse(self.effects())
            self.assertIn("id", self.c.capture(self.state))

    def test_interrupted_atomic_temporaries_are_preserved_but_unknown_history_refuses(self):
        self.fixture();self.c.directory(self.c.receipts)
        temp = self.c.receipts / ".urbit-incomplete"
        temp.write_text('{"partial":');temp.chmod(0o600)
        self.c.assert_no_pending();self.assertEqual(temp.read_text(), '{"partial":')
        (self.c.receipts / "unknown.json").write_text("{}")
        with self.assertRaises(Failure): self.c.assert_no_pending()


if __name__ == "__main__":
    unittest.main()
