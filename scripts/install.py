#!/usr/bin/env python3
"""Explicit local snapshot installation; never authenticate or publish."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ID = "thelifeandtimes.urbit-theme"
MARKER = ".urbit-theme-install.json"
FILES = (
    "manifest.json", "Service.qml", "Panel.qml", "Model.js", "README.md",
    "LICENSE", "CONTRACT.md", "client/main.py", "client/support.py", "client/eyre.py",
)
HOOK = "omarchy-urbit-theme"
SOURCE = Path(__file__).resolve().parents[1]


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Expected a regular, non-symlink file: {path}")
    return path.read_bytes()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_parents(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise RuntimeError(f"Refusing a symlink in installation path: {parent}")


def paths(home):
    # Omarchy itself uses these HOME paths, not XDG_CONFIG_HOME.
    root = home / ".config/omarchy"
    target = root / "plugins" / ID
    hook = root / "hooks/theme-set.d" / HOOK
    safe_parents(target)
    safe_parents(hook)
    return target, hook


def inspect(target, hook):
    """Refuse removal/replacement of files not owned by this installer."""
    receipt = json.loads(regular(target / MARKER))
    if (receipt.get("id") != ID or receipt.get("version") != 1
            or not isinstance(receipt.get("files"), dict)
            or set(receipt["files"]) != set(FILES)):
        raise RuntimeError("The existing installation has no recognized ownership receipt.")
    found = set()
    for path in target.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(f"Refusing an installed symlink: {path}")
        if path.is_file() and path.relative_to(target).as_posix() != MARKER:
            found.add(path.relative_to(target).as_posix())
    if found != set(FILES):
        raise RuntimeError("The installed file set was changed; refusing to remove user files.")
    for name, expected in receipt["files"].items():
        if digest(regular(target / name)) != expected:
            raise RuntimeError(f"Installed file was modified; preserve it before replacing: {name}")
    if hook.exists() and digest(regular(hook)) != receipt.get("hook"):
        raise RuntimeError("The installed hook was modified; refusing to replace it.")
    return receipt


def run(args):
    subprocess.run(args, check=True, timeout=15)


def install(source, home, replace=False, enable=False, runner=run, restart_shell=False):
    target, hook = paths(home)
    payload = {name: regular(source / name) for name in FILES}
    hook_data = regular(source / "hooks" / HOOK)
    if json.loads(payload["manifest.json"]).get("id") != ID:
        raise RuntimeError("Unexpected plugin identity.")
    runner(["omarchy", "plugin", "validate", str(source)])
    exists = target.exists()
    if exists:
        if not replace:
            raise RuntimeError("Already installed. Use --replace for an unmodified managed snapshot.")
        inspect(target, hook)
    elif hook.exists():
        raise RuntimeError("A hook with this name already exists; refusing to overwrite it.")
    old_hook = regular(hook) if hook.exists() else None
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".urbit-theme-stage-", dir=target.parent) as temp:
        stage = Path(temp) / "plugin"
        stage.mkdir()
        for name, data in payload.items():
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            dest.chmod(0o644)
        receipt = dict(version=1, id=ID, files={k: digest(v) for k, v in payload.items()}, hook=digest(hook_data))
        (stage / MARKER).write_text(json.dumps(receipt, indent=2) + "\n")
        backup = Path(temp) / "previous"
        if exists:
            target.rename(backup)
        try:
            stage.rename(target)
            runner(["omarchy", "hook", "install", "theme-set", str(source / "hooks" / HOOK)])
            if regular(hook) != hook_data:
                raise RuntimeError("Omarchy did not install the expected hook.")
        except Exception:
            shutil.rmtree(target)
            if exists:
                backup.rename(target)
            if old_hook is not None:
                hook.write_bytes(old_hook)
                hook.chmod(0o755)
            elif hook.exists() and not hook.is_symlink() and regular(hook) == hook_data:
                hook.unlink()
            raise
    print(f"Installed snapshot: {target}")
    print(f"Installed theme hook: {hook}")
    if enable:
        runner(["omarchy-shell", "shell", "rescanPlugins"])
        runner(["omarchy", "plugin", "enable", ID, "--section", "right"])
        if restart_shell:
            runner(["omarchy", "restart", "shell"])
    else:
        print(f"Enable explicitly: omarchy-shell shell rescanPlugins; omarchy plugin enable {ID}")


def uninstall(home, runner=run):
    target, hook = paths(home)
    inspect(target, hook)
    # Disabling stops new service work. Pause all ships first so any in-flight
    # publication finishes and automatic consent does not survive a reinstall.
    runner(["omarchy", "plugin", "disable", ID])
    if hook.exists():
        hook.unlink()
    shutil.rmtree(target)
    runner(["omarchy-shell", "shell", "rescanPlugins"])
    print("Removed this plugin and hook. Ship settings and local session/state were retained.")
    print("Remove ships with X before uninstalling if you also want to forget their keyring sessions.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall"))
    parser.add_argument("--replace", action="store_true", help="replace an unmodified installer-owned snapshot")
    parser.add_argument("--enable", action="store_true", help="rescan and enable the bar widget after installation")
    parser.add_argument("--restart-shell", action="store_true", help="restart the shell after enabling to clear stale QML code")
    args = parser.parse_args()
    if args.action == "uninstall" and (args.replace or args.enable or args.restart_shell):
        parser.error("installation flags cannot be used for uninstall")
    if args.restart_shell and not args.enable:
        parser.error("--restart-shell requires --enable")
    try:
        if args.action == "install":
            install(SOURCE, Path.home(), args.replace, args.enable, restart_shell=args.restart_shell)
        else:
            uninstall(Path.home())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Installation stopped: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
