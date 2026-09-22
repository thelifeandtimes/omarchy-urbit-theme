"""Local palette, private state, and Secret Service support (stdlib only)."""

import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


PLUGIN_ID = "omarchy-urbit-theme"
LIMIT = 1024 * 1024


class Failure(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable

    def public(self):
        return dict(code=self.code, message=self.message, retryable=self.retryable)


def malformed():
    return Failure("malformed", "Existing data is malformed; nothing was replaced.")


def dumps(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def loads(value):
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = item
        return result

    try:
        return json.loads(value, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise malformed() from None


def origin(value):
    error = Failure("url", "Use an HTTPS origin, or HTTP with literal localhost or a loopback IP.")
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise error
    if any(ord(c) <= 32 or ord(c) >= 127 for c in value) or any(c in value for c in "\\?#%"):
        raise error
    try:
        url = urlsplit(value)
        host, port = url.hostname, url.port
        if (url.scheme not in ("https", "http") or not host or url.netloc.endswith(":") or url.username is not None
                or url.password is not None or url.path not in ("", "/") or port == 0):
            raise error
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
            if len(host) > 253 or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                          for label in host.split(".")):
                raise error
        loopback = address and (address.is_loopback or
                               isinstance(address, ipaddress.IPv6Address) and
                               address.ipv4_mapped and address.ipv4_mapped.is_loopback)
        if url.scheme == "http" and host != "localhost" and not loopback:
            raise error
        host = address.compressed if address else host
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            # Keep the origin stable across Python versions' IPv6 formatting.
            host = "::ffff:" + str(address.ipv4_mapped)
        host = "[" + host + "]" if ":" in host else host
        suffix = "" if port in (None, 443 if url.scheme == "https" else 80) else ":" + str(port)
        return url.scheme + "://" + host + suffix
    except ValueError:
        raise error from None


def command(argv, data=b"", timeout=8, limit=65536):
    """Bound both time and output, without placing captured secrets on disk."""
    error = Failure("command", "A required local command failed or is unavailable.", True)
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        raise error from None
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as selector:
            os.set_blocking(process.stdout.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ)
            if data:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            sent = 0
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise error
                for key, _ in selector.select(remaining):
                    if key.fileobj is process.stdin:
                        sent += os.write(process.stdin.fileno(), data[sent:sent + 4096])
                        if sent == len(data):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            selector.unregister(process.stdout)
                        output.extend(chunk)
                        if len(output) > limit:
                            raise error
            result = process.wait(timeout=max(0.001, deadline - time.monotonic()))
            return result, bytes(output)
    except (OSError, subprocess.TimeoutExpired):
        raise error from None
    finally:
        # Also reap children of shell-based commands if they outlive their parent.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        process.stdout.close()
        process.stdin.close()


def hex_color(value):
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise Failure("palette", "The current theme has missing or invalid base colors.", True)
    return value.upper()


def resolve_palette(home=None, runner=command):
    current = Path(home or Path.home()) / ".local/state/omarchy/current"
    result, raw = runner(["omarchy", "theme", "color", "--file",
                          str(current / "theme/colors.toml"), "--all"])
    if result:
        raise Failure("palette", "Omarchy could not resolve the current palette.", True)
    try:
        colors = {}
        for line in raw.decode("utf-8").splitlines():
            key, value = line.split("\t", 1)
            if key in colors:
                raise ValueError()
            colors[key] = value.strip()
        with (current / "theme.name").open("rb") as stream:
            raw_name = stream.read(1026)
        if len(raw_name) > 1025:
            raise ValueError()
        name = raw_name.decode("utf-8").strip()
        if not name or len(name) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in name):
            raise ValueError()
    except (OSError, UnicodeError, ValueError):
        raise Failure("palette", "The current Omarchy theme is unavailable or invalid.", True) from None
    background = hex_color(colors.get("background"))
    foreground = hex_color(colors.get("foreground"))
    primary = hex_color(colors.get("accent") or colors.get("blue") or foreground)
    secondary = hex_color(colors.get("blue") or primary)
    tertiary = hex_color(colors.get("green") or secondary)
    surface = hex_color(colors.get("lighter_background") or background)
    mode = colors.get("mode")
    if mode not in ("dark", "light"):
        raise Failure("palette", "Omarchy did not resolve a valid theme mode.", True)
    return dict(id=PLUGIN_ID, name=name, dark=mode == "dark", primary=primary,
                secondary=secondary, tertiary=tertiary, background=background, surface=surface)


def fingerprint(palette):
    canonical = json.dumps(palette, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_palette(value):
    keys = {"id", "name", "dark", "primary", "secondary", "tertiary", "background", "surface"}
    if (not isinstance(value, dict) or set(value) != keys or value["id"] != PLUGIN_ID
            or type(value["dark"]) is not bool or not isinstance(value["name"], str)
            or not value["name"].strip() or len(value["name"]) > 256
            or any(ord(c) < 32 or ord(c) == 127 for c in value["name"])):
        raise Failure("palette", "The supplied palette is invalid.")
    result = dict(value)
    for key in ("primary", "secondary", "tertiary", "background", "surface"):
        result[key] = hex_color(value[key])
    return result


def empty_state():
    return {"ships": []}


def empty_ship():
    return dict(id="", ship="", url="", automatic=False, pending=False,
                authenticationRequired=False, lastPublished="", lastTheme="", lastError="")


def empty_record():
    return dict(version=2, ships=[])


def account(url, ship):
    return hashlib.sha256((url + "\n" + ship).encode()).hexdigest()


def validate_record(record):
    if not isinstance(record, dict) or type(record.get("version")) is not int:
        raise malformed()
    migrating = record["version"] == 1
    if migrating:
        legacy = dict(connected=False, **{k: v for k, v in empty_ship().items() if k != "id"})
        if (set(record) != {"version", "state", "fingerprint", "account"}
                or not isinstance(record["state"], dict) or set(record["state"]) != set(legacy)
                or any(type(record["state"][k]) is not type(v) for k, v in legacy.items())):
            raise malformed()
        state = record["state"]
        for key in ("fingerprint", "account"):
            if not isinstance(record[key], str) or (record[key] and not re.fullmatch("[0-9a-f]{64}", record[key])):
                raise malformed()
        if state["connected"]:
            if record["account"] != account(state["url"], state["ship"]):
                raise malformed()
            row = {k: v for k, v in state.items() if k != "connected"}
            record = dict(version=2, ships=[dict(row, id=record["account"], fingerprint=record["fingerprint"])])
        else:
            if (state["url"] or state["ship"] or state["automatic"] or state["pending"]
                    or record["account"] or record["fingerprint"]):
                raise malformed()
            return empty_record()
    if (record["version"] != 2 or set(record) != {"version", "ships"}
            or not isinstance(record["ships"], list) or len(record["ships"]) > 64):
        raise malformed()
    template = dict(empty_ship(), fingerprint="")
    ids, urls = set(), set()
    for row in record["ships"]:
        if (not isinstance(row, dict) or set(row) != set(template)
                or any(type(row[k]) is not type(v) for k, v in template.items())):
            raise malformed()
        if (not re.fullmatch("[0-9a-f]{64}", row["id"])
                or row["fingerprint"] and not re.fullmatch("[0-9a-f]{64}", row["fingerprint"])
                or len(row["ship"]) > 128 or not re.fullmatch(r"~[a-z]+(?:-+[a-z]+)*", row["ship"])
                or row["id"] in ids or row["url"] in urls):
            raise malformed()
        try:
            if origin(row["url"]) != row["url"]:
                raise malformed()
        except Failure:
            raise malformed() from None
        for key, limit in (("lastPublished", 40), ("lastTheme", 256), ("lastError", 512)):
            if len(row[key]) > limit or any(ord(c) < 32 or ord(c) == 127 for c in row[key]):
                if not migrating:
                    raise malformed()
                # V1 allowed incompatible display text; it must not trap an account.
                row[key] = "" if key == "lastPublished" else "".join(
                    c for c in row[key] if ord(c) >= 32 and ord(c) != 127)[:limit]
        if row["lastPublished"] and not re.fullmatch(
                r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)", row["lastPublished"]):
            if not migrating:
                raise malformed()
            row["lastPublished"] = ""
        ids.add(row["id"])
        urls.add(row["url"])
    return record


class StateStore:
    def __init__(self, root=None):
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
        self.root = Path(root) if root is not None else base / PLUGIN_ID
        if not self.root.is_absolute():
            raise Failure("state", "The state directory must be absolute.")

    @contextlib.contextmanager
    def locked(self):
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.root.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                raise OSError()
            os.chmod(self.root, 0o700)
            fd = os.open(self.root / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "rb") as lock:
                self._private(lock.fileno())
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Failure("busy", "Another helper operation is running.", True) from None
                yield
        except OSError:
            raise Failure("state", "Private local state could not be accessed.") from None

    @staticmethod
    def _private(fd):
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise OSError()
        os.fchmod(fd, 0o600)

    def load(self):
        try:
            fd = os.open(self.root / "state.json", os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return empty_record()
        with os.fdopen(fd, "rb") as stream:
            self._private(stream.fileno())
            raw = stream.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise malformed()
        return validate_record(loads(raw))

    def save(self, record):
        path = None
        try:
            fd, path = tempfile.mkstemp(prefix=".state-", dir=self.root)
            with os.fdopen(fd, "wb") as stream:
                stream.write(dumps(record).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(path, self.root / "state.json")
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if path is not None:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(path)


class Keyring:
    def __init__(self, runner=command):
        self.runner = runner

    def _call(self, action, url, data=b""):
        args = ["secret-tool", action]
        if action == "store":
            args += ["--label=Omarchy Urbit Theme session"]
        args += ["application", PLUGIN_ID, "origin", url]
        try:
            result, output = self.runner(args, data=data, timeout=8, limit=16384)
        except Failure:
            raise Failure("keyring", "Secret Service is unavailable or locked; no plaintext fallback is used.", True) from None
        if result:
            raise Failure("keyring", "Secret Service could not complete the session operation.", True)
        return output

    def store(self, url, session):
        self._call("store", url, dumps(session).encode())
        try:
            verified = self.lookup(url, session["ship"])
        except Failure:
            raise Failure("keyring", "The session could not be verified in Secret Service.", True) from None
        if verified != session:
            raise Failure("keyring", "The session could not be verified in Secret Service.", True)

    def lookup(self, url, ship):
        raw = self._call("lookup", url)
        session = loads(raw)
        if (not isinstance(session, dict) or set(session) != {"url", "ship", "cookieName", "cookieValue"}
                or session["url"] != url or session["ship"] != ship
                or session["cookieName"] != "urbauth-" + ship
                or not isinstance(session["cookieValue"], str)
                or not re.fullmatch(r"[A-Za-z0-9._~-]{1,4096}", session["cookieValue"])):
            raise Failure("authentication", "The saved session does not match this account. Disconnect and sign in again.")
        return session

    def clear(self, url):
        self._call("clear", url)
