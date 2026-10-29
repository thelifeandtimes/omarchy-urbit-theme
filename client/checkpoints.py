"""Offline, profile-bound checkpoints for Theme-controlled appearance.

Snapshots contain local theme data, never ship sessions. Explicit restore request
IDs distinguish retries from a later, intentional restore of the same checkpoint.
"""
import base64
import contextlib
import fcntl
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

try:
    from .appearance import STATE_PATTERN, guarded_include, legacy_include, private
    from .profile import (PROFILE_LIMIT, atomic, colors_toml, dumps, loads,
                          shell_toml, theme_receipt, validate, verify_application)
    from .support import Failure
except ImportError:
    from appearance import STATE_PATTERN, guarded_include, legacy_include, private
    from profile import (PROFILE_LIMIT, atomic, colors_toml, dumps, loads,
                         shell_toml, theme_receipt, validate, verify_application)
    from support import Failure

LIMIT = 8 * 1024 * 1024
FILE_LIMIT = 1024 * 1024
TOTAL_LIMIT = 4 * 1024 * 1024
MAX_RECEIPTS = 128


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def identifier(value, length):
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{" + str(length) + "}", value)


def read(path, limit=LIMIT):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        private(os.fstat(stream.fileno()))
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise Failure("state", "Appearance checkpoint state exceeds its size limit; it was preserved.")
    return raw


def durable(path, value):
    atomic(path, dumps(value))
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def user_configuration(raw):
    # Candidate profiles can append exact generated includes. Unrelated edits
    # invalidate the preflight rather than being overwritten during rollback.
    for name in set(re.findall(STATE_PATTERN + r"(?=/appearance\.lua)", raw)):
        raw = raw.replace(guarded_include(name), "").replace(legacy_include(name), "")
    return digest(raw.encode())


@contextlib.contextmanager
def theme_read_lock():
    # Keep profile observation and frozen files on the same native theme-set
    # generation. Refuse an active native setter; never wait indefinitely.
    path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "omarchy-theme-set.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise Failure("state", "The native theme lock is not an owned regular file.")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("busy", "A native theme change is in progress; retry the appearance checkpoint or restoration.") from None
        yield
    finally:
        os.close(fd)


def frozen_files(current):
    info = current.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise Failure("state", "The current theme is not an owned directory.")
    files = {}; total = 0
    for p in current.iterdir():
        if p.name == "backgrounds" and (p.is_symlink() or p.is_dir()):
            continue  # Wallpaper is explicitly outside this checkpoint's scope.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", p.name) or p.is_symlink():
            raise Failure("state", "Unsupported generated theme entry; checkpointing was refused.")
        fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise Failure("state", "Generated theme entry is not an owned regular file.")
            data = stream.read(FILE_LIMIT + 1)
        total += len(data)
        if len(data) > FILE_LIMIT or total > TOTAL_LIMIT or len(files) >= 64:
            raise Failure("state", "Generated theme exceeds the checkpoint limit.")
        files[p.name] = base64.b64encode(data).decode()
    return files


