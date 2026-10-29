"""Origin-bound Eyre transport and conservative Talon settings merging."""

import contextlib
import copy
import http.client
from http.cookies import SimpleCookie, CookieError
import re
import ssl
import time
from urllib.parse import urlencode, urlsplit
import uuid

try:
    from .support import Failure, LIMIT, PLUGIN_ID, dumps, loads, malformed, origin, check_session_expiry
    from .transport_journal import TransportJournal, intent, session_ref
except ImportError:
    from support import Failure, LIMIT, PLUGIN_ID, dumps, loads, malformed, origin, check_session_expiry
    from transport_journal import TransportJournal, intent, session_ref


class Eyre:
    def __init__(self, url, session=None, timeout=5, ack_timeout=8, journal=None):
        self.url = origin(url)
        self.session = session
        self.timeout, self.ack_timeout = timeout, ack_timeout
        self.pending_watch_channels = set()
        self.journal = journal if journal is not None else TransportJournal()
        if session is not None and session["url"] != self.url:
            raise Failure("authentication", "The saved session does not match this origin.")

    @contextlib.contextmanager
    def request(self, method, path, body=None, content_type="application/json", stream=False, cleanup=False):
        url = urlsplit(self.url)
        cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
        kwargs = {"context": ssl.create_default_context()} if url.scheme == "https" else {}
        # Direct connections avoid proxy environment variables and automatic redirects.
        conn = cls(url.hostname, url.port, timeout=self.timeout, **kwargs)
        headers = {"Accept": "text/event-stream" if stream else "application/json",
                   "Accept-Encoding": "identity", "Cache-Control": "no-cache"}
        if body is not None:
            headers["Content-Type"] = content_type
        if self.session:
            if not cleanup:check_session_expiry(self.session)
            headers["Cookie"] = self.session["cookieName"] + "=" + self.session["cookieValue"]
        response = None
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            logout = method == "POST" and path == "/~/logout" and body == b""
            logout_ok = logout and response.status in (303, 401)
            if 300 <= response.status < 400 and not logout_ok:
                raise Failure("redirect", "The ship redirected the request. Use its final origin; redirects are never followed.")
            if not logout_ok and (response.status in (401, 403) or (path == "/~/login" and response.status == 400)):
                raise Failure("authentication", "Authentication was rejected. Disconnect and sign in again.")
            if not logout_ok and not 200 <= response.status < 300:
                raise Failure("http", "The ship could not complete the request.", response.status >= 500 or response.status == 429)
            yield response
        except ssl.SSLError:
            raise Failure("tls", "The HTTPS connection could not be verified securely.") from None
        except (OSError, http.client.HTTPException):
            raise Failure("network", "The ship connection failed or timed out.", True) from None
        finally:
            if response is not None:
                response.close()
            conn.close()

    def read(self, response):
        deadline = time.monotonic() + self.timeout
        data = bytearray()
        while True:
            if time.monotonic() >= deadline:
                raise Failure("network", "The ship response timed out.", True)
            chunk = response.read1(min(8192, LIMIT + 1 - len(data)))
            if not chunk:
                return bytes(data)
            data.extend(chunk)
            if len(data) > LIMIT:
                raise Failure("size", "The ship response exceeded the safe size limit.")

    def login(self, code):
        if not isinstance(code, str) or not 1 <= len(code) <= 512:
            raise Failure("input", "Enter a valid access code.")
        code = code.strip().removeprefix("+")
        if not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", code):
            raise Failure("input", "Enter a valid access code.")
        # No redirect form field: Eyre returns 200, not its browser-only 303.
        with self.request("POST", "/~/login", urlencode({"password": code}).encode(),
                          "application/x-www-form-urlencoded") as response:
            cookies = []
            try:
                for header in response.headers.get_all("Set-Cookie", []):
                    parsed = SimpleCookie()
                    parsed.load(header)
                    cookies.extend(item for key, item in parsed.items() if key.startswith("urbauth-~"))
            except CookieError:
                raise Failure("authentication", "The ship did not return a valid session.") from None
            if len(cookies) != 1:
                raise Failure("authentication", "The ship did not return a valid session.")
            cookie = cookies[0]
            ship = cookie.key.removeprefix("urbauth-")
            if (not re.fullmatch(r"~[a-z]+(?:-+[a-z]+)*", ship) or len(ship) > 128
                    or not re.fullmatch(r"[A-Za-z0-9._~-]{1,4096}", cookie.value)):
                raise Failure("authentication", "The ship did not return a valid session.")
            host = urlsplit(self.url).hostname
            if ((cookie["domain"] and cookie["domain"].lstrip(".").lower() != host)
                    or cookie["path"] not in ("", "/") or cookie["max-age"] == "0"):
                raise Failure("authentication", "The ship returned a session for a different scope.")
            self.session = dict(url=self.url, ship=ship, cookieName=cookie.key, cookieValue=cookie.value)
        return self.session

    def scry(self, desk="talon"):
        with self.journal.admitted(self.session):
            return self._scry(desk)

    def _scry(self, desk):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", desk):
            raise malformed()
        saved = self.journal.status()
        candidates = [] if saved is None else [row for row in saved["channels"]
            if row["origin"] == self.url and row["session_ref"] == session_ref(self.session)
            and row["kind"] == "poke" and row["intent"]["desk"] == desk
            and row["phase"] == "closed" and row["outcome"] == "uncertain"]
        with self.request("GET", "/~/scry/settings/desk/" + desk + ".json") as response:
            body = loads(self.read(response))
        settings = body.get("desk", body) if isinstance(body, dict) else None
        if candidates and isinstance(settings, dict):
            with self.journal.operation() as lease:
                for row in candidates:
                    wanted = row["intent"]
                    bucket = settings.get(wanted["bucket"])
                    if not isinstance(bucket, dict) or wanted["entry"] not in bucket:
                        continue
                    value = bucket[wanted["entry"]]
                    try:
                        if isinstance(value, str):
                            value = loads(value)
                        observed = intent(desk, wanted["bucket"], wanted["entry"], value)
                        if observed == wanted:
                            self.journal.observe(lease, row["id"], self.session, observed)
                    except Failure:
                        # An active original writer, malformed entry or changed
                        # cohort cannot resolve uncertainty from this read.
                        pass
        return body

    def logout(self):
        # No all/sid/host or redirect parameters: revoke only our request session.
        with self.request("POST", "/~/logout", b"", "application/x-www-form-urlencoded", cleanup=True) as response:
            return response.status

    @contextlib.contextmanager
    def disconnecting(self, timeout=35):
        with self.journal.retiring(self.url, self.session, timeout):
            with self.journal.operation() as lease:
                saved = self.journal.status()
                pending = [r for r in saved["channels"] if r["origin"] == self.url and r["phase"] != "closed"]
                if pending and self.session is None:
                    raise Failure("transport-state", "Channel cleanup needs the saved credential. The account was kept; unlock Secret Service and retry Disconnect.")
                session_closed = None
                for row in pending:
                    # The broker may already have revoked this session. Eyre
                    # closes its channels with it, so authenticated DELETE then
                    # fails. Confirm session-only logout, without following its
                    # redirect; never treat network failure as confirmation.
                    if session_closed is None:
                        try:
                            self.cleanup_channel(lease, row)
                            continue
                        except Failure as error:
                            if error.code != "authentication":
                                raise
                            session_closed = self.logout()
                            if session_closed not in (303, 401):
                                raise Failure("protocol", "Session closure was not confirmed; retry Disconnect.")
                    closing = self.journal.prepare_cleanup(lease, row["id"], self.session)
                    self.journal.closed_after_logout(lease, row["id"], closing["cleanup_request"], session_closed)
                    self.pending_watch_channels.discard("/~/channel/" + row["channel"])
            yield

    def put(self, path, messages):
        body = dumps(messages).encode()
        if len(body) > LIMIT:
            raise Failure("size", "The settings update exceeded the safe size limit.")
        deleting = all(message.get("action") == "delete" for message in messages)
        with self.request("PUT", path, body, cleanup=deleting) as response:
            returned = self.read(response)
            if any(message.get("action") == "delete" for message in messages):
                if response.status not in (200, 204) or returned:
                    raise Failure("protocol", "Channel deletion was not confirmed exactly.")
            return response.status

    def cleanup_channel(self, lease, row):
        closing = self.journal.prepare_cleanup(lease, row["id"], self.session)
        if closing["origin"] != self.url:
            raise Failure("authentication", "Cleanup belongs to a different ship origin.")
        if closing["phase"] != "closed":
            status = self.put("/~/channel/" + closing["channel"], [{"action": "delete"}])
            closing = self.journal.deleted(lease, row["id"], closing["cleanup_request"], status)
        self.pending_watch_channels.discard("/~/channel/" + row["channel"])
        return closing

    @contextlib.contextmanager
    def channel(self, kind, write_intent=None):
        # Independent operations may coexist; only abandoned work is recovered.
        # The lease is released after every local request/stream context exits.
        with self.journal.admitted(self.session), self.journal.operation() as lease:
            for previous in self.journal.pending(self.session):
                self.cleanup_channel(lease, previous)
            row = self.journal.begin(lease, self.session, kind, write_intent)
            path = "/~/channel/" + row["channel"]
            if kind == "watch":
                self.pending_watch_channels.add(path)
            try:
                yield lease, row, path
            finally:
                with contextlib.suppress(Failure, OSError):
                    self.cleanup_channel(lease, row)

    def watch(self, desk, stop, changed, ready):
        """One live subscription. Caller reconnects and re-scries after every gap.

        Facts are invalidations, never snapshots to replay over a newer scry.
        Only a subscribe ACK sets ready; %settings sends no initial snapshot.
        """
        if not re.fullmatch(r"[a-z][a-z0-9-]*", desk):
            raise malformed()
        next_id = 2
        try:
            with self.channel("watch") as (lease, row, path):
                self.put(path, [dict(id=1, action="subscribe", ship=self.session["ship"].removeprefix("~"),
                                     app="settings", path="/desk/" + desk)])
                with self.request("GET", path, stream=True) as response:
                    if response.headers.get_content_type() != "text/event-stream":
                        raise Failure("protocol", "The ship did not return an Eyre event stream.")
                    buffer, data, event_id = b"", [], None
                    while not stop.is_set():
                        self.journal.assert_admitted(self.session)
                        chunk = response.read1(4096)
                        if not chunk:
                            raise Failure("network", "The live appearance connection closed.", True)
                        buffer += chunk
                        if len(buffer) + sum(map(len, data)) > LIMIT:
                            raise Failure("size", "The ship event exceeded the safe size limit.")
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            line = line.rstrip(b"\r")
                            if not line:
                                if data:
                                    event = loads(b"\n".join(data))
                                    if event_id is None or not isinstance(event, dict):
                                        raise Failure("protocol", "Invalid live appearance event.")
                                    self.put(path, [dict(id=next_id, action="ack", **{"event-id": event_id})])
                                    next_id += 1
                                    if event.get("id") == 1:
                                        if event.get("err") is not None or event.get("response") == "quit":
                                            raise Failure("network", "The appearance subscription must reconnect.", True)
                                        if event.get("response") == "subscribe":
                                            if event.get("ok") != "ok":
                                                raise Failure("protocol", "The ship returned an unrecognized subscription acknowledgement.")
                                            ready.set()
                                        changed.set()
                                data, event_id = [], None
                            elif line.startswith(b"data:"):
                                data.append(line[5:].removeprefix(b" "))
                            elif line.startswith(b"id:"):
                                raw = line[3:].strip()
                                if not re.fullmatch(rb"[0-9]{1,18}", raw):
                                    raise Failure("protocol", "Invalid live appearance event ID.")
                                event_id = int(raw)
        finally:
            ready.clear()

    def close_watches(self, deadline):
        """Recover exact released/dead operations, never delete a live peer's work."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        previous_timeout = self.timeout
        try:
            self.timeout = min(previous_timeout, remaining)
            with self.journal.operation() as lease:
                for row in self.journal.pending(self.session):
                    if time.monotonic() >= deadline:
                        return False
                    self.cleanup_channel(lease, row)
                saved = self.journal.status()
                return not self.pending_watch_channels and not any(row["phase"] != "closed"
                    and row["origin"] == self.url for row in saved["channels"])
        except (Failure, OSError):
            return False
        finally:
            self.timeout = previous_timeout

    def poke(self, entry, value, desk="talon", bucket="ui-prefs"):
        next_id = 2
        with self.channel("poke", intent(desk, bucket, entry, value)) as (lease, row, path):
            self.put(path, [dict(id=1, action="poke", ship=self.session["ship"].removeprefix("~"),
                                 app="settings", mark="settings-event", json={"put-entry": {
                                      "desk": desk, "bucket-key": bucket, "entry-key": entry,
                                     "value": dumps(value)}})])
            # Eyre queues the poke response until this GET attaches. Parse every
            # buffered frame; HTTP acceptance alone is not Gall delivery.
            with self.request("GET", path, stream=True) as response:
                if response.headers.get_content_type() != "text/event-stream":
                    raise Failure("protocol", "The ship did not return an Eyre event stream.")
                deadline = time.monotonic() + self.ack_timeout
                buffer, data, event_id, total = b"", [], None, 0
                while time.monotonic() < deadline:
                    chunk = response.read1(4096)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > LIMIT:
                        raise Failure("size", "The event stream exceeded the safe size limit.")
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        line = line.rstrip(b"\r")
                        if not line:
                            if data:
                                event = loads(b"\n".join(data))
                                if event_id is None:
                                    raise Failure("protocol", "An Eyre event did not have a valid frame ID.")
                                self.put(path, [dict(id=next_id, action="ack", **{"event-id": event_id})])
                                next_id += 1
                                if (isinstance(event, dict) and type(event.get("id")) is int
                                        and event["id"] == 1 and event.get("response") == "poke"):
                                    if event.get("err") is not None:
                                        self.journal.receipt(lease, row["id"], "rejected")
                                        raise Failure("nack", "The settings agent rejected the update.")
                                    if event.get("ok") != "ok":
                                        raise Failure("protocol", "The settings agent returned an unrecognized acknowledgement.")
                                    self.journal.receipt(lease, row["id"], "acknowledged")
                                    return
                            data, event_id = [], None
                        elif line.startswith(b"data:"):
                            data.append(line[5:].removeprefix(b" "))
                        elif line.startswith(b"id:"):
                            raw = line[3:].strip()
                            if not re.fullmatch(rb"[0-9]{1,18}", raw):
                                raise Failure("protocol", "An Eyre event did not have a valid frame ID.")
                            event_id = int(raw)
                raise Failure("ack-timeout", "No matching settings acknowledgement arrived; verification is required.", True)


def entries(body):
    if not isinstance(body, dict):
        raise malformed()
    desk = body.get("desk", body)
    if not isinstance(desk, dict) or any(not isinstance(bucket, dict) for bucket in desk.values()):
        raise malformed()
    bucket = desk.get("ui-prefs", {})
    if not isinstance(bucket, dict):
        raise malformed()
    result = {}
    for key in ("themes", "accent"):
        if key not in bucket:
            result[key] = {}
            continue
        value = bucket[key]
        if isinstance(value, str):
            value = loads(value)
        if not isinstance(value, dict):
            raise malformed()
        result[key] = value
    themes, accent = result["themes"], result["accent"]
    if (not isinstance(themes.get("themes", []), list)
            or (themes.get("activeId") is not None and not isinstance(themes["activeId"], str))):
        raise malformed()
    seen = set()
    for theme in themes.get("themes", []):
        if not isinstance(theme, dict) or not isinstance(theme.get("id"), str) or not theme["id"] or theme["id"] in seen:
            raise malformed()
        seen.add(theme["id"])
        if (not isinstance(theme.get("name"), str) or not theme["name"].strip()
                or type(theme.get("dark")) is not bool):
            raise malformed()
        for color in ("primary", "secondary", "tertiary", "background", "surface"):
            if not isinstance(theme.get(color), str) or not re.fullmatch(r"#?[0-9A-Fa-f]{6}", theme[color].strip()):
                raise malformed()
        for color in ("text", "muted", "raised", "error", "selection", "link"):
            value = theme.get(color)
            if value is not None and value != "" and (not isinstance(value, str) or not re.fullmatch(r"#?[0-9A-Fa-f]{6}", value.strip())):
                raise malformed()
    if (accent.get("enabled") is not None and type(accent["enabled"]) is not bool
            or "mode" in accent and not isinstance(accent["mode"], str)
            or accent.get("customHex") is not None and not isinstance(accent["customHex"], str)):
        raise malformed()
    return result


def merge(body, palette):
    result = copy.deepcopy(entries(body))
    themes = result["themes"].setdefault("themes", [])
    for theme in themes:
        if theme["id"] == PLUGIN_ID:
            theme.update(palette)
            break
    else:
        themes.append(copy.deepcopy(palette))
    result["themes"]["activeId"] = PLUGIN_ID
    result["accent"]["enabled"] = False
    return result


def publish(eyre, palette, *, renotify=True):
    body = eyre.scry()
    desired = merge(body, palette)
    current = entries(body)
    for key in ("themes", "accent"):
        if key == "accent":
            # Refresh the separate entry immediately before writing it.
            current = entries(eyre.scry())
            desired[key] = dict(current[key], enabled=False)
        # A new explicit Publish Now re-notifies subscribers. Passive retries
        # reconcile matching state by authenticated read without another poke,
        # including after the read resolves a retained uncertain intent.
        unchanged = current[key] == desired[key]
        if unchanged and not renotify:
            continue
        try:
            eyre.poke(key, desired[key])
        except Failure as error:
            if not error.retryable or error.code == "timeout" or unchanged:
                raise
            # Lost acknowledgements and PUT timeouts have ambiguous outcomes.
            # A changed readback resolves them, never the timeout alone. Existing
            # matching state cannot prove a re-send reached the agent.
            if entries(eyre.scry())[key] != desired[key]:
                raise error
        if entries(eyre.scry())[key] != desired[key]:
            raise Failure("verification", "The ship did not retain the full update; publication remains pending.", True)
    if entries(eyre.scry()) != desired:
        raise Failure("verification", "Ship settings changed during publication; publication remains pending.", True)
    # %settings has no compare-and-swap. Read/merge/write can still race with
    # another client, so this plugin must have only one automatic publisher.
