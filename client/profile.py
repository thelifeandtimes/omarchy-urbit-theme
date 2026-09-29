"""Versioned appearance superset and the local Omarchy adapter. No remote code."""

import base64
import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile

try:
    from .support import Failure, command, dumps, fingerprint, hex_color, loads, validate_palette
except ImportError:
    from support import Failure, command, dumps, fingerprint, hex_color, loads, validate_palette

DESK = "omarchy-urbit-theme"
PROFILE_LIMIT = 262144
MANAGED_THEME = "urbit-synced"
# These are data files, not terminal launch configuration or executable Lua.
THEME_FILES = ("btop.theme", "chromium.theme", "helix.toml", "icons.theme", "keyboard.rgb",
               "obsidian.css", "vscode-theme.json", "pi.json", "claude.json", "hermes.yaml", "t3code.json")
OPTIONS = tuple("general:" + k for k in ("gaps_in", "gaps_out", "border_size", "col.active_border", "col.inactive_border")) + tuple(
    "decoration:" + k for k in ("rounding", "rounding_power", "active_opacity", "inactive_opacity", "fullscreen_opacity",
    "dim_inactive", "dim_strength", "dim_special", "dim_around")) + tuple(
    "decoration:blur:" + k for k in ("enabled", "size", "passes", "ignore_opacity", "new_optimizations", "xray",
    "noise", "contrast", "brightness", "vibrancy", "vibrancy_darkness", "special", "popups", "popups_ignorealpha", "input_methods", "input_methods_ignorealpha")) + tuple(
    "decoration:" + section + ":" + k for section in ("shadow", "glow") for k in (
    "enabled", "range", "render_power", "sharp", "ignore_window", "color", "color_inactive", "offset", "scale")) + (
    "animations:enabled",) + tuple("group:" + k for k in ("col.border_active", "col.border_inactive", "col.border_locked_active", "col.border_locked_inactive")) + tuple(
    "group:groupbar:" + k for k in ("enabled", "font_family", "font_size", "font_weight_active", "font_weight_inactive",
    "indicator_height", "indicator_gap", "height", "gaps_in", "gaps_out", "text_color", "text_color_inactive",
    "col.active", "col.inactive", "col.locked_active", "col.locked_inactive", "gradients", "gradient_rounding", "gradient_round_only_edges"))


def text(value, limit=1024):
    return isinstance(value, str) and len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def slug(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,127}", value) and value != MANAGED_THEME


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".urbit-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def parse_shell(raw):
    """Match Omarchy's flat shell parser, including bare CSS width lists."""
    result, section = {}, ""
    for line in raw.splitlines():
        line = line.strip()
        match = re.fullmatch(r"\[([A-Za-z0-9_-]+)\]\s*(?:#.*)?", line)
        if match:
            section = match[1]
            continue
        match = re.fullmatch(r"([A-Za-z0-9_-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^#]*?))\s*(?:#.*)?", line)
        if match and section:
            result[section + "." + match[1]] = next(v for v in match.groups()[1:] if v is not None).strip()
    return result


def shell_toml(values):
    groups = {}
    for key, value in sorted(values.items()):
        section, key = key.split(".", 1)
        groups.setdefault(section, []).append(key + " = " + json.dumps(value))
    return "\n\n".join("[" + section + "]\n" + "\n".join(lines) for section, lines in groups.items()) + "\n"


def colors_toml(colors):
    return "".join(key + " = " + json.dumps(value) + "\n" for key, value in sorted(colors.items()))