class Checkpoints:
    def __init__(self, desktop):
        self.desktop = desktop
        self.root = desktop.appearance / "checkpoints"
        self.receipts = desktop.appearance / "restore-receipts"
        self.owner = desktop.instance.state_name

    def directory(self, path):
        self.desktop.appearance.mkdir(parents=True, exist_ok=True, mode=0o700)
        private(self.desktop.appearance.lstat(), directory=True)
        path.mkdir(exist_ok=True, mode=0o700)
        private(path.lstat(), directory=True)

    def pending(self):
        if not self.receipts.exists() and not self.receipts.is_symlink():
            return []
        private(self.desktop.appearance.lstat(), directory=True)
        private(self.receipts.lstat(), directory=True)
        paths = []
        for path in self.receipts.iterdir():
            paths.append(path)
            if len(paths) > MAX_RECEIPTS * 2:
                break
        if len(paths) > MAX_RECEIPTS * 2:
            raise Failure("state", "Appearance restoration history is full; preserve it before continuing.")
        rows = []
        for path in paths:
            if path.name.startswith(".urbit-"):
                read(path, 4096)  # Interrupted atomic temporary; preserve, never execute.
                continue
            if not re.fullmatch(r"[a-f0-9]{32}\.json", path.name):
                raise Failure("state", "Unknown appearance restoration history entry; it was preserved.")
            row = loads(read(path, 4096))
            if (not isinstance(row, dict) or set(row) != {"version", "owner", "request", "checkpoint", "phase"}
                    or type(row["version"]) is not int or row["version"] != 1
                    or row["owner"] != self.owner or row["request"] != path.stem
                    or not identifier(row["checkpoint"], 64) or row["phase"] not in ("applying", "verified")):
                raise Failure("state", "Unknown appearance restoration receipt; it was preserved.")
            rows.append(row)
        return rows

    def assert_no_pending(self):
        if any(row["phase"] != "verified" for row in self.pending()):
            raise Failure("state", "A local appearance restoration is incomplete; retry its exact checkpoint and request before starting sync.")

    def load(self, checkpoint):
        if not identifier(checkpoint, 64):
            raise Failure("input", "Specify the exact 64-character appearance checkpoint ID.")
        private(self.desktop.appearance.lstat(), directory=True)
        private(self.root.lstat(), directory=True)
        raw = read(self.root / (checkpoint + ".json"))
        if digest(raw) != checkpoint:
            raise Failure("state", "The appearance checkpoint changed; it was preserved.")
        return self.validate(loads(raw))

    def validate(self, value):
        if (not isinstance(value, dict) or set(value) != {"version", "owner", "profile", "files", "configuration", "backgroundPolicy"}
                or type(value["version"]) is not int or value["version"] != 1 or value["owner"] != self.owner
                or value["backgroundPolicy"] != "preserve-current" or not identifier(value["configuration"], 64)
                or not isinstance(value["files"], dict) or not 1 <= len(value["files"]) <= 64):
            raise Failure("state", "Unknown or differently bound appearance checkpoint; it was preserved.")
        validate(value["profile"])
        total = 0
        for name, encoded in value["files"].items():
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) or not isinstance(encoded, str):
                raise Failure("state", "Invalid checkpoint theme entry; it was preserved.")
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise Failure("state", "Invalid checkpoint theme bytes; they were preserved.") from None
            total += len(data)
            if len(data) > FILE_LIMIT or total > TOTAL_LIMIT:
                raise Failure("state", "Checkpoint theme assets exceed their size limit.")
        # Bind the frozen data-bearing theme files to the validated profile.
        required = {"colors.toml": colors_toml(value["profile"]["colors"]),
                    "shell.toml": shell_toml(value["profile"]["shell"]), **value["profile"]["files"]}
        for name, text in required.items():
            if base64.b64decode(value["files"].get(name, "")) != text.encode():
                raise Failure("state", "Checkpoint theme files disagree with the retained profile.")
        return value

    def capture(self, state):
        d = self.desktop
        with d.session(), theme_read_lock():
            self.assert_no_pending()
            if state["applying"] is not None or state["observed"] is None:
                raise Failure("state", "A verified applied appearance is required before checkpointing.")
            if d.ownership.read("owner") != self.owner:
                raise Failure("state", "This profile does not own the applied appearance.")
            configuration = d.prepare_configuration()
            actual = d.capture(state["deviceId"])
            verify_application(state["observed"], actual)
            source = d.home / ".config/omarchy/themes" / d.managed_theme
            if source.is_symlink() or not source.is_dir():
                raise Failure("state", "The generated source theme is unavailable for checkpointing.")
            marker = source / ".urbit-theme-managed"
            if loads(read(marker, PROFILE_LIMIT)) != theme_receipt(source):
                raise Failure("state", "The generated theme was edited; checkpointing was refused.")
            files = frozen_files(d.current / "theme")
            # Capture effective controlled values, preserving frozen ancillary
            # theme files rather than fetching the current installed source later.
            for name, text in {"colors.toml": colors_toml(actual["colors"]), "shell.toml": shell_toml(actual["shell"]), **actual["files"]}.items():
                files[name] = base64.b64encode(text.encode()).decode()
            value = {"version": 1, "owner": self.owner, "profile": actual, "files": files,
                     "configuration": user_configuration(configuration[1]), "backgroundPolicy": "preserve-current"}
            self.validate(value)
            raw = dumps(value).encode(); checkpoint = digest(raw)
            self.directory(self.root)
            path = self.root / (checkpoint + ".json")
            if path.exists() or path.is_symlink():
                if read(path) != raw:
                    raise Failure("state", "Checkpoint identity collision; existing state was preserved.")
            else:
                durable(path, value)
            self.load(checkpoint)
            return {"id": checkpoint, "owner": self.owner, "backgroundPolicy": "preserve-current"}

    def verify_files(self, value):
        if frozen_files(self.desktop.current / "theme") != value["files"]:
            raise Failure("desktop", "Restored theme file readback differs from the checkpoint; the request remains incomplete.")

    def restore(self, checkpoint, request):
        if not identifier(request, 32):
            raise Failure("input", "Specify an exact 32-character restoration request ID.")
        d = self.desktop
        with d.session():
            value = self.load(checkpoint)
            rows = self.pending()
            existing = next((row for row in rows if row["request"] == request), None)
            if existing and existing["checkpoint"] != checkpoint:
                raise Failure("state", "The restoration request is already bound to another checkpoint.")
            if any(row["request"] != request and row["phase"] != "verified" for row in rows):
                raise Failure("state", "Another restoration is incomplete; retry its exact request first.")
            if not existing and len(rows) >= MAX_RECEIPTS:
                raise Failure("state", "Appearance restoration history is full; preserve it before continuing.")
            configuration = d.prepare_configuration()
            if user_configuration(configuration[1]) != value["configuration"]:
                raise Failure("desktop", "User configuration changed after checkpointing; it was preserved for review.")
            if existing and existing["phase"] == "verified":
                with theme_read_lock():
                    verify_application(value["profile"], d.capture(value["profile"]["deviceId"]))
                    self.verify_files(value)
                    if d.ownership.read("owner") != self.owner:
                        raise Failure("state", "The completed restoration no longer owns the desktop; use a new request for another restore.")
                    return existing
            d.require_font(value["profile"]["font"])
            managed_name = "urbit-restored-" + checkpoint
            managed = d.home / ".config/omarchy/themes" / managed_name
            expected = {name: digest(base64.b64decode(data)) for name, data in value["files"].items()}
            if managed.exists() or managed.is_symlink():
                if (managed.is_symlink() or not managed.is_dir()
                        or loads(read(managed / ".urbit-theme-managed", PROFILE_LIMIT)) != expected
                        or theme_receipt(managed) != expected):
                    raise Failure("desktop", "The retained restore theme changed; it was preserved.")
            else:
                managed.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix=".urbit-restore-", dir=managed.parent) as temporary:
                    stage = Path(temporary) / "theme"; stage.mkdir(mode=0o700)
                    for name, data in value["files"].items():
                        path = stage / name
                        with path.open("wb") as stream:
                            stream.write(base64.b64decode(data));stream.flush();os.fsync(stream.fileno())
                        path.chmod(0o600)
                    durable(stage / ".urbit-theme-managed", expected)
                    stage.rename(managed)
                    parent = os.open(managed.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    try:
                        os.fsync(parent)
                    finally:
                        os.close(parent)
            self.directory(self.receipts)
            row = {"version": 1, "owner": self.owner, "request": request, "checkpoint": checkpoint, "phase": "applying"}
            path = self.receipts / (request + ".json")
            durable(path, row)  # Before any native command/config activation.
            d.activate(value["profile"], managed_name, configuration, skip_background=True)
            with theme_read_lock():
                actual = d.capture(value["profile"]["deviceId"])
                verify_application(value["profile"], actual)
                self.verify_files(value)
                if d.ownership.read("owner") != self.owner or theme_receipt(managed) != expected:
                    raise Failure("state", "Appearance restoration readback changed; the request remains incomplete.")
                row["phase"] = "verified"; durable(path, row)
                return row
