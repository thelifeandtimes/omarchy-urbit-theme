"""Auxiliary hook setup for Omarchy's Git installer (which has no setup callback)."""

import hashlib
import tempfile
from pathlib import Path

try:
    from .instance import INSTANCE
    from .profile import atomic
    from .support import Failure, StateStore, command, dumps, loads
except ImportError:
    from instance import INSTANCE
    from profile import atomic
    from support import Failure, StateStore, command, dumps, loads

PLUGIN = "thelifeandtimes.urbit-theme"
HOOKS = (("theme-set", "omarchy-urbit-theme"), ("font-set", "omarchy-urbit-font"))


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise Failure("hook-setup", "A theme-sync hook is not a regular file. Preserve it before retrying setup.")
    return path.read_bytes()


def ensure_hooks(source=None, home=None, state_root=None, runner=command, instance=INSTANCE):
    """Called under desktop.lock, before credentials or synchronization.

    Only a native Git install at that instance's Omarchy plugin path self-installs
    hooks. Snapshot installations retain their installer's ownership receipt.
    Preflight both hooks before touching either; only replace matching owned
    bytes. Receipts live outside the checkout so native updates stay clean.
    """
    source = Path(source or Path(__file__).resolve().parents[1])
    home = Path(home or Path.home())
    installed = home / ".config/omarchy/plugins" / instance.plugin_id
    if source.resolve() != installed.resolve() or not (source / ".git").exists():
        return False
    root = Path(state_root or StateStore(instance=instance).root)
    suffix = "" if instance.profile == "default" else "." + instance.profile
    hooks = [(event, name, name + suffix) for event, name in HOOKS]
    receipt_path = root / "native-hooks.json"
    receipt = {"version": 1, "hooks": {}}
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = loads(regular(receipt_path))
        if (not isinstance(receipt, dict) or set(receipt) != {"version", "hooks"}
                or type(receipt["version"]) is not int or receipt["version"] != 1
                or not isinstance(receipt["hooks"], dict)
                or set(receipt["hooks"]) - {event + "/" + name for event, _, name in hooks}
                or any(not isinstance(v, str) or len(v) != 64 for v in receipt["hooks"].values())):
            raise Failure("hook-setup", "The native hook ownership receipt is invalid; existing hooks were preserved.")
    plans = []
    for event, source_name, name in hooks:
        payload = regular(source / "hooks" / source_name)
        target = home / ".config/omarchy/hooks" / (event + ".d") / name
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise Failure("hook-setup", "A theme-sync hook path contains a symlink; existing hooks were preserved.")
        existing = regular(target) if target.exists() else None
        key = event + "/" + name
        if existing is not None and existing != payload and checksum(existing) != receipt["hooks"].get(key):
            raise Failure("hook-setup", "The " + name + " hook was modified or is unmanaged. Preserve it and remove the conflict, then retry.")
        plans.append((event, source_name, name, target, payload, existing, key))
    for event, source_name, name, target, payload, existing, key in plans:
        if existing != payload:
            if suffix:
                # Omarchy uses the source basename as the installed hook name.
                # Stage only reviewed hook bytes after preflighting both targets.
                root.mkdir(mode=0o700, parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=".native-hooks-", dir=root) as temp:
                    staged = Path(temp) / name
                    staged.write_bytes(payload)
                    code, _ = runner(["omarchy", "hook", "install", event, str(staged)], timeout=10)
            else:
                code, _ = runner(["omarchy", "hook", "install", event, str(source / "hooks" / source_name)], timeout=10)
            if code or regular(target) != payload:
                raise Failure("hook-setup", "Omarchy could not install the " + name + " hook. Desktop sync will retry setup.", True)
        desired = checksum(payload)
        if receipt["hooks"].get(key) != desired:
            receipt["hooks"][key] = desired
            atomic(receipt_path, dumps(receipt))
    return True