def theme_receipt(directory):
    result = {}
    for path in directory.iterdir():
        if path.name == ".urbit-theme-managed":
            continue
        if path.is_symlink():
            result[path.name] = "link:" + os.readlink(path)
        elif path.is_file():
            result[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            raise Failure("desktop", "Unexpected directory in the generated theme; preserve it before retrying.")
    return result


def palette(profile):
    c = profile["colors"]
    primary = c.get("accent") or c.get("blue") or c["foreground"]
    result = dict(id=DESK, name=profile["theme"], dark=c["mode"] == "dark", primary=primary,
                  secondary=c.get("blue") or primary, tertiary=c.get("green") or c.get("blue") or primary,
                  background=c["background"], surface=c.get("lighter_background") or c["background"])
    for key, source in zip(("text", "muted", "raised", "error", "selection", "link"),
                           ("foreground", "muted", "lighter_background", "red", "selection", "blue")):
        result[key] = c.get(source, "")
    return validate_palette(result)


def content(profile):
    return {k: v for k, v in profile.items() if k not in ("updateId", "deviceId")}


def digest(profile):
    return fingerprint(content(profile))


def validate(profile):
    bad = Failure("profile", "The shared appearance profile is invalid or uses an unsupported version.")
    if not isinstance(profile, dict) or len(dumps(profile).encode()) > PROFILE_LIMIT:
        raise bad
    required = {"schemaVersion", "updateId", "deviceId", "theme", "colors", "font", "shell", "windows", "animations", "files"}
    if not required <= profile.keys() or type(profile["schemaVersion"]) is not int or profile["schemaVersion"] != 1 or not slug(profile["theme"]):
        raise bad
    for key in ("updateId", "deviceId"):
        if not isinstance(profile[key], str) or not re.fullmatch("[a-f0-9]{32}", profile[key]):
            raise bad
    if not text(profile["font"], 128) or not profile["font"].strip() or any(c in profile["font"] for c in '\\"<>/&'):
        raise bad
    for field in ("colors", "shell", "windows", "files"):
        if not isinstance(profile[field], dict) or len(profile[field]) > 512:
            raise bad
    for key, value in profile["colors"].items():
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", key) or not text(value, 1024) or not re.fullmatch(r"[a-zA-Z0-9#(),._+/% -]*", value):
            raise bad
    if profile["colors"].get("mode") not in ("light", "dark"):
        raise bad
    try:
        hex_color(profile["colors"].get("foreground"))
        palette(profile)
    except (KeyError, Failure):
        raise bad from None
    for key, value in profile["shell"].items():
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", key) or not text(value, 1024) or any(c in value for c in '\\"\''):
            raise bad
    for key, value in profile["windows"].items():
        if key not in OPTIONS or not isinstance(value, dict) or len(value) != 1:
            raise bad
        kind, v = next(iter(value.items()))
        if kind == "bool":
            valid = type(v) is bool
        elif kind in ("int", "float"):
            bound = 0xffffffff if key in ("group:groupbar:text_color", "group:groupbar:text_color_inactive") else 10000
            valid = type(v) in (int, float) and math.isfinite(v) and abs(v) <= bound
        elif kind == "vec2":
            valid = isinstance(v, list) and len(v) == 2 and all(type(n) in (int, float) and math.isfinite(n) and abs(n) <= 10000 for n in v)
        elif kind == "css":
            valid = text(v, 100) and re.fullmatch(r"-?\d+(?:\.\d+)?(?: +-?\d+(?:\.\d+)?){0,3}", v)
        elif kind == "gradient":
            valid = text(v, 1024) and re.fullmatch(r"(?:[0-9a-fA-F]{8} +)+-?\d+(?:\.\d+)?deg", v)
        elif kind == "str":
            valid = text(v, 128)
        else:
            valid = False
        if not valid:
            raise bad
    a = profile["animations"]
    if not isinstance(a, dict) or set(a) != {"curves", "rules"} or not all(isinstance(a[k], list) and len(a[k]) <= 128 for k in a):
        raise bad
    for curve in a["curves"]:
        if not isinstance(curve, dict) or set(curve) != {"name", "points"} or not text(curve["name"], 80):
            raise bad
        pts = curve["points"]
        if not isinstance(pts, list) or len(pts) != 4 or not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 100 for v in pts):
            raise bad
    for rule in a["rules"]:
        if (not isinstance(rule, dict) or set(rule) != {"name", "enabled", "speed", "bezier", "style"}
                or not all(text(rule[k], 128) for k in ("name", "bezier", "style")) or type(rule["enabled"]) is not bool
                or type(rule["speed"]) not in (int, float) or not math.isfinite(rule["speed"]) or not 0 < rule["speed"] <= 10000):
            raise bad
    for name, value in profile["files"].items():
        if name not in THEME_FILES or not isinstance(value, str) or len(value.encode()) > 65536 or "\x00" in value:
            raise bad
    return profile


