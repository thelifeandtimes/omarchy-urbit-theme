"""Bounded JSON protocol v2. Secrets never enter argv or response output.

Capture expectedAccount's exact id, url, and ship from an authoritative row
when creating sync, set-auto, or disconnect intent.
"""

import contextlib
import copy
from datetime import datetime, timezone
import secrets
import signal
import sys

try:
    from .eyre import Eyre, entries, publish
    from .support import (Failure, Keyring, StateStore, empty_ship,
                          fingerprint, loads, dumps, origin, resolve_palette, validate_palette)
except ImportError:
    from eyre import Eyre, entries, publish
    from support import (Failure, Keyring, StateStore, empty_ship,
                         fingerprint, loads, dumps, origin, resolve_palette, validate_palette)


class Helper:
    def __init__(self, store=None, keyring=None, palette=resolve_palette, transport=Eyre):
        self.store = store or StateStore()
        self.keyring = keyring or Keyring()
        self.palette = palette
        self.transport = transport
        self.warning = None

    def save(self, record):
        try:
            self.store.save(record)
        except Exception:
            raise Failure("state", "Private local state could not be read or saved.") from None

    @staticmethod
    def selected(record, value):
        expected = value.get("expectedAccount") if isinstance(value, dict) else None
        return next((row for row in record["ships"]
                     if expected == {k: row[k] for k in ("id", "url", "ship")}), None)

    def cleanup(self, state):
        remote_failed = keyring_failed = False
        try:
            session = self.keyring.lookup(state["url"], state["ship"])
            self.transport(state["url"], session).logout()
        except Exception:
            remote_failed = True
        finally:
            try:
                self.keyring.clear(state["url"])
            except Exception:
                # secret-tool exit 1 can mean absent OR locked; do not claim deletion.
                keyring_failed = True
        if remote_failed or keyring_failed:
            message = "The ship was removed locally."
            if remote_failed:
                message += " Remote session logout could not be confirmed."
            if keyring_failed:
                message += " Keyring cleanup could not be confirmed; a saved session may remain."
            self.warning = Failure("cleanup" if remote_failed and keyring_failed else
                                   "logout-unconfirmed" if remote_failed else "keyring-cleanup", message)

    def dispatch(self, action, value, record):
        allowed = {"status": set(), "preview": set(), "login": {"url", "code"},
                   "set-auto": {"enabled", "expectedAccount"},
                   "sync": {"force", "expectedAccount", "palette"}, "disconnect": {"expectedAccount"}}
        state = None
        if action in ("sync", "set-auto", "disconnect"):
            state = self.selected(record, value)
            if state is None:
                raise Failure("account-changed", "The account changed or was not specified. Refresh status before trying again.")
        if action not in allowed or not isinstance(value, dict) or set(value) - allowed[action]:
            raise Failure("input", "The helper request is invalid.")
        if action == "status":
            return None
        if action == "preview":
            return validate_palette(self.palette())
        if action == "login":
            url = origin(value.get("url"))
            if any(row["url"] == url for row in record["ships"]):
                raise Failure("duplicate-origin", "This ship origin has already been added.")
            if len(record["ships"]) >= 64:
                raise Failure("ship-limit", "Remove a ship before adding another; the limit is 64 ships.")
            session = self.transport(url).login(value.get("code"))
            self.keyring.store(url, session)
            state = dict(empty_ship(), id=secrets.token_hex(32), url=url, ship=session["ship"],
                         automatic=True, pending=True, fingerprint="")
            record["ships"].append(state)
            try:
                self.save(record)
            except Failure:
                with contextlib.suppress(Exception):
                    # A directory fsync can fail after replace committed the row.
                    if not any(row["id"] == state["id"] for row in self.store.load()["ships"]):
                        self.cleanup(state)
                raise
            return None
        if action == "disconnect":
            record["ships"].remove(state)
            try:
                # Commit forgetting first. Cleanup never resurrects a removed row.
                self.save(record)
            except Failure:
                with contextlib.suppress(Exception):
                    if not any(row["id"] == state["id"] for row in self.store.load()["ships"]):
                        self.cleanup(state)
                raise
            self.cleanup(state)
            return None
        if action == "set-auto":
            if type(value.get("enabled")) is not bool:
                raise Failure("input", "Automatic publishing requires a boolean setting.")
            state.update(automatic=value["enabled"], pending=value["enabled"])
            self.save(record)
            return None
        if action == "sync":
            force = value.get("force", False)
            if type(force) is not bool:
                raise Failure("input", "The force option must be a boolean.")
            shared = validate_palette(value["palette"]) if "palette" in value else None
            if not force and not state["automatic"]:
                return None
            if state["authenticationRequired"]:
                raise Failure("authentication", "Remove and sign in before publishing.")
            was_pending = state["pending"]
            state["pending"] = True
            self.save(record)
            palette = shared if shared is not None else validate_palette(self.palette())
            digest = fingerprint(palette)
            if not force and not was_pending and state["fingerprint"] == digest:
                # Passive checks must not turn a failed scry into publication intent.
                state["pending"] = False
                self.save(record)
                session = self.keyring.lookup(state["url"], state["ship"])
                entries(self.transport(state["url"], session).scry())
                if state["lastError"]:
                    state["lastError"] = ""
                    self.save(record)
                return palette
            session = self.keyring.lookup(state["url"], state["ship"])
            publish(self.transport(state["url"], session), palette)
            state.update(pending=False, authenticationRequired=False, lastError="",
                         lastPublished=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                         lastTheme=palette["name"], fingerprint=digest)
            self.save(record)
            return palette
        raise Failure("input", "Unknown helper action.")

    def run(self, action, value):
        record, palette, error = None, None, None
        self.warning = None
        try:
            with self.store.locked():
                record = self.store.load()
                try:
                    palette = self.dispatch(action, value, record)
                except Exception as exception:
                    if isinstance(exception, Failure):
                        error = exception
                    elif isinstance(exception, OSError):
                        error = Failure("state", "Private local state could not be read or saved.")
                    else:
                        error = Failure("internal", "The helper could not safely complete the operation.")
                    # Never report an unsaved working copy, even after partial commit.
                    record = None
                    record = self.store.load()
                    if action == "sync" and error.code not in ("account-changed", "input", "state"):
                        updated = copy.deepcopy(record)
                        row = self.selected(updated, value)
                        if row is not None:
                            row["lastError"] = error.message
                            if error.code == "authentication":
                                row.update(authenticationRequired=True, automatic=False, pending=False)
                            try:
                                self.save(updated)
                            except Failure as failure:
                                error = failure
                                record = None
                                record = self.store.load()
                            else:
                                record = updated
        except Exception as exception:
            record = None
            error = exception if isinstance(exception, Failure) else Failure("state", "Private local state could not be read or saved.")
        state = None if record is None else {"ships": [
            {k: v for k, v in row.items() if k != "fingerprint"} for row in record["ships"]]}
        return dict(schemaVersion=2, ok=error is None, state=state, palette=palette,
                    error=error.public() if error else None, warning=self.warning.public() if self.warning else None)


def main():
    def deadline(_signum, _frame):
        signal.setitimer(signal.ITIMER_REAL, 5)
        raise Failure("timeout", "The helper operation timed out; publication was not confirmed.", True)

    signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 45)
    try:
        if len(sys.argv) != 2:
            raise Failure("input", "Specify one helper action.")
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise Failure("input", "The helper request exceeded the safe size limit.")
        value = loads(raw)
        raw = b""
        response = Helper().run(sys.argv[1], value)
    except Exception as exception:
        error = exception if isinstance(exception, Failure) else Failure("internal", "The helper could not safely complete the operation.")
        response = dict(schemaVersion=2, ok=False, state=None, palette=None, error=error.public(), warning=None)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    sys.stdout.write(dumps(response) + "\n")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
