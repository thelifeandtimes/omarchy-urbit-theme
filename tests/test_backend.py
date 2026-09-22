import contextlib
import copy
import http.server
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from client.eyre import Eyre, entries, merge, publish
from client.main import Helper
from client.support import (Failure, Keyring, PLUGIN_ID, StateStore, account, command,
                            dumps, empty_record, fingerprint, loads, origin, resolve_palette)


PALETTE = dict(id=PLUGIN_ID, name="Test Theme", dark=True, primary="#ABCDEF",
               secondary="#334455", tertiary="#667788", background="#111111", surface="#222222")
SECRET = "0v.private-session-token"
CODE = "sampel-ticlyt-migfun-falmel"


class MemoryKeyring:
    def __init__(self):
        self.sessions = {}
        self.calls = []
        self.fail = False

    def store(self, url, session):
        self.calls.append("store")
        if self.fail:
            raise Failure("keyring", "Secret Service is unavailable.", True)
        self.sessions[url] = copy.deepcopy(session)

    def lookup(self, url, ship):
        self.calls.append("lookup")
        if self.fail:
            raise Failure("keyring", "Secret Service is unavailable.", True)
        session = self.sessions[url]
        assert session["ship"] == ship
        return session

    def clear(self, url):
        self.calls.append("clear")
        if self.fail:
            raise Failure("keyring", "Secret Service is unavailable.", True)
        self.sessions.pop(url, None)


