"""One cooperative Theme helper per HOME; account/profile state remains separate.

The durable selection survives a shell restart. This is coordination among these
helpers, not protection against arbitrary programs running as the same Unix user.
"""
import contextlib
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile

try:
    from .instance import INSTANCE
    from .support import Failure
except ImportError:
    from instance import INSTANCE
    from support import Failure

STATE_PATTERN = r"omarchy-urbit-theme(?:\.[a-z][a-z0-9-]{0,31})?"
HEADER = "Network Theme owner 1\n"
RELATIVE_ROOT = "/.local/state/omarchy-urbit-theme-ownership"


def private(info, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077
            or not directory and info.st_nlink != 1):
        raise Failure("state", "Appearance ownership state is not private; it was preserved.")


class Ownership:
    def __init__(self, home=None, instance=INSTANCE):
        self.root = Path(home or Path.home()) / RELATIVE_ROOT.lstrip("/")
        self.name = instance.state_name
        if not re.fullmatch(STATE_PATTERN, self.name):
            raise Failure("state", "Invalid appearance owner identity.")
        self.fd = None

    def read(self, key, *, selection=False):
        assert key in ("active", "owner")
        try:
            private(self.root.lstat(), directory=True)
            fd = os.open(self.root / key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        except OSError:
            raise Failure("state", "Appearance ownership state could not be read.") from None
        with os.fdopen(fd, "rb") as stream:
            private(os.fstat(stream.fileno()))
            data = stream.read(257)
        try:
            value = data.decode("ascii")
        except UnicodeError:
            value = ""
        if not value.startswith(HEADER) or not value.endswith("\n"):
            raise Failure("state", "Unknown appearance ownership format; it was preserved.")
        fields = value[len(HEADER):-1].split("\n")
        if (len(fields) != (2 if key == "active" else 1)
                or fields[0] != "-" and not re.fullmatch(STATE_PATTERN, fields[0])
                or key == "active" and not re.fullmatch(r"[a-f0-9]{32}", fields[1])):
            raise Failure("state", "Unknown appearance ownership format; it was preserved.")
        return tuple(fields) if selection else fields[0]

    def write(self, key, name):
        if self.fd is None:
            raise Failure("busy", "Appearance changes require the desktop ownership lock.")
        previous = self.read(key, selection=True)  # Never replace unknown formats.
        assert name == "-" or re.fullmatch(STATE_PATTERN, name)
        suffix = ""
        if key == "active":
            # Survives same-profile shell restart; a profile switch invalidates
            # delayed hook output from every previous selection of that profile.
            token = previous[1] if previous and previous[0] == name else secrets.token_hex(16)
            suffix = "\n" + token
        fd, temporary = tempfile.mkstemp(prefix=".owner-", dir=self.root)
        try:
            with os.fdopen(fd, "w") as stream:
                stream.write(HEADER + name + suffix + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.root / key)
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary)

    @contextlib.contextmanager
    def hold(self):
        if self.fd is not None:
            yield self
            return
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        private(self.root.lstat(), directory=True)
        try:
            fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except OSError:
            raise Failure("state", "Appearance ownership lock could not be opened.") from None
        try:
            private(os.fstat(fd))
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure("busy", "Another Theme profile owns this desktop; stop its helper before switching.") from None
            self.fd = fd
            self.read("owner")
            self.write("active", self.name)
            yield self
        finally:
            self.fd = None
            os.close(fd)

    def notification_selection(self):
        selected = self.read("active", selection=True)
        return selected[1] if selected and selected[0] == self.name else None

    def notification_current(self, notice):
        selected = self.notification_selection()
        return selected is not None and notice.get("selection") == selected


def legacy_include(name):
    return ('\n-- ' + name + ' appearance\ndo\n  local p = os.getenv("HOME") .. "/.local/state/' + name + '/appearance.lua"\n'
            '  local f = io.open(p, "r")\n  if f then f:close(); dofile(p) end\nend\n')


def owner_lua(name):
    # The identifier is validated; Lua reads the bounded, versioned record without
    # executing it. Missing or future records disable the include.
    assert re.fullmatch(STATE_PATTERN, name)
    return ('local o = io.open(os.getenv("HOME") .. "' + RELATIVE_ROOT + '/owner", "r")\n'
            'local v = o and o:read(256); if o then o:close() end\n'
            'if v == "Network Theme owner 1\\n' + name + '\\n" then\n')


def guarded_include(name):
    return ('\n-- ' + name + ' appearance\ndo\n' + owner_lua(name)
            + '  local p = os.getenv("HOME") .. "/.local/state/' + name + '/appearance.lua"\n'
            '  local f = io.open(p, "r")\n  if f then f:close(); dofile(p) end\nend\nend\n')


def guard_includes(original, current):
    # Transform only byte-exact previously generated blocks. Leave modified user
    # includes intact and refuse before any desktop command if one is ambiguous.
    names = set(re.findall(STATE_PATTERN + r"(?=/appearance\.lua)", original))
    result = original
    for name in names:
        result = result.replace(legacy_include(name), guarded_include(name))
        remainder = result.replace(guarded_include(name), "")
        if name + "/appearance.lua" in remainder:
            raise Failure("desktop", "An appearance include was edited; preserve and review it before switching profiles.")
    include = guarded_include(current)
    if include not in result:
        result += include
    return result
