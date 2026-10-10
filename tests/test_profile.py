"""Portable profile, real Hyprland config validation, and inert desktop application."""

import copy
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from client.profile import (APPEARANCE_HEADER, Desktop, MANAGED_THEME, application_mismatches,
                            colors_toml, css_sides, digest, palette, parse_shell,
                            read_remote, shell_toml, validate, verify_application, window_lua)
from client.support import CommandFailure, Failure, command, contrast_ratio, mix_color, resolve_palette


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
                          ["#EEEEEE", "", "", "#EE3333", "#123ABC", "#123ABC"])
        self.assertEqual(p["colors"]["muted"], "#888888")
        for key in ("muted", "lighter_background", "red", "selection", "blue"):
            del p["colors"][key]
        t = palette(p)
        self.assertEqual([t[k] for k in ("muted", "raised", "error", "link")], [""] * 4)
        self.assertRegex(t["selection"], "^#[0-9A-F]{6}$")
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

    def test_gap_shorthand_has_named_sides_and_vectors_remain_arrays(self):
        for raw, expected in (("0", [0, 0, 0, 0]), ("5", [5, 5, 5, 5]),
                              ("2 8", [2, 8, 2, 8]), ("3 6 9", [3, 6, 9, 6]),
                              ("2 4 6 8", [2, 4, 6, 8])):
            self.assertEqual(css_sides(raw), dict(zip(("top", "right", "bottom", "left"), expected)))
        p = profile()
        code = window_lua(p)
        self.assertIn('["gaps_out"]={["top"]=10,["right"]=10,["bottom"]=10,["left"]=10}', code)
        self.assertIn('["offset"]={1,2}', code)
        for raw in ("", "1.5", "1 2 3 4 5", "1;bad", "10001"):
            with self.assertRaises(Failure):
                css_sides(raw)

    def test_readback_compares_all_requested_fields_with_semantic_normalization(self):
        p = profile()
        actual = copy.deepcopy(p)
        actual["windows"]["general:gaps_out"] = {"css": "10"}
        actual["windows"]["general:col.active_border"] = {"gradient": "FF112233 AA445566 405deg"}
        actual["colors"]["foreground"] = "#eeeeee"
        actual["windows"]["general:border_size"] = {"int": 2}
        self.assertEqual(application_mismatches(p, actual), [])
        for group, key, value in (("windows", "general:gaps_out", {"css": "0 0 0 0"}),
                                  ("windows", "decoration:rounding", {"int": 0}),
                                  ("colors", "background", "#000000"),
                                  ("shell", "font.base-size", "20"),
                                  ("files", "btop.theme", "wrong")):
            bad = copy.deepcopy(actual)
            bad[group][key] = value
            with self.assertRaises(Failure) as caught:
                verify_application(p, bad)
            self.assertIn(group + "." + key, caught.exception.message)
        actual["animations"]["rules"][0]["speed"] = 8
        self.assertIn("animations.rules.windows", application_mismatches(p, actual))

    @unittest.skipUnless(shutil.which("Hyprland"), "Installed Hyprland config verifier is an integration check")
    def test_generated_lua_roundtrips_gap_values_in_real_hyprland(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "hyprland.lua"
            env = dict(os.environ, HOME=temp, XDG_CONFIG_HOME=temp, XDG_STATE_HOME=temp, XDG_CACHE_HOME=temp)
            for raw, expected in (("0", (0, 0, 0, 0)), ("5", (5, 5, 5, 5)),
                                  ("2 8", (2, 8, 2, 8)), ("3 6 9", (3, 6, 9, 6)),
                                  ("3 7 11 15", (3, 7, 11, 15))):
                p = profile()
                p["windows"]["general:gaps_in"] = {"css": raw}
                p["windows"]["general:gaps_out"] = {"css": raw}
                assertions = ""
                for key in ("gaps_in", "gaps_out"):
                    assertions += 'local v=hl.get_config("general.' + key + '")\n'
                    for side, value in zip(("top", "right", "bottom", "left"), expected):
                        assertions += f'assert(v.{side} == {value}, "{key}.{side} mismatch")\n'
                path.write_text(window_lua(p) + assertions)
                result = subprocess.run(["Hyprland", "--verify-config", "-c", str(path)],
                                        env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_talon_surface_selection_and_preview_agree_across_dark_and_light_palettes(self):
        themes = {
            "everforest": ("dark", "#2D353B", "#343F44", "#D3C6AA", "#7FBBB3", "#3D484D"),
            "miasma": ("dark", "#222222", "#2C2C2C", "#C2C2B0", "#78824B", "#383838"),
            "solitude": ("dark", "#101315", "#101315", "#CACCCC", "#798186", "#343D41"),
            "latte": ("light", "#EFF1F5", "#DCE0E8", "#4C4F69", "#1E66F5", "#CCD0DA"),
        }
        with tempfile.TemporaryDirectory() as temp:
            current = Path(temp) / ".local/state/omarchy/current"
            current.mkdir(parents=True)
            for name, (mode, background, surface, foreground, accent, selection) in themes.items():
                with self.subTest(theme=name):
                    p = profile(name)
                    p["colors"].update(mode=mode, background=background, lighter_background=surface,
                                       foreground=foreground, accent=accent, selection=selection)
                    before = copy.deepcopy(p)
                    mapped = palette(p)
                    self.assertEqual(p, before)
                    self.assertEqual(mapped["surface"], surface)
                    self.assertEqual(mapped["raised"], "")
                    for base in (surface, background):
                        selected = mix_color(mapped["selection"], base, 0.4)
                        self.assertGreaterEqual(contrast_ratio(foreground, selected), 4.5)
                    selected = mix_color(mapped["selection"], surface, 0.4)
                    old = mix_color(selection, surface, 0.4)
                    self.assertGreater(contrast_ratio(selected, surface), contrast_ratio(old, surface))
                    self.assertGreater(contrast_ratio(selected, surface), 1.2)
                    (current / "theme.name").write_text(name)
                    raw = "".join(k + "\t" + v + "\n" for k, v in p["colors"].items()).encode()
                    self.assertEqual(resolve_palette(temp, lambda args: (0, raw)), mapped)


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

    def test_old_generated_override_is_detected_and_repaired(self):
        d = Desktop(self.home, self.runner)
        self.assertFalse(d.needs_repair())
        path = self.home / ".local/state/omarchy-urbit-theme/appearance.lua"
        path.parent.mkdir(parents=True)
        path.write_text('hl.config({general={gaps_out={10,10,10,10}}})\n')
        self.assertTrue(d.needs_repair())
        d.apply(profile())
        self.assertTrue(path.read_text().startswith(APPEARANCE_HEADER))
        self.assertFalse(d.needs_repair())

    def test_large_font_catalog_does_not_prevent_shared_profile_application(self):
        # Reproduce the affected machine's 116,124-byte listing through the real
        # bounded subprocess runner. The filtered family remains a small result.
        fontconfig = '''
import sys
if ":family=Fixture Mono" in sys.argv:
    print("Fixture Mono,FixtureMono")
else:
    sys.stdout.write("Fixture Mono\\n" + "x" * (116124 - len("Fixture Mono\\n")))
'''
        def runner(args, **kwargs):
            if args[0] == "fc-list":
                return command([sys.executable, "-c", fontconfig, *args[1:]], **kwargs)
            return self.runner(args, **kwargs)

        with self.assertRaises(CommandFailure) as error:
            runner(["fc-list", "--format", "%{family}\n"])
        self.assertEqual(error.exception.reason, "output")
        Desktop(self.home, runner).apply(profile())
        self.assertEqual((self.current / "theme.name").read_text(), "tokyo-night\n")

    def test_command_failures_name_only_the_safe_command_and_reason(self):
        for reason, message in (("missing", "not found"), ("permission", "permission was denied"),
                                ("timeout", "90 seconds"), ("output", "65536-byte output limit"),
                                ("io", "communicating"), ("start", "could not be started")):
            def runner(args, **kwargs):
                raise CommandFailure(reason)
            with self.subTest(reason=reason), self.assertRaises(Failure) as caught:
                Desktop(self.home, runner).run(["omarchy-shell", "shell", "applyTheme", "PRIVATE-PAYLOAD"], timeout=90)
            self.assertIn("omarchy-shell shell applyTheme", caught.exception.message)
            self.assertIn(message, caught.exception.message)
            self.assertNotIn("PRIVATE-PAYLOAD", caught.exception.message)
            self.assertEqual(caught.exception.code, "desktop-command")

    def test_nonzero_exit_names_command_without_forwarding_output(self):
        def runner(args, **kwargs):
            return 7, b"PRIVATE-OUTPUT"
        with self.assertRaises(Failure) as caught:
            Desktop(self.home, runner).run(["omarchy", "theme", "set", "private-theme"])
        self.assertIn("'omarchy theme set' exited with status 7", caught.exception.message)
        self.assertNotIn("PRIVATE-OUTPUT", caught.exception.message)
        self.assertNotIn("private-theme", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
