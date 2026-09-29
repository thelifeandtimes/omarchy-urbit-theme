"""Portable profile, real Hyprland config validation, and inert desktop application."""

import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from client.profile import (Desktop, MANAGED_THEME, colors_toml, digest, palette, parse_shell,
                            read_remote, shell_toml, validate, window_lua)
from client.support import Failure


def profile(theme="tokyo-night", color="#112233", device="a" * 32):
    return dict(schemaVersion=1, updateId=secrets.token_hex(16), deviceId=device, theme=theme,
                colors={"mode": "dark", "background": color, "foreground": "#EEEEEE", "blue": "#123ABC",
                        "green": "#33CC66", "lighter_background": "#223344", "muted": "#888888",
                        "selection": "#334455", "red": "#EE3333"},
                font="Fixture Mono", shell={"font.base-size": "12", "controls.focus-fill-alpha": "0.08"},
                windows={"decoration:rounding": {"int": 8}, "decoration:blur:enabled": {"bool": True},
                         "general:gaps_out": {"css": "10 10 10 10"}, "decoration:shadow:offset": {"vec2": [1, 2]},
                         "general:col.active_border": {"gradient": "ff112233 aa445566 45deg"},
                         "group:groupbar:text_color": {"int": 0xffffffff}},
                animations={"curves": [{"name": "fixture", "points": [0.2, 1, 0.3, 1]}],
                            "rules": [{"name": "windows", "enabled": True, "speed": 3.5, "bezier": "fixture", "style": "popin 87%"}]},
                files={"btop.theme": "# fixture\ntheme[main_bg]=\"#112233\"\n"})


