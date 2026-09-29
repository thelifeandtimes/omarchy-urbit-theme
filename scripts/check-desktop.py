#!/usr/bin/env python3
"""Read-only live capture, then validate generated Lua in Hyprland's config-only mode."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.profile import Desktop, palette, window_lua

profile = Desktop().capture("0" * 32)
with tempfile.TemporaryDirectory(prefix="urbit-theme-verify-") as temp:
    config = Path(temp) / "hyprland.lua"
    config.write_text(window_lua(profile))
    result = subprocess.run(["Hyprland", "--verify-config", "-c", str(config)], capture_output=True,
                            text=True, timeout=20, env=dict(os.environ, HOME=temp, XDG_CONFIG_HOME=temp,
                                                           XDG_STATE_HOME=temp, XDG_CACHE_HOME=temp))
    if result.returncode:
        print(result.stdout + result.stderr, file=sys.stderr)
        sys.exit(1)
print("Captured", profile["theme"], "with", len(profile["colors"]), "palette values,",
      len(profile["shell"]), "shell values,", len(profile["windows"]), "window options and",
      len(profile["animations"]["rules"]), "animation rules.")
print("Generated appearance configuration passed Hyprland validation; desktop was not changed.")
print("Talon palette fields:", ", ".join(k for k in palette(profile) if k not in ("id", "name", "dark")))
