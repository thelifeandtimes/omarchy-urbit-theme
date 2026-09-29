"""Plugin-owned live profile coordinator. Standard library, existing %settings.

The shell owns this process. Only this process writes desktop.json; the hook
writes a separate durable notification. Neither file contains credentials.
"""

import contextlib
import copy
import fcntl
import os
from pathlib import Path
import queue
import re
import secrets
import sys
import threading
import time

try:
    from .eyre import Eyre
    from .native import ensure_hooks
    from .profile import DESK, Desktop, atomic, content, digest, palette, read_remote, validate
    from .support import Failure, Keyring, StateStore, command, dumps, loads
except ImportError:
    from eyre import Eyre
    from native import ensure_hooks
    from profile import DESK, Desktop, atomic, content, digest, palette, read_remote, validate
    from support import Failure, Keyring, StateStore, command, dumps, loads


def fresh():
    return dict(version=1, deviceId=secrets.token_hex(16), hub=None, paused=False,
                observed=None, baseline="", pending=None, attempted=False, baseId=None,
                applying=None, notification="")


class Disk:
    def __init__(self, root=None):
        self.root = Path(root or StateStore().root)
        self.path = self.root / "desktop.json"

    def load(self):
        if not self.path.exists():
            return fresh()
        fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            StateStore._private(stream.fileno())
            raw = stream.read(1048577)
        if len(raw) > 1048576:
            raise Failure("state", "The local desktop sync state exceeded its size limit.")
        value = loads(raw)
        if (not isinstance(value, dict) or set(value) != set(fresh()) or value["version"] != 1
                or type(value["paused"]) is not bool or type(value["attempted"]) is not bool
                or not isinstance(value["baseline"], str) or not isinstance(value["notification"], str)):
            raise Failure("state", "The local desktop sync state is invalid; it was not replaced.")
        if (not isinstance(value["deviceId"], str) or not re.fullmatch("[a-f0-9]{32}", value["deviceId"])
                or value["hub"] is not None and (not isinstance(value["hub"], dict) or set(value["hub"]) != {"id", "url", "ship"}
                    or not all(isinstance(v, str) and len(v) <= 2048 for v in value["hub"].values()))):
            raise Failure("state", "The local desktop identity is invalid; it was not replaced.")
        for key in ("observed", "pending", "applying"):
            if value[key] is not None:
                validate(value[key])
        return value

    def save(self, value):
        atomic(self.path, dumps(value))


class Coordinator:
    """Small last-arrival-wins state machine, also driven by simulated devices."""
    def __init__(self, disk, desktop, remote):
        self.disk, self.desktop, self.remote = disk, desktop, remote
        self.state = disk.load()
        self.first = True

    def save(self):
        self.disk.save(self.state)

    def intent(self, local):
        validate(local)
        if self.state["pending"] and digest(self.state["pending"]) == digest(local):
            return
        # Preserve extension fields from newer writers without copying stale
        # known fields (a removed theme file must really disappear).
        prior = self.state["observed"] or {}
        merged = dict(prior, **local)
        self.state.update(pending=merged, attempted=False, baseId=None)
        self.save()

    def reconcile(self, local, intentional=False):
        s = self.state
        if intentional:
            self.intent(local)  # Durable even if the following scry fails.
        current = self.remote.read()
        pending = s["pending"]
        if pending is not None:
            if current is not None and current["updateId"] == pending["updateId"]:
                s.update(pending=None, attempted=False)
                self.save()
            elif s["attempted"] and (current or {}).get("updateId") != s["baseId"]:
                # A lost ACK must not let a retry overwrite a later selection.
                s.update(pending=None, attempted=False)
                self.save()
            else:
                s.update(attempted=True, baseId=(current or {}).get("updateId"))
                self.save()
                self.remote.write(pending)
                current = self.remote.read()
                # After a successful ACK a different readback is a newer winner,
                # not grounds for repeatedly reasserting our old selection.
                s.update(pending=None, attempted=False)
                self.save()
        elif current is None:
            self.intent(local)
            return self.reconcile(local)
        if current is None:
            raise Failure("profile", "The shared appearance was removed during synchronization.", True)
        validate(current)
        needs_apply = s["applying"] is not None or (s["observed"] or {}).get("updateId") != current["updateId"] or self.first
        if needs_apply:
            if s["applying"] is not None or digest(current) != digest(local):
                s["applying"] = current
                self.save()  # Font application may restart our owning shell.
                self.desktop.apply(current)
                local = self.desktop.capture(s["deviceId"])
            s.update(observed=current, baseline=digest(local), applying=None)
            self.save()
        self.first = False
        return current


class Remote:
    def __init__(self, eyre):
        self.eyre = eyre

    def read(self):
        return read_remote(self.eyre.scry(DESK))

    def write(self, profile):
        self.eyre.poke("current", validate(profile), desk=DESK, bucket="appearance")


