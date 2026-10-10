#!/usr/bin/env python3
"""Read-only live capture, then validate generated Lua in Hyprland's config-only mode."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.profile import Desktop, application_mismatches, css_sides, palette, window_lua
from client.sync import Disk

profile = Desktop().capture("0" * 32)
with tempfile.TemporaryDirectory(prefix="urbit-theme-verify-") as temp:
    config = Path(temp) / "hyprland.lua"
    checks = ""
    for option in ("general:gaps_in", "general:gaps_out"):
        if option not in profile["windows"]:
            continue
        sides = css_sides(profile["windows"][option]["css"])
        checks += 'local gap = hl.get_config("' + option + '")\n'
        for side, value in sides.items():
            checks += f'assert(gap.{side} == {value}, "{option}.{side} readback mismatch")\n'
    config.write_text(window_lua(profile) + checks)
    result = subprocess.run(["Hyprland", "--verify-config", "-c", str(config)], capture_output=True,
                            text=True, timeout=20, env=dict(os.environ, HOME=temp, XDG_CONFIG_HOME=temp,
                                                           XDG_STATE_HOME=temp, XDG_CACHE_HOME=temp))
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
        sys.exit(1)
print("Captured", profile["theme"], "with", len(profile["colors"]), "palette values,",
      len(profile["shell"]), "shell values,", len(profile["windows"]), "window options and",
      len(profile["animations"]["rules"]), "animation rules.")
print("Generated appearance configuration passed Hyprland validation and gap readback; desktop was not changed.")
print("Talon palette fields:", ", ".join(k for k in palette(profile) if k not in ("id", "name", "dark")))
disk = Disk()
if disk.path.exists():
    observed = disk.load()["observed"]
    if observed:
        differences = application_mismatches(observed, profile)
        print("Differences from the last observed shared profile:", ", ".join(differences) if differences else "none")
if Desktop().needs_repair():
    print("A legacy generated override will be repaired at the next connected reconciliation.")
