"""JSON stdin/stdout helper. Never print exceptions or remote/command output.

Invoke python3 -B client/main.py ACTION and supply one JSON object on stdin:
sync: {"force":false,"expectedAccount":{"url":"https://ship.example","ship":"~zod"}}
set-auto: {"enabled":true,"expectedAccount":{"url":"https://ship.example","ship":"~zod"}}
disconnect: {"expectedAccount":{"url":"https://ship.example","ship":"~zod"}}
Copy expectedAccount's exact url and ship from the last authoritative state.
"""

import contextlib
import copy
from datetime import datetime, timezone
import signal
import sys

try:
    from .eyre import Eyre, entries, publish
    from .support import (Failure, Keyring, StateStore, account, empty_record,
                          fingerprint, loads, dumps, origin, resolve_palette)
except ImportError:
    from eyre import Eyre, entries, publish
    from support import (Failure, Keyring, StateStore, account, empty_record,
                         fingerprint, loads, dumps, origin, resolve_palette)


class Helper:
    def __init__(self, store=None, keyring=None, palette=resolve_palette, transport=Eyre):
        self.store = store or StateStore()
        self.keyring = keyring or Keyring()
        self.palette = palette
        self.transport = transport

    def dispatch(self, action, value, record):
        state = record["state"]
        allowed = {"status": set(), "preview": set(), "login": {"url", "code"},
                   "set-auto": {"enabled", "expectedAccount"},
                   "sync": {"force", "expectedAccount"}, "disconnect": {"expectedAccount"}}
        if action in ("sync", "set-auto", "disconnect"):
            expected = value.get("expectedAccount") if isinstance(value, dict) else None
            if expected != {"url": state["url"], "ship": state["ship"]}:
                raise Failure("account-changed", "The account changed or was not specified. Refresh status before trying again.")
        if action not in allowed or not isinstance(value, dict) or set(value) - allowed[action]:
            raise Failure("input", "The helper request is invalid.")
        if action == "status":
            return None
        if action == "preview":
            return self.palette()
        if action == "login":
            if state["connected"]:
                raise Failure("connected", "Disconnect the current account before signing in again.")
            url = origin(value.get("url"))
            session = self.transport(url).login(value.get("code"))
            self.keyring.store(url, session)
            new = empty_record()
            new["state"].update(connected=True, ship=session["ship"], url=url)
            new["account"] = account(url, session["ship"])
            try:
                self.store.save(new)
            except Exception:
                with contextlib.suppress(Exception):
                    # replace may have committed before a directory fsync failed.
                    # Do not remove credentials for a possibly committed account.
                    if self.store.load()["account"] != new["account"]:
                        self.keyring.clear(url)
                raise
            record.clear()
            record.update(new)
            return None
        if action == "disconnect":
            # Stop intent first, even if unlocking the keyring for deletion fails.
            state.update(automatic=False, pending=False)
            self.store.save(record)
            if state["connected"]:
                self.keyring.clear(state["url"])
            new = empty_record()
            self.store.save(new)
            record.clear()
            record.update(new)
            return None
        if action == "set-auto":
            if type(value.get("enabled")) is not bool:
                raise Failure("input", "Automatic publishing requires a boolean setting.")
            if value["enabled"] and not state["connected"]:
                raise Failure("authentication", "Sign in before enabling automatic publishing.")
            if value["enabled"] and not state["automatic"]:
                # Enabling is explicit consent to publish now, unlike a passive
                # startup reconciliation of an already-followed palette.
                state["pending"] = True
            state["automatic"] = value["enabled"]
            if not state["automatic"]:
                state["pending"] = False
            self.store.save(record)
            return None
        if action == "sync":
            force = value.get("force", False)
            if type(force) is not bool:
                raise Failure("input", "The force option must be a boolean.")
            if not force and not state["automatic"]:
                return None
            if not state["connected"] or state["authenticationRequired"]:
                raise Failure("authentication", "Disconnect and sign in before publishing.")
            was_pending = state["pending"]
            state["pending"] = True
            self.store.save(record)
            palette = self.palette()
            digest = fingerprint(palette)
            if not force and not was_pending and record["fingerprint"] == digest:
                # This is observation, not publication intent. A failed read-only
                # scry must not make the next poll overwrite a manual ship edit.
                state["pending"] = False
                self.store.save(record)
                session = self.keyring.lookup(state["url"], state["ship"])
                entries(self.transport(state["url"], session).scry())
                if state["lastError"]:
                    state["lastError"] = ""
                    self.store.save(record)
                return palette
            session = self.keyring.lookup(state["url"], state["ship"])
            publish(self.transport(state["url"], session), palette)
            state.update(pending=False, authenticationRequired=False, lastError="",
                         lastPublished=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                         lastTheme=palette["name"])
            record["fingerprint"] = digest
            self.store.save(record)
            return palette
        raise Failure("input", "Unknown helper action.")

    def run(self, action, value):
        record, palette, error = None, None, None
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
                    # Never persist or report a dispatch record whose save failed.
                    # Only the record reloaded under this lock is authoritative.
                    record = None
                    record = self.store.load()
                    if (action in ("login", "disconnect", "sync", "set-auto")
                            and error.code not in ("account-changed", "input", "state")):
                        updated = copy.deepcopy(record)
                        updated["state"]["lastError"] = error.message
                        if error.code == "authentication" and updated["state"]["connected"]:
                            updated["state"]["authenticationRequired"] = True
                        try:
                            self.store.save(updated)
                        except Exception:
                            error = Failure("state", "Private local state could not be read or saved.")
                            record = None
                            record = self.store.load()
                        else:
                            record = updated
        except Exception as exception:
            record = None
            error = exception if isinstance(exception, Failure) else Failure("state", "Private local state could not be read or saved.")
        return dict(schemaVersion=1, ok=error is None, state=record["state"] if record is not None else None, palette=palette,
                    error=error.public() if error else None)


def main():
    def deadline(_signum, _frame):
        # Keep cleanup bounded too if the first deadline interrupted a request.
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
        response = dict(schemaVersion=1, ok=False, state=None, palette=None, error=error.public())
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    sys.stdout.write(dumps(response) + "\n")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