class Watch:
    def __init__(self, url, session):
        self.stop = threading.Event()
        self.changed = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, args=(url, session), daemon=True)
        self.thread.start()

    def run(self, url, session):
        delay = 1
        while not self.stop.is_set():
            try:
                self.error = None
                Eyre(url, session, timeout=25).watch(DESK, self.stop, self.changed, self.ready)
                delay = 1
            except Exception as e:
                self.error = e if isinstance(e, Failure) else Failure("network", "The appearance connection must reconnect.", True)
                self.ready.clear()
                self.changed.set()
            if self.stop.wait(delay):
                return
            delay = min(delay * 2, 30)


class Bridge:
    """Independent bounded lane: an offline Talon destination cannot stop desktop sync."""
    def __init__(self, accounts):
        self.accounts = accounts
        self.target = None
        self.stop = threading.Event()
        self.changed = threading.Event()
        self.errors = {}
        self.busy = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def set(self, profile, hub):
        target = (profile, hub)
        if self.target != target:
            self.target = target
            self.changed.set()

    def run(self):
        done, due, attempts = {}, {}, {}
        while not self.stop.is_set():
            self.changed.wait(1)
            self.changed.clear()
            target = self.target
            if target is None:
                continue
            profile, hub = target
            try:
                rows = self.accounts.load()["ships"]
                for row in rows:
                    if self.stop.is_set() or target != self.target:
                        break
                    if not row["automatic"] or row["authenticationRequired"]:
                        continue
                    stamp = (profile["updateId"], row["pending"])
                    if done.get(row["id"]) == stamp or due.get(row["id"], 0) > time.monotonic():
                        continue
                    # Observe the hub before every publication, including retries.
                    self.busy = True
                    session = Keyring().lookup(hub["url"], hub["ship"])
                    current = Remote(Eyre(hub["url"], session)).read()
                    if current is None or current["updateId"] != profile["updateId"]:
                        break
                    request = dict(force=False, expectedAccount={k: row[k] for k in ("id", "url", "ship")}, palette=palette(profile))
                    code, raw = command([sys.executable, "-B", str(Path(__file__).with_name("main.py")), "sync"],
                                        dumps(request).encode(), timeout=55, limit=262144)
                    result = loads(raw)
                    if code == 0 and result.get("ok"):
                        done[row["id"]] = (profile["updateId"], False)
                        attempts.pop(row["id"], None)
                        due.pop(row["id"], None)
                        self.errors.pop(row["id"], None)
                    else:
                        n = attempts.get(row["id"], 0)
                        due[row["id"]] = time.monotonic() + min(60, 2 ** min(n + 1, 6))
                        attempts[row["id"]] = n + 1
                        self.errors[row["id"]] = "Talon publication pending; retrying."
            except Exception:
                self.stop.wait(2)
            finally:
                self.busy = False


def notify(root=None, theme=False, font=None):
    if os.environ.get("OMARCHY_URBIT_THEME_REMOTE") == "1":
        return
    root = Path(root or StateStore().root)
    if theme:
        # A deliberate theme selection restores its own decoration before capture.
        # Font-only changes retain the current shared decoration.
        include = Path.home() / ".local/state/omarchy-urbit-theme/appearance.lua"
        if include.exists():
            include.unlink()
            command(["hyprctl", "reload"], timeout=4)
    value = {"id": secrets.token_hex(16)}
    if font is not None and isinstance(font, str) and len(font) <= 128:
        value["font"] = font
    atomic(root / "appearance-notify.json", dumps(value))


def notification(root):
    path = root / "appearance-notify.json"
    return loads(path.read_bytes()) if path.exists() else {"id": ""}