class FakeEyre(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, ship="~zod"):
        super().__init__(("127.0.0.1", 0), Handler)
        self.ship = ship
        self.url = "http://127.0.0.1:" + str(self.server_port)
        self.body = {"desk": {"ui-prefs": {}, "other-bucket": {"opaque": "untouched"}}}
        self.requests = []
        self.messages = []
        self.events = {}
        self.mode = "success"
        self.login_status = 200
        self.logout_status = 303
        self.sessions = {SECRET, "0v.other-client-session"}
        self.scry_status = 200
        self.scry_count = 0
        self.login_cookie = "urbauth-" + ship + "=" + SECRET + "; Path=/; HttpOnly"
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join()

    def session(self):
        return dict(url=self.url, ship=self.ship, cookieName="urbauth-" + self.ship, cookieValue=SECRET)

    def client(self, url=None, session=None):
        return Eyre(url or self.url, session or self.session(), timeout=0.15, ack_timeout=0.15)

    def transport(self, url, session=None):
        return Eyre(url, session, timeout=0.15, ack_timeout=0.15)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass

    def reply(self, status, body=b"", kind="application/json", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.close_connection = True
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.server.requests.append(("POST", self.path, raw, dict(self.headers)))
        if self.path == "/~/logout":
            if self.server.logout_status in (200, 303, 401):
                cookie = self.headers.get("Cookie", "").partition("=")[2]
                self.server.sessions.discard(cookie)
            self.reply(self.server.logout_status, headers={"Location": "/~/login"})
            return
        headers = {"Set-Cookie": self.server.login_cookie}
        if 300 <= self.server.login_status < 400:
            headers["Location"] = "/must-not-follow"
        self.reply(self.server.login_status, b"private login response", headers=headers)

    def do_GET(self):
        server = self.server
        server.requests.append(("GET", self.path, b"", dict(self.headers)))
        if self.path == "/~/scry/settings/desk/talon.json":
            server.scry_count += 1
            self.reply(server.scry_status, dumps(server.body).encode())
        elif self.path.startswith("/~/channel/"):
            if server.mode == "stall":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", "100")
                self.end_headers()
                time.sleep(0.3)
                self.close_connection = True
            else:
                self.reply(200, server.events.get(self.path, b""), "text/event-stream")
        else:
            self.reply(404)

    def do_PUT(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        server = self.server
        server.requests.append(("PUT", self.path, raw, dict(self.headers)))
        messages = json.loads(raw)
        assert isinstance(messages, list)
        for message in messages:
            server.messages.append(message)
            if message["action"] != "poke":
                continue
            put = message["json"]["put-entry"]
            assert put["desk"] == "talon" and put["bucket-key"] == "ui-prefs"
            assert isinstance(put["value"], str)
            key = put["entry-key"]
            nack = server.mode == "nack" or server.mode == "nack-accent" and key == "accent"
            if not nack and server.mode not in ("ignore", "timeout-no-write", "wrong-id"):
                server.body["desk"]["ui-prefs"][key] = put["value"]
            event = {"id": message["id"], "response": "poke"}
            if nack:
                event["err"] = SECRET + CODE
            else:
                event["ok"] = "ok"
            if server.mode == "wrong-id":
                event["id"] += 999
            data = b"id: 10\ndata: " + dumps(event).encode() + b"\n\n"
            if server.mode == "buffered":
                data = b': heartbeat\r\nid: 9\r\ndata: {"id":999,"response":"poke","ok":"ok"}\r\n\r\n' + data
            if server.mode in ("timeout", "timeout-no-write"):
                data = b": heartbeat\n\n"
            server.events[self.path] = data
        self.reply(204)


class PaletteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.current = self.home / ".local/state/omarchy/current"
        self.current.mkdir(parents=True)
        (self.current / "theme.name").write_text("My Theme\n")
        self.colors = "background\t#112233\nforeground\t#aAbBcC\nmode\tdark\n"

    def runner(self, args):
        self.assertEqual(args, ["omarchy", "theme", "color", "--file",
                                str(self.current / "theme/colors.toml"), "--all"])
        return 0, self.colors.encode()

    def test_mapping_and_canonical_hex(self):
        self.colors += "accent\t#aabbcc\nblue\t#123abc\ngreen\t#abc123\nlighter_background\t#223344\n"
        palette = resolve_palette(self.home, self.runner)
        self.assertEqual(set(palette), set(PALETTE))
        self.assertEqual(palette, dict(id=PLUGIN_ID, name="My Theme", dark=True, primary="#AABBCC",
                                      secondary="#123ABC", tertiary="#ABC123", background="#112233", surface="#223344"))

    def test_optional_fallbacks(self):
        palette = resolve_palette(self.home, self.runner)
        self.assertEqual(palette["primary"], "#AABBCC")
        self.assertEqual(palette["tertiary"], "#AABBCC")
        self.assertEqual(palette["surface"], "#112233")
        self.colors += "blue\t#abcdef\n"
        self.assertEqual(resolve_palette(self.home, self.runner)["primary"], "#ABCDEF")

    def test_light_mode(self):
        self.colors = self.colors.replace("dark", "light")
        self.assertFalse(resolve_palette(self.home, self.runner)["dark"])

    def test_invalid_palette_and_required_bases(self):
        original = self.colors
        for colors in ("mode\tdark\n", original.replace("#112233", "bad"),
                       original + "accent\tinvalid\n", original.replace("dark", "other"),
                       original + "mode\tdark\n", "invalid-output"):
            with self.subTest(colors=colors):
                self.colors = colors
                with self.assertRaises(Failure):
                    resolve_palette(self.home, self.runner)

    def test_missing_name_is_not_guessed(self):
        (self.current / "theme.name").unlink()
        with self.assertRaises(Failure):
            resolve_palette(self.home, self.runner)

    def test_theme_name_del_is_rejected_by_resolver(self):
        for name in ("My\x7fTheme", "\x7fTheme", "Theme\x7f"):
            with self.subTest(name=name):
                (self.current / "theme.name").write_text(name + "\n")
                with self.assertRaises(Failure) as caught:
                    resolve_palette(self.home, self.runner)
                self.assertEqual(caught.exception.code, "palette")

    def test_fingerprint_is_independent_of_key_order(self):
        self.assertEqual(fingerprint(PALETTE), fingerprint(dict(reversed(list(PALETTE.items())))))

    def test_theme_name_length_is_bounded_without_truncation(self):
        path = self.current / "theme.name"
        for name in ("a" * 256, "\u00e9" * 256):
            path.write_text(name + "\n", encoding="utf-8")
            self.assertEqual(resolve_palette(self.home, self.runner)["name"], name)
        for name in ("a" * 257, "\u00e9" * 257, "Name" + " " * 1100 + "trailing"):
            path.write_text(name, encoding="utf-8")
            with self.assertRaises(Failure):
                resolve_palette(self.home, self.runner)


class PolicyTests(unittest.TestCase):
    def test_allowed_origins(self):
        for value, expected in (("https://SHIP.example:443/", "https://ship.example"),
                                ("http://localhost:8080/", "http://localhost:8080"),
                                ("http://127.12.3.4", "http://127.12.3.4"),
                                ("http://[::ffff:127.0.0.1]", "http://[::ffff:127.0.0.1]"),
                                ("http://[::1]:1234", "http://[::1]:1234")):
            self.assertEqual(origin(value), expected)

    def test_unsafe_urls(self):
        values = (None, "ship.example", "//ship.example", "ftp://ship.example", "http://ship.example",
                  "http://localhost.evil", "http://127.1", "http://2130706433", "http://[::ffff:10.0.0.1]",
                  "http://0.0.0.0", "https://user:pass@ship.example", "https://ship.example/path",
                  "https://ship.example?", "https://ship.example#", "https://ship.example\\evil",
                  " https://ship.example", "https://ship.example\n", "https://ship.example:99999",
                  "https://ship.example:0", "https://ship.example:", "https://%65xample.com", "https://[::1%25lo]")
        for value in values:
            with self.subTest(value=value), self.assertRaises(Failure):
                origin(value)

    def test_https_uses_certificate_and_hostname_verification(self):
        with patch("client.eyre.http.client.HTTPSConnection") as connection:
            connection.return_value.getresponse.return_value.status = 200
            with Eyre("https://ship.example").request("GET", "/"):
                pass
            context = connection.call_args.kwargs["context"]
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(context.check_hostname)

    def test_tls_failure_is_sanitized(self):
        with patch("client.eyre.http.client.HTTPSConnection") as connection:
            connection.return_value.request.side_effect = ssl.SSLCertVerificationError(SECRET)
            with self.assertRaises(Failure) as caught:
                with Eyre("https://ship.example").request("GET", "/"):
                    pass
            self.assertNotIn(SECRET, str(caught.exception))

    def test_session_cannot_move_origins(self):
        with self.assertRaises(Failure):
            Eyre("https://other.example", {"url": "https://first.example"})

    def test_strict_json(self):
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', 'bad'):
            with self.assertRaises(Failure):
                loads(raw)


class MergeTests(unittest.TestCase):
    def test_preserves_fields_order_and_input(self):
        other = dict(PALETTE, id="other", future={"arbitrary": [1, None, True]})
        plugin = dict(PALETTE, name="Old", future="preserve me")
        body = {"desk": {"ui-prefs": {
            "themes": dumps({"themes": [other, plugin], "activeId": "other", "future": {"x": 3}}),
            "accent": dumps({"enabled": True, "mode": "Custom", "customHex": "#BEEF00", "future": 7}),
            "unrelated": "opaque"}, "other": {"keep": 1}}}
        before = copy.deepcopy(body)
        desired = merge(body, PALETTE)
        self.assertEqual(body, before)
        self.assertEqual(desired["themes"]["themes"][0], other)
        self.assertEqual(desired["themes"]["themes"][1], dict(PALETTE, future="preserve me"))
        self.assertEqual(desired["themes"]["future"], {"x": 3})
        self.assertEqual(desired["themes"]["activeId"], PLUGIN_ID)
        self.assertEqual(desired["accent"], {"enabled": False, "mode": "Custom", "customHex": "#BEEF00", "future": 7})

    def test_empty_desk_and_unwrapped_entries(self):
        self.assertEqual(merge({}, PALETTE)["themes"]["themes"], [PALETTE])
        self.assertEqual(merge({"ui-prefs": {"accent": {"mode": "Profile"}}}, PALETTE)["accent"],
                         {"mode": "Profile", "enabled": False})

    def test_existing_talon_hex_is_preserved_without_normalizing_it(self):
        theme = dict(PALETTE, id="other", primary=" abcdef ")
        merged = merge({"ui-prefs": {"themes": {"themes": [theme]}}}, PALETTE)
        self.assertEqual(merged["themes"]["themes"][0], theme)

    def test_rejects_malformed_existing_settings(self):
        bodies = [None, [], {"desk": None}, {"error": "failed"}, {"ui-prefs": None}, {"ui-prefs": {"themes": None}},
                  {"ui-prefs": {"themes": "not json"}}, {"ui-prefs": {"themes": {"themes": None}}},
                  {"ui-prefs": {"themes": {"themes": [dict(PALETTE, dark="false")]}}},
                  {"ui-prefs": {"themes": {"themes": [dict(PALETTE, primary="#FFAABBCC")]}}},
                  {"ui-prefs": {"themes": {"themes": [PALETTE, PALETTE]}}},
                  {"ui-prefs": {"accent": {"enabled": "false"}}},
                  {"ui-prefs": {"themes": {"activeId": 1}}}]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(Failure):
                merge(body, PALETTE)


class EyreTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeEyre()
        self.addCleanup(self.server.close)

    def test_login_form_preserves_dashes_and_omits_redirect(self):
        session = Eyre(self.server.url).login(" +" + CODE + " ")
        self.assertEqual(session, self.server.session())
        request = self.server.requests[0]
        self.assertEqual(parse_qs(request[2].decode()), {"password": [CODE]})
        self.assertNotIn("Cookie", request[3])

    def test_logout_only_revokes_request_session_and_never_follows_redirect(self):
        for status in (200, 303, 401):
            self.server.sessions.add(SECRET)
            self.server.logout_status = status
            self.server.client().logout()
            self.assertEqual(self.server.sessions, {"0v.other-client-session"})
            method, path, raw, headers = self.server.requests[-1]
            self.assertEqual((method, path, raw), ("POST", "/~/logout", b""))
            self.assertEqual(headers["Cookie"], "urbauth-~zod=" + SECRET)
        self.assertEqual(len(self.server.requests), 3)
        for status in (301, 302, 307, 308, 403, 503):
            self.server.logout_status = status
            with self.assertRaises(Failure):
                self.server.client().logout()
        self.server.logout_status = 303
        with self.assertRaises(Failure):
            with self.server.client().request("POST", "/~/logout", b"all=1"):
                pass

    def test_login_redirects_never_followed_even_with_cookie(self):
        for status in (301, 302, 303, 307, 308):
            self.server.login_status = status
            with self.assertRaises(Failure) as caught:
                Eyre(self.server.url).login(CODE)
            self.assertEqual(caught.exception.code, "redirect")
        self.assertEqual(len(self.server.requests), 5)

    def test_cookie_scope_and_missing_cookie(self):
        for cookie in ("other=foo", "urbauth-~zod=x; Domain=other.example", "urbauth-~zod=x; Path=/apps"):
            self.server.login_cookie = cookie
            with self.assertRaises(Failure):
                Eyre(self.server.url).login(CODE)

    def test_scry_authentication_and_redirects(self):
        for status, code in ((401, "authentication"), (403, "authentication"), (302, "redirect")):
            self.server.scry_status = status
            with self.assertRaises(Failure) as caught:
                self.server.client().scry()
            self.assertEqual(caught.exception.code, code)

    def test_channel_success_buffered_frames_ack_and_cleanup(self):
        self.server.mode = "buffered"
        self.server.client().poke("themes", {"themes": [PALETTE]})
        messages = self.server.messages
        self.assertEqual([m["action"] for m in messages], ["poke", "ack", "ack", "delete"])
        self.assertEqual([m["id"] for m in messages], [1, 2, 3, 4])
        self.assertEqual([m["event-id"] for m in messages if m["action"] == "ack"], [9, 10])
        self.assertEqual(messages[0]["ship"], "zod")
        for request in self.server.requests:
            self.assertEqual(request[3]["Cookie"], "urbauth-~zod=" + SECRET)

    def test_nack_is_not_delivered_and_is_sanitized(self):
        self.server.mode = "nack"
        with self.assertRaises(Failure) as caught:
            publish(self.server.client(), PALETTE)
        self.assertEqual(caught.exception.code, "nack")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(self.server.messages[-1]["action"], "delete")

    def test_wrong_request_id_is_not_acknowledgement(self):
        self.server.mode = "wrong-id"
        with self.assertRaises(Failure) as caught:
            publish(self.server.client(), PALETTE)
        self.assertEqual(caught.exception.code, "ack-timeout")

    def test_timeout_readback_can_confirm_delivery(self):
        for mode in ("timeout", "stall"):
            self.server.mode = mode
            self.server.body["desk"]["ui-prefs"] = {}
            publish(self.server.client(), PALETTE)
            self.assertEqual(entries(self.server.body)["themes"]["activeId"], PLUGIN_ID)

    def test_timeout_without_delivery_is_failure(self):
        self.server.mode = "timeout-no-write"
        with self.assertRaises(Failure) as caught:
            publish(self.server.client(), PALETTE)
        self.assertTrue(caught.exception.retryable)

    def test_ack_without_readback_is_failure(self):
        self.server.mode = "ignore"
        with self.assertRaises(Failure) as caught:
            publish(self.server.client(), PALETTE)
        self.assertEqual(caught.exception.code, "verification")

    def test_only_put_entries_and_preserves_other_buckets(self):
        self.server.body["desk"]["ui-prefs"]["unrelated"] = "raw untouched"
        publish(self.server.client(), PALETTE)
        self.assertEqual(self.server.body["desk"]["ui-prefs"]["unrelated"], "raw untouched")
        self.assertEqual(self.server.body["desk"]["other-bucket"], {"opaque": "untouched"})
        writes = [m for m in self.server.messages if m["action"] == "poke"]
        self.assertEqual([m["json"]["put-entry"]["entry-key"] for m in writes], ["themes", "accent"])

    def test_republish_resends_both_entries_without_changing_stored_values(self):
        client = self.server.client()
        publish(client, PALETTE)
        stored = copy.deepcopy(self.server.body)
        before = len(self.server.messages)
        publish(client, PALETTE)
        writes = [m["json"]["put-entry"] for m in self.server.messages[before:] if m["action"] == "poke"]
        self.assertEqual([p["entry-key"] for p in writes], ["themes", "accent"])
        self.assertEqual(self.server.body, stored)

    def test_matching_readback_cannot_confirm_unacknowledged_resend(self):
        client = self.server.client()
        publish(client, PALETTE)
        self.server.mode = "timeout-no-write"
        with self.assertRaises(Failure) as caught:
            publish(client, PALETTE)
        self.assertEqual(caught.exception.code, "ack-timeout")


class StateAndHelperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name) / "private")
        self.keyring = MemoryKeyring()
        self.server = FakeEyre()
        self.addCleanup(self.server.close)
        self.palette = copy.deepcopy(PALETTE)
        self.helper = Helper(self.store, self.keyring, lambda: self.palette, self.server.transport)
        self.expected_account = {"id": "", "url": "", "ship": ""}

    def run_action(self, action, **value):
        if action in ("sync", "set-auto", "disconnect"):
            value.setdefault("expectedAccount", self.expected_account)
        response = self.helper.run(action, value)
        if response["state"] is not None and response["state"]["ships"]:
            self.expected_account = {key: response["state"]["ships"][0][key] for key in ("id", "url", "ship")}
        return response

    def login(self):
        response = self.run_action("login", url=self.server.url, code=CODE)
        self.assertTrue(response["ok"], response)
        return response

    def test_status_and_preview_never_touch_keyring_or_network(self):
        self.assertTrue(self.run_action("status")["ok"])
        self.assertEqual(self.run_action("preview")["palette"], PALETTE)
        self.assertEqual(self.keyring.calls, [])
        self.assertEqual(self.server.requests, [])

    def test_secure_state_permissions_and_atomic_save(self):
        self.login()
        self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)
        path = self.store.root / "state.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        raw = path.read_text()
        self.assertNotIn(SECRET, raw)
        self.assertNotIn(CODE, raw)
        self.assertNotIn("cookie", raw)
        self.assertEqual(sorted(p.name for p in self.store.root.iterdir()), ["lock", "state.json"])

    def test_lock_contention_is_bounded_retryable(self):
        with self.store.locked():
            response = self.run_action("status")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "busy")
        self.assertTrue(response["error"]["retryable"])
        self.assertIsNone(response["state"])

    def test_atomic_replace_failure_preserves_previous_state(self):
        self.login()
        with self.store.locked():
            before = self.store.load()
            changed = copy.deepcopy(before)
            changed["ships"][0]["automatic"] = False
            with patch("client.support.os.replace", side_effect=OSError("write failed")):
                with self.assertRaises(OSError):
                    self.store.save(changed)
            self.assertEqual(self.store.load(), before)
        self.assertEqual(sorted(p.name for p in self.store.root.iterdir()), ["lock", "state.json"])

    def test_cli_observes_lock_held_by_another_process(self):
        script = Path(__file__).resolve().parents[1] / "client/main.py"
        store = StateStore(Path(self.temp.name) / PLUGIN_ID)
        with store.locked():
            env = dict(os.environ, XDG_STATE_HOME=self.temp.name)
            result = subprocess.run([sys.executable, "-B", str(script), "status"], input=b"{}",
                                    capture_output=True, env=env, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "busy")
        self.assertIsNone(json.loads(result.stdout)["state"])
        self.assertEqual(result.stderr, b"")

    def test_corrupt_state_is_not_reset_or_overwritten(self):
        self.login()
        path = self.store.root / "state.json"
        path.write_text("broken-state")
        response = self.run_action("login", url=self.server.url, code=CODE)
        self.assertFalse(response["ok"])
        self.assertIsNone(response["state"])
        self.assertEqual(path.read_text(), "broken-state")
        self.assertEqual(self.keyring.calls, ["store"])

    def test_symlink_state_rejected(self):
        with self.store.locked():
            pass
        other = Path(self.temp.name) / "other"
        other.write_text(dumps(empty_record()))
        (self.store.root / "state.json").symlink_to(other)
        self.assertFalse(self.run_action("status")["ok"])

    def test_account_binding_rejected(self):
        self.login()
        with self.store.locked():
            record = self.store.load()
            record["ships"][0]["url"] = "https://OTHER.example/"
            self.store.save(record)
        self.assertFalse(self.run_action("status")["ok"])

    def test_account_guard_rejects_missing_malformed_and_stale_input_without_effects(self):
        before = self.login()["state"]
        path = self.store.root / "state.json"
        raw = path.read_bytes()
        requests, calls = len(self.server.requests), list(self.keyring.calls)
        expected = dict(self.expected_account)
        invalid = (None, {}, [], SECRET, {"url": self.server.url},
                   dict(expected, ship="~nec"), dict(expected, url="https://other.example"),
                   dict(expected, extra=True), {"url": SECRET, "ship": CODE})
        with patch.object(self.helper, "palette") as palette, patch.object(self.store, "save") as save:
            for action, options in (("sync", {"force": True}), ("set-auto", {"enabled": False}), ("disconnect", {})):
                for account_value in invalid:
                    value = dict(options)
                    if account_value is not None:
                        value["expectedAccount"] = account_value
                    with self.subTest(action=action, account=account_value):
                        response = self.helper.run(action, value)
                        self.assertFalse(response["ok"])
                        self.assertEqual(response["error"]["code"], "account-changed")
                        self.assertFalse(response["error"]["retryable"])
                        self.assertEqual(response["state"], before)
                        self.assertNotIn(SECRET, dumps(response))
                        self.assertNotIn(CODE, dumps(response))
            palette.assert_not_called()
            save.assert_not_called()
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(len(self.server.requests), requests)
        self.assertEqual(self.keyring.calls, calls)

    def test_queued_operation_checks_persisted_account_not_ui_account(self):
        self.login()
        expected = dict(self.expected_account)
        for url, ship in ((self.server.url, "~nec"), ("https://other.example", "~zod")):
            with self.store.locked():
                record = self.store.load()
                record["ships"][0].update(url=url, ship=ship, automatic=True, pending=True)
                self.store.save(record)
            for action, options in (("sync", {"force": True}), ("set-auto", {"enabled": False}), ("disconnect", {})):
                response = self.helper.run(action, dict(options, expectedAccount=expected))
                self.assertEqual(response["error"]["code"], "account-changed")
                self.assertEqual(response["state"]["ships"], [{k: v for k, v in record["ships"][0].items() if k != "fingerprint"}])
            with self.store.locked():
                self.assertEqual(self.store.load(), record)
        self.assertEqual(self.keyring.calls, ["store"])
        self.assertEqual(len(self.server.requests), 1)

    def test_state_read_failure_returns_null_without_saving(self):
        with patch.object(self.store, "load", side_effect=OSError(SECRET)), patch.object(self.store, "save") as save:
            response = self.run_action("status")
        self.assertFalse(response["ok"])
        self.assertIsNone(response["state"])
        self.assertNotIn(SECRET, dumps(response))
        save.assert_not_called()

    def test_failed_consent_save_never_reports_or_commits_mutated_state(self):
        before = self.login()["state"]
        real_save = self.store.save
        for failure in (OSError(SECRET), Failure("state", "State unavailable."), RuntimeError(SECRET)):
            with self.subTest(failure=type(failure).__name__):
                count = 0

                def fail_once(record):
                    nonlocal count
                    count += 1
                    if count == 1:
                        raise failure
                    real_save(record)

                with patch.object(self.store, "save", side_effect=fail_once):
                    response = self.run_action("set-auto", enabled=False)
                self.assertFalse(response["ok"])
                self.assertTrue(response["state"]["ships"][0]["automatic"])
                self.assertEqual(response["state"], before)
                self.assertNotIn(SECRET, dumps(response))
                with self.store.locked():
                    self.assertEqual(response["state"]["ships"][0]["pending"], self.store.load()["ships"][0]["pending"])
                self.assertEqual(count, 1)

    def test_save_failure_and_failed_reload_returns_null(self):
        self.login()
        with self.store.locked():
            before = self.store.load()
        with patch.object(self.store, "load", side_effect=[before, OSError(SECRET)]), \
                patch.object(self.store, "save", side_effect=OSError(SECRET)) as save:
            response = self.run_action("set-auto", enabled=True)
        self.assertFalse(response["ok"])
        self.assertIsNone(response["state"])
        self.assertEqual(save.call_count, 1)
        self.assertNotIn(SECRET, dumps(response))

    def test_save_error_after_replace_reports_actual_persisted_state(self):
        self.login()
        real_save = self.store.save

        def committed_then_failed(record):
            real_save(record)
            raise OSError("directory fsync failed")

        with patch.object(self.store, "save", side_effect=committed_then_failed) as save:
            response = self.run_action("set-auto", enabled=True)
        self.assertFalse(response["ok"])
        self.assertTrue(response["state"]["ships"][0]["automatic"])
        self.assertEqual(save.call_count, 1)
        with self.store.locked():
            self.assertEqual(response["state"]["ships"][0]["automatic"], self.store.load()["ships"][0]["automatic"])

    def test_failed_pending_save_stops_before_palette_keyring_and_network(self):
        self.login()
        requests, calls = len(self.server.requests), list(self.keyring.calls)
        with patch.object(self.store, "save", side_effect=OSError(SECRET)), \
                patch.object(self.helper, "palette") as palette:
            response = self.run_action("sync", force=True)
        self.assertFalse(response["ok"])
        self.assertTrue(response["state"]["ships"][0]["pending"])
        palette.assert_not_called()
        self.assertEqual(len(self.server.requests), requests)
        self.assertEqual(self.keyring.calls, calls)

    def test_failed_success_save_keeps_authoritative_pending_and_old_fingerprint(self):
        self.login()
        real_save = self.store.save

        def fail_success(record):
            if record["ships"][0]["lastPublished"]:
                raise OSError(SECRET)
            real_save(record)

        with patch.object(self.store, "save", side_effect=fail_success):
            response = self.run_action("sync", force=True)
        self.assertFalse(response["ok"])
        self.assertTrue(response["state"]["ships"][0]["pending"])
        self.assertEqual(response["state"]["ships"][0]["lastPublished"], "")
        self.assertEqual(response["state"]["ships"][0]["lastTheme"], "")
        with self.store.locked():
            record = self.store.load()
        self.assertEqual(record["ships"][0], dict(response["state"]["ships"][0], fingerprint=""))
        self.assertTrue(self.run_action("sync", force=True)["ok"])

    def test_error_save_failure_does_not_report_uncommitted_error_metadata(self):
        before = self.login()["state"]
        real_save = self.store.save

        def fail_error(record):
            if record["ships"][0]["lastError"]:
                raise OSError(SECRET)
            real_save(record)

        with patch.object(self.store, "save", side_effect=fail_error), \
                patch.object(self.helper, "palette", side_effect=Failure("palette", "Palette unavailable.", True)):
            response = self.run_action("sync", force=True)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "state")
        self.assertTrue(response["state"]["ships"][0]["pending"])
        self.assertEqual(response["state"]["ships"][0]["lastError"], before["ships"][0]["lastError"])
        with self.store.locked():
            self.assertEqual(dict(response["state"]["ships"][0], fingerprint=""), self.store.load()["ships"][0])

    def test_login_records_consent_and_refuses_duplicate_origin(self):
        response = self.login()
        self.assertTrue(response["state"]["ships"][0]["automatic"])
        self.assertTrue(response["state"]["ships"][0]["pending"])
        self.assertEqual(self.server.messages, [])
        before = len(self.server.requests)
        response = self.run_action("login", url=self.server.url + "/", code=CODE)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "duplicate-origin")
        self.assertEqual(len(self.server.requests), before)
        self.assertEqual(response["state"]["ships"][0]["url"], self.server.url)

    def test_no_plaintext_fallback_if_keyring_store_fails(self):
        self.keyring.fail = True
        response = self.run_action("login", url=self.server.url, code=CODE)
        self.assertFalse(response["ok"])
        self.assertEqual(response["state"], {"ships": []})
        self.assertFalse((self.store.root / "state.json").exists())

    def test_login_is_not_connected_after_silent_keyring_store_failure(self):
        for output in (b"", dumps(dict(self.server.session(), cookieValue="old-cookie")).encode()):
            calls = []

            def runner(args, **_kwargs):
                calls.append(args[1])
                return 0, output if args[1] == "lookup" else b""

            self.helper.keyring = Keyring(runner)
            response = self.run_action("login", url=self.server.url, code=CODE)
            self.assertFalse(response["ok"])
            self.assertEqual(response["error"]["code"], "keyring")
            self.assertEqual(response["state"], {"ships": []})
            self.assertEqual(calls, ["store", "lookup"])
            self.assertNotIn(SECRET, dumps(response))
            with self.store.locked():
                self.assertEqual(self.store.load(), empty_record())

    def test_committed_login_save_failure_does_not_remove_persisted_accounts_session(self):
        real_save = self.store.save

        def committed_then_failed(record):
            real_save(record)
            raise OSError("directory fsync failed")

        with patch.object(self.store, "save", side_effect=committed_then_failed):
            response = self.run_action("login", url=self.server.url, code=CODE)
        self.assertFalse(response["ok"])
        self.assertEqual(len(response["state"]["ships"]), 1)
        self.assertEqual(self.keyring.calls, ["store"])
        self.assertEqual(self.keyring.sessions[self.server.url], self.server.session())

    def test_consent_only_and_manual_publish(self):
        self.assertFalse(self.run_action("set-auto", enabled=True)["ok"])
        self.login()
        self.run_action("set-auto", enabled=False)
        before = len(self.server.requests)
        self.assertTrue(self.run_action("sync")["ok"])
        self.assertEqual(len(self.server.requests), before)
        self.assertTrue(self.run_action("set-auto", enabled=True)["ok"])
        self.assertEqual(len(self.server.requests), before)
        self.run_action("set-auto", enabled=False)
        response = self.run_action("sync", force=True)
        self.assertTrue(response["ok"], response)
        self.assertFalse(response["state"]["ships"][0]["automatic"])
        self.assertTrue(response["state"]["ships"][0]["lastPublished"])

    def test_paused_sync_does_not_resolve_palette_or_access_session(self):
        self.login()
        self.run_action("set-auto", enabled=False)
        calls, requests = list(self.keyring.calls), len(self.server.requests)
        with patch.object(self.helper, "palette") as palette, patch.object(self.store, "save") as save:
            response = self.run_action("sync")
        self.assertTrue(response["ok"])
        self.assertIsNone(response["palette"])
        palette.assert_not_called()
        save.assert_not_called()
        self.assertEqual(self.keyring.calls, calls)
        self.assertEqual(len(self.server.requests), requests)

    def test_palette_preflight_failure_keeps_account_bound_pending_until_retry_or_pause(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.assertTrue(self.run_action("sync")["ok"])
        for force in (False, True):
            with patch.object(self.helper, "palette", side_effect=Failure("palette", "Palette unavailable.", True)):
                response = self.run_action("sync", force=force)
            self.assertFalse(response["ok"])
            self.assertTrue(response["error"]["retryable"])
            self.assertTrue(response["state"]["ships"][0]["pending"])
            with self.store.locked():
                record = self.store.load()
            self.assertTrue(record["ships"][0]["pending"])
            self.assertEqual(record["ships"][0]["id"], self.expected_account["id"])
            # A retry with the same palette must honor the deliberately saved intent.
            remote = entries(self.server.body)["themes"]
            remote["activeId"] = None
            self.server.body["desk"]["ui-prefs"]["themes"] = dumps(remote)
            self.assertTrue(self.run_action("sync")["ok"])
            self.assertEqual(entries(self.server.body)["themes"]["activeId"], PLUGIN_ID)
        with patch.object(self.helper, "palette", side_effect=Failure("palette", "Palette unavailable.", True)):
            self.assertTrue(self.run_action("sync")["state"]["ships"][0]["pending"])
        self.assertFalse(self.run_action("set-auto", enabled=False)["state"]["ships"][0]["pending"])

    def test_unchanged_palette_scry_failure_does_not_create_publication_intent(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.assertTrue(self.run_action("sync")["ok"])
        remote = entries(self.server.body)["themes"]
        remote["activeId"] = None
        self.server.body["desk"]["ui-prefs"]["themes"] = dumps(remote)
        messages = len(self.server.messages)
        self.server.scry_status = 503
        response = self.run_action("sync")
        self.assertFalse(response["ok"])
        self.assertFalse(response["state"]["ships"][0]["pending"])
        self.server.scry_status = 200
        self.assertTrue(self.run_action("sync")["ok"])
        self.assertEqual(len(self.server.messages), messages)
        self.assertEqual(entries(self.server.body)["themes"], remote)

    def test_unchanged_poll_does_not_overwrite_manual_ship_edit(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.assertTrue(self.run_action("sync")["ok"])
        remote = entries(self.server.body)["themes"]
        remote["activeId"] = None
        remote["themes"][0]["primary"] = "#999999"
        self.server.body["desk"]["ui-prefs"]["themes"] = dumps(remote)
        # A new process/Helper gets its guard from private disk state, not memory.
        self.helper = Helper(self.store, self.keyring, lambda: self.palette, self.server.transport)
        before = len(self.server.requests)
        messages_before = len(self.server.messages)
        keyring_before = len(self.keyring.calls)
        self.assertTrue(self.run_action("sync")["ok"])
        self.assertEqual(len(self.server.requests), before + 1)
        self.assertEqual(self.server.requests[-1][0:2], ("GET", "/~/scry/settings/desk/talon.json"))
        self.assertEqual(len(self.server.messages), messages_before)
        self.assertEqual(len(self.keyring.calls), keyring_before + 1)
        self.assertEqual(entries(self.server.body)["themes"], remote)
        self.assertTrue(self.run_action("sync", force=True)["ok"])
        self.assertEqual(entries(self.server.body)["themes"]["themes"][0]["primary"], PALETTE["primary"])

    def test_changed_palette_publishes(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.run_action("sync")
        self.palette["primary"] = "#112233"
        self.assertTrue(self.run_action("sync")["ok"])
        self.assertEqual(entries(self.server.body)["themes"]["themes"][0]["primary"], "#112233")

    def test_publish_now_renotifies_clients_that_missed_the_initial_fact(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.assertTrue(self.run_action("sync")["ok"])
        # A client subscribes now, after taking its bootstrap snapshot before
        # the first publication. It needs a new fact, not matching ship storage.
        before = len(self.server.messages)
        self.assertTrue(self.run_action("sync")["ok"])
        self.assertEqual(len(self.server.messages), before)
        response = self.run_action("sync", force=True)
        self.assertTrue(response["ok"])
        writes = [m["json"]["put-entry"] for m in self.server.messages[before:] if m["action"] == "poke"]
        self.assertEqual([p["entry-key"] for p in writes], ["themes", "accent"])
        self.assertEqual(loads(writes[0]["value"])["themes"], [PALETTE])
        self.assertFalse(loads(writes[1]["value"])["enabled"])

    def test_explicit_reenable_reasserts_palette_but_consent_itself_does_not_write(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.assertTrue(self.run_action("sync")["ok"])
        self.run_action("set-auto", enabled=False)
        remote = entries(self.server.body)["themes"]
        remote["activeId"] = None
        self.server.body["desk"]["ui-prefs"]["themes"] = dumps(remote)
        requests = len(self.server.requests)
        enabled = self.run_action("set-auto", enabled=True)
        self.assertTrue(enabled["state"]["ships"][0]["pending"])
        self.assertEqual(len(self.server.requests), requests)
        self.assertTrue(self.run_action("sync")["ok"])
        self.assertEqual(entries(self.server.body)["themes"]["activeId"], PLUGIN_ID)

    def test_partial_failure_pending_retry_and_pause(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.server.mode = "nack-accent"
        response = self.run_action("sync")
        self.assertFalse(response["ok"])
        self.assertTrue(response["state"]["ships"][0]["pending"])
        self.assertEqual(response["state"]["ships"][0]["lastPublished"], "")
        self.assertNotIn(SECRET, dumps(response))
        self.assertEqual(entries(self.server.body)["themes"]["activeId"], PLUGIN_ID)
        self.server.mode = "success"
        before = len(self.server.messages)
        self.assertTrue(self.run_action("sync")["ok"])
        retried = [m["json"]["put-entry"]["entry-key"] for m in self.server.messages[before:]
                   if m["action"] == "poke"]
        self.assertEqual(retried, ["themes", "accent"])
        self.assertFalse(self.run_action("status")["state"]["ships"][0]["pending"])
        self.server.mode = "timeout-no-write"
        self.palette["primary"] = "#123456"
        self.assertFalse(self.run_action("sync")["ok"])
        response = self.run_action("set-auto", enabled=False)
        self.assertFalse(response["state"]["ships"][0]["pending"])

    def test_expired_authentication_is_not_retried(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.server.scry_status = 401
        response = self.run_action("sync")
        self.assertTrue(response["state"]["ships"][0]["authenticationRequired"])
        self.assertFalse(response["state"]["ships"][0]["automatic"])
        self.assertFalse(response["error"]["retryable"])
        before = len(self.server.requests)
        self.assertTrue(self.run_action("sync")["ok"])
        self.assertEqual(len(self.server.requests), before)

    def test_transient_scry_failure_keeps_pending_without_writes(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.server.scry_status = 503
        response = self.run_action("sync")
        self.assertFalse(response["ok"])
        self.assertTrue(response["error"]["retryable"])
        self.assertTrue(response["state"]["ships"][0]["pending"])
        self.assertEqual(response["state"]["ships"][0]["lastPublished"], "")
        self.assertEqual(self.server.messages, [])
        self.server.scry_status = 200
        self.assertTrue(self.run_action("sync")["ok"])

    def test_corrupt_remote_data_is_not_replaced(self):
        self.login()
        self.server.body = {"desk": {"ui-prefs": {"themes": "broken"}}}
        before = copy.deepcopy(self.server.body)
        response = self.run_action("sync", force=True)
        self.assertFalse(response["ok"])
        self.assertFalse(response["error"]["retryable"])
        self.assertTrue(response["state"]["ships"][0]["pending"])
        self.assertEqual(self.server.body, before)
        self.assertEqual(self.server.messages, [])

    def test_disconnect_leaves_ship_settings_and_removes_local_binding(self):
        self.login()
        self.run_action("sync", force=True)
        before = copy.deepcopy(self.server.body)
        requests = len(self.server.requests)
        response = self.run_action("disconnect")
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"], {"ships": []})
        self.assertEqual(self.keyring.sessions, {})
        self.assertEqual(self.server.body, before)
        self.assertEqual(len(self.server.requests), requests + 1)
        self.assertEqual(self.server.requests[-1][:3], ("POST", "/~/logout", b""))
        self.assertFalse(self.run_action("disconnect")["ok"])

    def test_disconnect_keyring_failure_still_stops_auto(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        self.keyring.fail = True
        response = self.run_action("disconnect")
        self.assertTrue(response["ok"])
        self.assertEqual(response["state"], {"ships": []})
        self.assertEqual(response["warning"]["code"], "cleanup")
        self.assertEqual(self.keyring.calls[-2:], ["lookup", "clear"])

    def test_disconnect_pause_save_failure_does_not_clear_session_or_report_paused(self):
        self.login()
        before = self.run_action("set-auto", enabled=True)["state"]
        calls = list(self.keyring.calls)
        with patch.object(self.store, "save", side_effect=OSError(SECRET)) as save:
            response = self.run_action("disconnect")
        self.assertFalse(response["ok"])
        self.assertEqual(response["state"], before)
        self.assertEqual(self.keyring.calls, calls)
        self.assertEqual(save.call_count, 1)

    def test_disconnect_final_save_failure_does_not_report_uncommitted_disconnection(self):
        self.login()
        self.run_action("set-auto", enabled=True)
        real_save = self.store.save

        def fail_disconnection(record):
            if not record["ships"]:
                raise OSError(SECRET)
            real_save(record)

        with patch.object(self.store, "save", side_effect=fail_disconnection):
            response = self.run_action("disconnect")
        self.assertFalse(response["ok"])
        self.assertTrue(response["state"]["ships"][0]["automatic"])
        self.assertTrue(response["state"]["ships"][0]["pending"])
        self.assertEqual(response["state"]["ships"][0]["url"], self.server.url)
        self.assertIn(self.server.url, self.keyring.sessions)
        with self.store.locked():
            self.assertEqual(dict(response["state"]["ships"][0], fingerprint=""), self.store.load()["ships"][0])
        self.assertTrue(self.run_action("disconnect")["ok"])

    def test_unexpected_errors_are_sanitized(self):
        def fail():
            raise RuntimeError(SECRET + CODE)
        self.helper.palette = fail
        response = self.run_action("preview")
        self.assertNotIn(SECRET, dumps(response))
        self.assertNotIn(CODE, dumps(response))
        self.assertFalse(response["ok"])

    def test_input_types(self):
        for action, value in (("unknown", {}), ("sync", {"force": "true"}),
                              ("set-auto", {"enabled": 1}), ("status", {"extra": 1}), ("status", [])):
            self.assertFalse(self.helper.run(action, value)["ok"])


class CommandAndKeyringTests(unittest.TestCase):
    def test_secret_tool_secret_is_stdin_not_argv(self):
        calls = []
        session = dict(url="https://ship.example", ship="~zod", cookieName="urbauth-~zod", cookieValue=SECRET)

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            return 0, dumps(session).encode() if args[1] == "lookup" else b""

        keyring = Keyring(runner)
        keyring.store(session["url"], session)
        self.assertEqual(keyring.lookup(session["url"], "~zod"), session)
        keyring.clear(session["url"])
        self.assertEqual([args[1] for args, _ in calls], ["store", "lookup", "lookup", "clear"])
        for args, _ in calls:
            self.assertNotIn(SECRET, dumps(args))
            self.assertNotIn(CODE, dumps(args))
        self.assertIn(SECRET.encode(), calls[0][1]["data"])
        self.assertIn(PLUGIN_ID, calls[0][0])

    def test_keyring_failure_output_is_not_exposed(self):
        keyring = Keyring(lambda *_args, **_kwargs: (1, (SECRET + CODE).encode()))
        with self.assertRaises(Failure) as caught:
            keyring.lookup("https://ship.example", "~zod")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_keyring_account_mismatch(self):
        session = dict(url="https://other.example", ship="~zod", cookieName="urbauth-~zod", cookieValue=SECRET)
        keyring = Keyring(lambda *_args, **_kwargs: (0, dumps(session).encode()))
        with self.assertRaises(Failure):
            keyring.lookup("https://ship.example", "~zod")

    def test_keyring_store_requires_exact_lookup(self):
        session = dict(url="https://ship.example", ship="~zod", cookieName="urbauth-~zod", cookieValue=SECRET)
        for result, output in ((1, b""), (0, b""), (0, b"not json"),
                               (0, dumps(dict(session, cookieValue="old-cookie")).encode()),
                               (0, dumps(dict(session, url="https://other.example")).encode())):
            def runner(args, **_kwargs):
                return (result, output) if args[1] == "lookup" else (0, b"")

            with self.assertRaises(Failure) as caught:
                Keyring(runner).store(session["url"], session)
            self.assertEqual(caught.exception.code, "keyring")
            self.assertNotIn(SECRET, str(caught.exception))

    def test_subprocess_time_and_output_bounds(self):
        for source, options in (("import time; time.sleep(2)", {"timeout": 0.05}),
                                ("import sys; sys.stdout.write('x' * 10000)", {"limit": 20})):
            started = time.monotonic()
            with self.assertRaises(Failure):
                command([sys.executable, "-c", source], **options)
            self.assertLess(time.monotonic() - started, 1)

    def test_subprocess_stdin_and_stderr(self):
        result, output = command([sys.executable, "-c",
                                  "import sys; sys.stderr.write('private error'); sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                                 data=b"private stdin")
        self.assertEqual((result, output), (0, b"private stdin"))

    def test_cli_json_and_sanitized_failure(self):
        script = Path(__file__).resolve().parents[1] / "client/main.py"
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, HOME=temp, XDG_STATE_HOME=temp, PATH="/nonexistent")
            for action, value, code in (("status", {}, 0), ("unknown", {"code": CODE}, 1),
                                        ("preview", {}, 1), ("status", {"extra": SECRET}, 1)):
                result = subprocess.run([sys.executable, "-B", str(script), action], input=dumps(value).encode(),
                                        capture_output=True, env=env, timeout=5)
                self.assertEqual(result.returncode, code)
                self.assertEqual(result.stderr, b"")
                response = json.loads(result.stdout)
                self.assertEqual(set(response), {"schemaVersion", "ok", "state", "palette", "error", "warning"})
                self.assertEqual(response["schemaVersion"], 2)
                self.assertNotIn(SECRET.encode(), result.stdout)
                self.assertNotIn(CODE.encode(), result.stdout)

    def test_cli_rejects_oversized_and_malformed_input(self):
        script = Path(__file__).resolve().parents[1] / "client/main.py"
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, HOME=temp, XDG_STATE_HOME=temp)
            for raw in (b"x" * 8193, b'{"code":"private",}', b"[]"):
                result = subprocess.run([sys.executable, "-B", str(script), "status"], input=raw,
                                        capture_output=True, env=env, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stderr, b"")
                self.assertFalse(json.loads(result.stdout)["ok"])
                if raw != b"[]":
                    self.assertIsNone(json.loads(result.stdout)["state"])
                self.assertNotIn(b"private", result.stdout.lower())

    def test_cli_account_guard_examples(self):
        script = Path(__file__).resolve().parents[1] / "client/main.py"
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, HOME=temp, XDG_STATE_HOME=temp, PATH="/nonexistent")
            expected = {"id": "0" * 64, "url": "https://ship.example", "ship": "~zod"}
            for action, options in (("sync", {"force": False}), ("set-auto", {"enabled": False}), ("disconnect", {})):
                for supplied in (False, True):
                    value = dict(options, expectedAccount=expected) if supplied else options
                    result = subprocess.run([sys.executable, "-B", str(script), action], input=dumps(value).encode(),
                                            capture_output=True, env=env, timeout=5)
                    response = json.loads(result.stdout)
                    self.assertEqual(result.stderr, b"")
                    self.assertFalse(response["ok"])
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(response["error"]["code"], "account-changed")
                    self.assertFalse(response["error"]["retryable"])


if __name__ == "__main__":
    unittest.main()
