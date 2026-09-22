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
    from .support import Failure, LIMIT, PLUGIN_ID, dumps, loads, malformed, origin
except ImportError:
    from support import Failure, LIMIT, PLUGIN_ID, dumps, loads, malformed, origin


class Eyre:
    def __init__(self, url, session=None, timeout=5, ack_timeout=8):
        self.url = origin(url)
        self.session = session
        self.timeout, self.ack_timeout = timeout, ack_timeout
        if session is not None and session["url"] != self.url:
            raise Failure("authentication", "The saved session does not match this origin.")

    @contextlib.contextmanager
    def request(self, method, path, body=None, content_type="application/json", stream=False):
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

    def scry(self):
        with self.request("GET", "/~/scry/settings/desk/talon.json") as response:
            return loads(self.read(response))

    def logout(self):
        # No all/sid/host or redirect parameters: revoke only our request session.
        with self.request("POST", "/~/logout", b"", "application/x-www-form-urlencoded"):
            pass

    def put(self, path, messages):
        body = dumps(messages).encode()
        if len(body) > LIMIT:
            raise Failure("size", "The settings update exceeded the safe size limit.")
        with self.request("PUT", path, body) as response:
            self.read(response)

    def poke(self, entry, value):
        path = "/~/channel/" + uuid.uuid4().hex
        next_id = 2
        try:
            self.put(path, [dict(id=1, action="poke", ship=self.session["ship"].removeprefix("~"),
                                 app="settings", mark="settings-event", json={"put-entry": {
                                     "desk": "talon", "bucket-key": "ui-prefs", "entry-key": entry,
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
                                        raise Failure("nack", "The settings agent rejected the update.")
                                    if event.get("ok") != "ok":
                                        raise Failure("protocol", "The settings agent returned an unrecognized acknowledgement.")
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
        finally:
            with contextlib.suppress(Failure):
                self.put(path, [dict(id=next_id, action="delete")])


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


def publish(eyre, palette):
    body = eyre.scry()
    desired = merge(body, palette)
    current = entries(body)
    for key in ("themes", "accent"):
        if key == "accent":
            # Refresh the separate entry immediately before writing it.
            current = entries(eyre.scry())
            desired[key] = dict(current[key], enabled=False)
        # Re-send even matching entries: a client may have missed the earlier
        # fact while bootstrapping. Passive deduplication belongs in Helper.sync,
        # not here, where Publish Now must actually notify current subscribers.
        unchanged = current[key] == desired[key]
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