def serve():
    accounts, disk, desktop = StateStore(), Disk(), Desktop()
    with accounts.locked():
        pass
    fd = os.open(disk.root / "desktop.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        StateStore._private(lock.fileno())
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("busy", "Desktop sync is already running.") from None
        ensure_hooks(state_root=disk.root)
        state = disk.load()
        disk.save(state)
        inbox = queue.Queue()
        stopped = threading.Event()

        def input_loop():
            try:
                while not stopped.is_set():
                    line = sys.stdin.buffer.readline(8193)
                    if not line:
                        break
                    if len(line) > 8192:
                        continue
                    inbox.put(loads(line))
            finally:
                stopped.set()

        threading.Thread(target=input_loop, daemon=True).start()
        bridge = Bridge(accounts)
        watch = engine = None
        active = None
        last_output = None
        candidate = None
        next_capture = 0
        local = None
        next_attempt = 0
        retry_delay = 1
        error = ""
        try:
            while not stopped.is_set():
                try:
                    rows = accounts.load()["ships"]  # Atomic snapshots; no lock held on the network.
                    while not inbox.empty():
                        request = inbox.get_nowait()
                        if not isinstance(request, dict):
                            continue
                        action = request.get("action")
                        if action == "pause":
                            state.update(paused=True, pending=None, attempted=False)
                        elif action == "resume":
                            state["paused"] = False
                            state["notification"] = notification(disk.root)["id"]
                            if engine:
                                engine.first = True
                            next_attempt = 0
                        elif action == "hub":
                            row = next((r for r in rows if r["id"] == request.get("id")), None)
                            if row:
                                state.update(hub={k: row[k] for k in ("id", "url", "ship")}, pending=None,
                                             observed=None, applying=None, baseline="", attempted=False, paused=False)
                        elif action == "retry":
                            next_attempt = 0
                            if engine:
                                engine.first = True
                        elif action == "changed":
                            next_capture = 0
                        disk.save(state)
                    if state["hub"] is None and rows:
                        state["hub"] = {k: rows[0][k] for k in ("id", "url", "ship")}
                        disk.save(state)
                    hub = next((r for r in rows if state["hub"] == {k: r[k] for k in ("id", "url", "ship")}), None)
                    if hub is None and state["hub"]:
                        replacement = next((r for r in rows if all(r[k] == state["hub"][k] for k in ("url", "ship"))), None)
                        if replacement:
                            state.update(hub={k: replacement[k] for k in ("id", "url", "ship")}, pending=None,
                                         observed=None, applying=None, baseline="", attempted=False)
                            disk.save(state)
                            hub = replacement
                    wanted = state["hub"] if hub and hub["automatic"] and not hub["authenticationRequired"] and not state["paused"] else None
                    if wanted != active:
                        if wanted is None:
                            state.update(pending=None, attempted=False)
                            disk.save(state)
                        if watch:
                            watch.stop.set()
                        watch = engine = None
                        bridge.target = None
                        active = None
                        if wanted:
                            session = Keyring().lookup(hub["url"], hub["ship"])
                            watch = Watch(hub["url"], session)
                            engine = Coordinator(disk, desktop, Remote(Eyre(hub["url"], session)))
                            state = engine.state
                            active = copy.deepcopy(wanted)
                            next_capture = next_attempt = 0
                            candidate = local = None
                    now = time.monotonic()
                    intentional = False
                    if engine and now >= next_capture:
                        try:
                            local = desktop.capture(state["deviceId"])
                        except Failure:
                            if not state["applying"]:
                                raise
                            # A shell restart can interrupt the temporary working
                            # theme before its display name has been restored.
                            local = state["applying"]
                        stamp = digest(local)
                        notice = notification(disk.root)
                        changed = bool(state["baseline"] and stamp != state["baseline"] and not state["applying"])
                        new_notice = notice["id"] != state["notification"]
                        intentional = changed and (new_notice or (candidate == stamp and not engine.first))
                        requested = local
                        if new_notice and "font" in notice and state["baseline"]:
                            # font-set runs after Omarchy restarts the shell. Keep
                            # its explicit family even if startup already pulled
                            # the old hub value before the hook reached us.
                            requested = dict(local, font=notice["font"])
                            intentional = True
                        if intentional:
                            engine.intent(requested)
                            next_attempt = 0
                        candidate = stamp
                        if state["notification"] != notice["id"]:
                            state["notification"] = notice["id"]
                            engine.save()
                        next_capture = now + 2
                    if engine and local and watch.ready.is_set() and (watch.changed.is_set() or now >= next_attempt):
                        watch.changed.clear()
                        profile = engine.reconcile(local)
                        local = desktop.capture(state["deviceId"])
                        candidate = digest(local)
                        bridge.set(profile, copy.deepcopy(active))
                        next_attempt = now + 30
                        retry_delay = 1
                        error = ""
                    if watch and watch.error:
                        error = watch.error.message
                    status = ("Paused" if state["paused"] else "Add a ship to start desktop sync" if not state["hub"]
                              else "Hub paused or disconnected" if not active else "Reconnecting" if not watch.ready.is_set()
                              else "Syncing" if state["pending"] or state["applying"] else "Following shared appearance")
                except Exception as exception:
                    error = exception.message if isinstance(exception, Failure) else "Desktop sync could not complete the operation; retrying."
                    status = "Needs attention"
                    next_attempt = time.monotonic() + retry_delay
                    next_capture = time.monotonic() + min(retry_delay, 5)
                    retry_delay = min(retry_delay * 2, 60)
                output = dict(status=status, error=error, hub=state["hub"], paused=state["paused"],
                              theme=(state["observed"] or {}).get("theme", ""), bridgeErrors=dict(bridge.errors), bridgeBusy=bridge.busy)
                raw = dumps(output)
                if raw != last_output:
                    print(raw, flush=True)
                    last_output = raw
                stopped.wait(0.25)
        finally:
            bridge.stop.set()
            if watch:
                watch.stop.set()


def main():
    try:
        if sys.argv[1:] in (["notify"], ["notify", "theme"]):
            notify(theme=sys.argv[1:] == ["notify", "theme"])
        elif len(sys.argv) == 4 and sys.argv[1:3] == ["notify", "font"]:
            notify(font=sys.argv[3])
        elif sys.argv[1:] == ["serve"]:
            serve()
        else:
            raise Failure("input", "Specify notify or serve.")
        return 0
    except Exception as exception:
        error = exception.message if isinstance(exception, Failure) else "Desktop sync could not start."
        print(dumps({"status": "Needs attention", "error": error, "hub": None, "paused": False, "theme": "", "bridgeErrors": {}}), flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