class ProfileTests(unittest.TestCase):
    def test_eleven_color_mapping_and_auto(self):
        p = profile()
        t = palette(p)
        self.assertEqual([t[k] for k in ("text", "muted", "raised", "error", "selection", "link")],
                         ["#EEEEEE", "#888888", "#223344", "#EE3333", "#334455", "#123ABC"])
        for key in ("muted", "lighter_background", "red", "selection", "blue"):
            del p["colors"][key]
        t = palette(p)
        self.assertEqual([t[k] for k in ("muted", "raised", "error", "selection", "link")], [""] * 5)
        self.assertEqual(t["primary"], "#EEEEEE")

    def test_content_digest_ignores_identity_not_effects(self):
        a = profile()
        b = copy.deepcopy(a)
        b.update(deviceId="b" * 32, updateId=secrets.token_hex(16))
        self.assertEqual(digest(a), digest(b))
        b["windows"]["decoration:rounding"]["int"] = 16
        self.assertNotEqual(digest(a), digest(b))

    def test_shell_roundtrip_including_widths_gradients_and_comments(self):
        raw = '''[font]\nbase-size = 14 # hello\n[popups]\nborder = "#112233 #445566 45deg"\nborder-width = 1 2 3 4\n'''
        parsed = parse_shell(raw)
        self.assertEqual(parsed["popups.border-width"], "1 2 3 4")
        self.assertEqual(parsed, parse_shell(shell_toml(parsed)))

    def test_invalid_profile_never_becomes_code_or_paths(self):
        mutations = [lambda p: p.update(theme="../../tmp"), lambda p: p.update(theme=MANAGED_THEME),
                     lambda p: p.update(font='Bad"/font'), lambda p: p.update(schemaVersion=2),
                     lambda p: p["windows"].update({"exec": {"str": "touch /tmp/owned"}}),
                     lambda p: p["windows"].update({"general:gaps_in": {"css": "0;exec bad"}}),
                     lambda p: p["files"].update({"hyprland.lua": "os.execute('bad')"}),
                     lambda p: p["colors"].update(accent="red"),
                     lambda p: p["shell"].update({"font.base-size": "12\nexec bad"}),
                     lambda p: p["animations"]["rules"][0].update(speed=-1),
                     lambda p: p["windows"].update({"decoration:rounding": {"float": float("inf")}})]
        for mutate in mutations:
            p = profile()
            mutate(p)
            with self.subTest(p=p), self.assertRaises((Failure, ValueError)):
                validate(p)

    def test_unknown_top_level_extension_survives(self):
        p = profile()
        p["futureConsumer"] = {"density": "comfortable"}
        self.assertEqual(validate(p)["futureConsumer"], {"density": "comfortable"})

    def test_remote_absent_vs_malformed(self):
        self.assertIsNone(read_remote({"desk": {}}))
        p = profile()
        self.assertEqual(read_remote({"desk": {"appearance": {"current": json.dumps(p)}}}), p)
        for value in (None, [], {"desk": []}, {"appearance": {"current": "no"}}, {"appearance": []}):
            with self.subTest(value=value), self.assertRaises(Failure):
                read_remote(value)

    def test_gradient_channel_order_and_quoted_lua(self):
        p = profile()
        p["animations"]["curves"][0]["name"] = 'quote"name'
        code = window_lua(p)
        self.assertIn("rgba(112233ff)", code)
        self.assertIn("rgba(445566aa)", code)
        self.assertIn('quote\\"name', code)

    @unittest.skipUnless(shutil.which("Hyprland"), "Installed Hyprland config verifier is an integration check")
    def test_generated_lua_passes_real_hyprland_config_verifier(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hyprland.lua"
            path.write_text(window_lua(profile()))
            env = dict(os.environ, HOME=temp, XDG_CONFIG_HOME=temp, XDG_STATE_HOME=temp, XDG_CACHE_HOME=temp)
            result = subprocess.run(["Hyprland", "--verify-config", "-c", str(path)],
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        environment = patch.dict(os.environ, OMARCHY_PATH=str(self.home / "stock"))
        environment.start()
        self.addCleanup(environment.stop)
        self.theme = self.home / ".config/omarchy/themes/tokyo-night"
        self.theme.mkdir(parents=True)
        (self.theme / "backgrounds").mkdir()
        (self.theme / "neovim.lua").write_text("-- trusted local theme\n")
        config = self.home / ".config/hypr/hyprland.lua"
        config.parent.mkdir(parents=True)
        config.write_text("-- personal settings stay\n")
        self.current = self.home / ".local/state/omarchy/current"
        self.current.mkdir(parents=True)
        (self.current / "theme.name").write_text("old-theme\n")
        self.calls = []
        self.font = "Fixture Mono"
        self.errors = []

    def runner(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args[0] == "fc-list":
            return 0, b"Fixture Mono,FixtureMono\n"
        if args == ["omarchy", "font", "current"]:
            return 0, (self.font + "\n").encode()
        if args[:3] == ["omarchy", "font", "set"]:
            self.font = args[3]
        if args == ["hyprctl", "-j", "configerrors"]:
            return 0, json.dumps(self.errors).encode()
        if args[:3] == ["omarchy", "theme", "set"]:
            (self.current / "theme.name").write_text(MANAGED_THEME + "\n")
        return 0, b""

    def test_missing_prerequisites_do_not_modify_desktop(self):
        d = Desktop(self.home, self.runner)
        p = profile("missing")
        with self.assertRaises(Failure) as e:
            d.apply(p)
        self.assertEqual(e.exception.code, "missing-theme")
        self.assertFalse(self.calls)
        p = profile()
        p["font"] = "Missing Font"
        with self.assertRaises(Failure) as e:
            d.apply(p)
        self.assertEqual(e.exception.code, "missing-font")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual((self.current / "theme.name").read_text(), "old-theme\n")

    def test_applies_profile_preserves_local_source_and_suppresses_hooks(self):
        d = Desktop(self.home, self.runner)
        p = profile()
        self.font = "Old Font"
        d.apply(p)
        self.assertEqual(self.font, "Fixture Mono")
        self.assertEqual((self.current / "theme.name").read_text(), "tokyo-night\n")
        generated = self.theme.parent / MANAGED_THEME
        self.assertEqual((generated / "colors.toml").read_text(), colors_toml(p["colors"]))
        self.assertEqual((generated / "neovim.lua").read_text(), "-- trusted local theme\n")
        self.assertFalse((self.theme / "colors.toml").exists())
        before = self.home / ".local/state/omarchy-urbit-theme/hyprland-before-sync.lua"
        self.assertEqual(before.read_text(), "-- personal settings stay\n")
        for args, kwargs in self.calls:
            if args[:3] in (["omarchy", "theme", "set"], ["omarchy", "font", "set"]):
                self.assertEqual(kwargs["env"]["OMARCHY_URBIT_THEME_REMOTE"], "1")
        d.apply(p)
        config = (self.home / ".config/hypr/hyprland.lua").read_text()
        self.assertEqual(config.count("-- omarchy-urbit-theme appearance"), 1)

    def test_git_theme_cannot_smuggle_code_into_managed_theme(self):
        (self.theme / ".git").mkdir()
        (self.theme / "kitty.conf").write_text("shell something\n")
        Desktop(self.home, self.runner).apply(profile())
        generated = self.theme.parent / MANAGED_THEME
        self.assertFalse((generated / "kitty.conf").exists())
        self.assertFalse((generated / "neovim.lua").exists())

    def test_managed_theme_edits_are_not_destroyed(self):
        d = Desktop(self.home, self.runner)
        d.apply(profile())
        generated = self.theme.parent / MANAGED_THEME
        (generated / "my-notes").write_text("keep me")
        with self.assertRaises(Failure):
            d.apply(profile())
        self.assertEqual((generated / "my-notes").read_text(), "keep me")

    def test_config_errors_are_not_success(self):
        self.errors = ["unsupported option"]
        with self.assertRaises(Failure):
            Desktop(self.home, self.runner).apply(profile())


if __name__ == "__main__":
    unittest.main()