def read_remote(body):
    if not isinstance(body, dict):
        raise Failure("profile", "The ship returned invalid appearance settings.")
    desk = body.get("desk", body)
    if not isinstance(desk, dict) or not isinstance(desk.get("appearance", {}), dict):
        raise Failure("profile", "The ship returned invalid appearance settings.")
    value = desk.get("appearance", {}).get("current")
    return None if value is None else validate(loads(value) if isinstance(value, str) else value)


def lua(value):
    if isinstance(value, dict):
        return "{" + ",".join("[" + lua(k) + "]=" + lua(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "{" + ",".join(lua(v) for v in value) + "}"
    if isinstance(value, str):
        # Lua does not accept JSON's \u escapes; emit Unicode directly.
        return json.dumps(value, ensure_ascii=False)
    return dumps(value)


def window_lua(profile):
    validate(profile)
    tree = {}
    for option, wrapped in profile["windows"].items():
        kind, value = next(iter(wrapped.items()))
        if kind == "gradient":
            tokens = value.split()
            colors = ["rgba(" + c[2:] + c[:2] + ")" for c in tokens[:-1]]
            value = colors[0] if len(colors) == 1 else {"colors": colors, "angle": float(tokens[-1][:-3])}
        elif option in ("group:groupbar:text_color", "group:groupbar:text_color_inactive"):
            value = "rgba(" + format(int(value) & 0xffffff, "06x") + format((int(value) >> 24) & 255, "02x") + ")"
        elif kind == "css":
            value = [float(v) for v in value.split()]
        target = tree
        parts = option.replace(".", ":").split(":")
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value
    result = "hl.config(" + lua(tree) + ")\n"
    for curve in profile["animations"]["curves"]:
        p = curve["points"]
        result += "hl.curve(" + lua(curve["name"]) + "," + lua({"type": "bezier", "points": [p[:2], p[2:]]}) + ")\n"
    for rule in sorted(profile["animations"]["rules"], key=lambda r: (len(r["name"]), r["name"])):
        result += "hl.animation(" + lua(dict(leaf=rule["name"], **{k: v for k, v in rule.items() if k != "name"})) + ")\n"
    return result


class Desktop:
    def __init__(self, home=None, runner=command):
        self.home = Path(home or Path.home())
        self.current = self.home / ".local/state/omarchy/current"
        self.runner = runner

    @staticmethod
    def command_label(args):
        # Labels are constant prefixes, never arbitrary arguments (font names,
        # paths, encoded theme payloads, or anything supplied by a caller).
        labels = (("omarchy", "theme", "color"), ("omarchy", "theme", "set"),
                  ("omarchy", "font", "current"), ("omarchy", "font", "set"),
                  ("hyprctl", "--batch"), ("hyprctl", "-j", "animations"),
                  ("hyprctl", "-j", "configerrors"), ("hyprctl", "reload"),
                  ("omarchy-shell", "shell", "applyTheme"), ("fc-list",))
        return next((" ".join(prefix) for prefix in labels if tuple(args[:len(prefix)]) == prefix), "local appearance command")

    def run(self, args, **kwargs):
        label = self.command_label(args)
        try:
            code, out = self.runner(args, **kwargs)
        except Failure as error:
            if error.code != "command":
                raise
            reason = getattr(error, "reason", "unknown")
            detail = {
                "missing": "was not found in the plugin's PATH",
                "permission": "could not start because permission was denied",
                "start": "could not be started",
                "timeout": "did not finish within " + str(kwargs.get("timeout", 8)) + " seconds",
                "output": "exceeded its " + str(kwargs.get("limit", 65536)) + "-byte output limit",
                "io": "failed while communicating with the process",
            }.get(reason, "could not complete")
            raise Failure("desktop-command", "'" + label + "' " + detail + ". The shared profile is retained for retry.", True) from None
        if code:
            raise Failure("desktop-command", "'" + label + "' exited with status " + str(code)
                          + ". The shared profile is retained for retry.", True)
        return out.decode("utf-8")

    def require_font(self, family):
        # Query the requested family, not every installed face. A normal large
        # font collection can exceed the generic command output cap.
        escaped = "".join("\\" + c if c in "\\,:-" else c for c in family)
        fonts = self.run(["fc-list", "--format", "%{family}\n", "--", ":family=" + escaped])
        if family.casefold() not in {alias.strip().casefold() for line in fonts.splitlines() for alias in line.split(",")}:
            raise Failure("missing-font", "Install the shared font '" + family + "', then retry.")

    def capture(self, device):
        lock = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "omarchy-theme-set.lock"
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            name = (self.current / "theme.name").read_text().strip()
            if not slug(name):
                raise Failure("desktop", "A theme transition is still in progress.", True)
            raw = self.run(["omarchy", "theme", "color", "--all"])
            colors = dict(line.split("\t", 1) for line in raw.splitlines())
            path = self.current / "theme"
            shell = parse_shell((path / "shell.toml").read_text()) if (path / "shell.toml").exists() else {}
            files = {name: (path / name).read_text() for name in THEME_FILES if (path / name).is_file()}
        # Theme typography travels; machine-level text zoom remains a local layer.
        font = self.run(["omarchy", "font", "current"]).strip()
        raw = self.run(["hyprctl", "--batch", ";".join("j/getoption " + key for key in OPTIONS)])
        windows = {}
        for chunk in re.split(r"\n\s*\n", raw):
            if not chunk.strip().startswith("{"):
                continue  # Older Hyprland may not implement a newer appearance option.
            v = loads(chunk)
            if v.get("option") in OPTIONS:
                value = {k: val for k, val in v.items() if k not in ("option", "set")}
                if value:
                    windows[v["option"]] = value
        rules, curves = loads(self.run(["hyprctl", "-j", "animations"]))
        animations = {
            "rules": [{k: r[k] for k in ("name", "enabled", "speed", "bezier", "style")} for r in sorted(rules, key=lambda r: r["name"])
                      if r.get("overridden") and not r["name"].startswith("__")],
            "curves": [{"name": c["name"], "points": [c[k] for k in ("X0", "Y0", "X1", "Y1")]} for c in sorted(curves, key=lambda c: c["name"])]}
        return validate(dict(schemaVersion=1, updateId=secrets.token_hex(16), deviceId=device,
                             theme=name, colors=colors, font=font, shell=shell, files=files,
                             windows=windows, animations=animations))

    def apply(self, profile):
        validate(profile)
        name = profile["theme"]
        stock = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "themes" / name
        user = self.home / ".config/omarchy/themes" / name
        if not stock.is_dir() and not user.is_dir():
            raise Failure("missing-theme", "Install the shared theme '" + name + "', then retry.")
        self.require_font(profile["font"])
        managed = self.home / ".config/omarchy/themes" / MANAGED_THEME
        marker = managed / ".urbit-theme-managed"
        if managed.exists() and (managed.is_symlink() or not marker.is_file()):
            raise Failure("desktop", "The urbit-synced theme directory is not owned by this plugin.")
        managed.parent.mkdir(parents=True, exist_ok=True)
        if managed.exists():
            receipt = loads(marker.read_bytes())
            actual = theme_receipt(managed)
            if receipt != actual:
                raise Failure("desktop", "The plugin's generated theme was edited; preserve those changes before retrying.")
        with tempfile.TemporaryDirectory(prefix=".urbit-stage-", dir=managed.parent) as directory:
            stage = Path(directory) / "theme"
            stage.mkdir()
            # Preserve app-specific configuration from the installed local theme.
            # Match Omarchy's exclusions for Git-installed themes; remote data can
            # never supply executable Lua or a terminal launch configuration.
            for source in (stock, user):
                if not source.is_dir():
                    continue
                git_theme = (source / ".git").is_dir() and not source.is_symlink()
                denied = {"alacritty.toml", "foot.ini", "kitty.conf", "ghostty.conf", "vscode.json"}
                for p in source.iterdir():
                    if p.name.startswith(".") or not p.is_file() or p.is_symlink() or p.suffix in (".png", ".jpg", ".webp"):
                        continue
                    if git_theme and (p.suffix == ".lua" or p.name in denied):
                        continue
                    if p.name == "hyprland.lua" or p.name.startswith("shell.") and p.name != "shell.toml":
                        continue
                    shutil.copyfile(p, stage / p.name)
            atomic(stage / "colors.toml", colors_toml(profile["colors"]))
            atomic(stage / "shell.toml", shell_toml(profile["shell"]))
            for filename, value in profile["files"].items():
                atomic(stage / filename, value)
            source = user / "backgrounds" if (user / "backgrounds").is_dir() else stock / "backgrounds"
            if source.is_dir():
                (stage / "backgrounds").symlink_to(source)
            receipt = theme_receipt(stage)
            atomic(stage / marker.name, dumps(receipt))
            backup = Path(directory) / "previous"
            if managed.exists():
                managed.rename(backup)
            try:
                stage.rename(managed)
            except Exception:
                if backup.exists():
                    backup.rename(managed)
                raise
        env = dict(os.environ, OMARCHY_URBIT_THEME_REMOTE="1")
        self.run(["omarchy", "theme", "set", MANAGED_THEME], timeout=90, env=env)
        atomic(self.current / "theme.name", name + "\n")
        # A trailing guarded user include wins over handwritten looknfeel without
        # replacing it. Its guard stops it overriding a different local theme.
        state = self.home / ".local/state/omarchy-urbit-theme"
        code = window_lua(profile)
        guarded = ('local f=io.open(os.getenv("HOME").."/.local/state/omarchy/current/theme.name","r")\n'
                   'local n=f and f:read("*l"); if f then f:close() end\n'
                   'if n==' + lua(name) + ' then\n' + code + 'end\n')
        atomic(state / "appearance.lua", guarded)
        config = (self.home / ".config/hypr/hyprland.lua").resolve()
        if config.is_relative_to(Path("/usr/share/omarchy")):
            raise Failure("desktop", "Hyprland's user configuration points at package-owned files.")
        include = '\n-- omarchy-urbit-theme appearance\ndo\n  local p = os.getenv("HOME") .. "/.local/state/omarchy-urbit-theme/appearance.lua"\n  local f = io.open(p, "r")\n  if f then f:close(); dofile(p) end\nend\n'
        original = config.read_text()
        if include not in original:
            if not (state / "hyprland-before-sync.lua").exists():
                atomic(state / "hyprland-before-sync.lua", original)
            atomic(config, original + include)
        self.run(["hyprctl", "reload"])
        errors = loads(self.run(["hyprctl", "-j", "configerrors"]))
        if any(errors):
            raise Failure("desktop", "Hyprland reported configuration errors; the shared profile remains pending.")
        self.run(["omarchy-shell", "shell", "applyTheme",
                  base64.b64encode(colors_toml(profile["colors"]).encode()).decode(),
                  base64.b64encode(shell_toml(profile["shell"]).encode()).decode()])
        if self.run(["omarchy", "font", "current"]).strip() != profile["font"]:
            self.run(["omarchy", "font", "set", profile["font"]], timeout=90, env=env)
