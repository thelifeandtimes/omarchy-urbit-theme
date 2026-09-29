"""Two simulated desktops plus real loopback HTTP/SSE transport, no live ship."""

import contextlib
import copy
import http.server
import json
import threading
import time
import unittest

from client.eyre import Eyre
from client.profile import DESK, digest
from client.support import Failure
from client.sync import Coordinator, Remote, Watch, fresh
from test_profile import profile


class MemoryDisk:
    def __init__(self):
        self.value = fresh()

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)


class FakeDesktop:
    def __init__(self, value):
        self.value = copy.deepcopy(value)
        self.applications = []
        self.fail = False

    def capture(self, device):
        return copy.deepcopy(self.value)

    def apply(self, value):
        self.applications.append(value["updateId"])
        if self.fail:
            raise Failure("missing-font", "Missing font")
        self.value = copy.deepcopy(value)


class MemoryHub:
    def __init__(self):
        self.value = None
        self.talon = {"desk": {"ui-prefs": {}}}
        self.writes = []
        self.offline = False
        self.lose_ack = False
        self.after_write = None

    def read(self):
        if self.offline:
            raise Failure("network", "Offline", True)
        return copy.deepcopy(self.value)

    def write(self, value):
        if self.offline:
            raise Failure("network", "Offline", True)
        self.value = copy.deepcopy(value)
        self.writes.append(value["updateId"])
        if self.after_write:
            self.after_write()
        if self.lose_ack:
            raise Failure("ack-timeout", "Lost ACK", True)


class CoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.hub = MemoryHub()
        self.a = FakeDesktop(profile())
        self.b = FakeDesktop(profile("catppuccin", "#445566", "b" * 32))
        self.ad, self.bd = MemoryDisk(), MemoryDisk()
        self.ca = Coordinator(self.ad, self.a, self.hub)
        self.cb = Coordinator(self.bd, self.b, self.hub)

    def step(self, engine, desktop, intentional=False):
        return engine.reconcile(desktop.capture(engine.state["deviceId"]), intentional)

    def join(self):
        self.step(self.ca, self.a)
        self.step(self.cb, self.b)

    def test_first_machine_seeds_second_adopts_without_stale_publication(self):
        original = self.a.value["updateId"]
        self.join()
        self.assertEqual(self.hub.writes, [original])
        self.assertEqual(digest(self.a.value), digest(self.b.value))
        self.assertEqual(self.a.applications, [])
        self.assertEqual(self.b.applications, [original])

    def test_both_directions_and_no_echo_or_repeated_apply(self):
        self.join()
        self.b.value = profile("catppuccin", "#445566", "b" * 32)
        latest = self.step(self.cb, self.b, True)
        self.step(self.ca, self.a)
        for _ in range(5):
            self.step(self.ca, self.a)
            self.step(self.cb, self.b)
        self.assertEqual(len(self.hub.writes), 2)
        self.assertEqual(digest(self.a.value), digest(latest))
        self.assertEqual(len(self.a.applications), 1)
        self.assertEqual(len(self.b.applications), 1)

    def test_later_ship_arrival_wins_even_between_poke_and_readback(self):
        self.join()
        self.a.value = profile(color="#AAAAAA")
        newer = profile(color="#BBBBBB", device="b" * 32)
        self.hub.after_write = lambda: setattr(self.hub, "value", newer)
        self.step(self.ca, self.a, True)
        self.step(self.cb, self.b)
        self.assertEqual(digest(self.a.value), digest(newer))
        self.assertEqual(digest(self.b.value), digest(newer))
        self.assertIsNone(self.ca.state["pending"])

    def test_offline_intent_survives_process_restart_and_wins_on_arrival(self):
        self.join()
        self.hub.offline = True
        self.a.value = profile(color="#AAAAAA")
        with self.assertRaises(Failure):
            self.step(self.ca, self.a, True)
        self.ca = Coordinator(self.ad, self.a, self.hub)
        self.hub.offline = False
        self.hub.value = profile(color="#BBBBBB")
        self.step(self.ca, self.a)
        self.step(self.cb, self.b)
        self.assertEqual(self.hub.value["colors"]["background"], "#AAAAAA")
        self.assertEqual(digest(self.a.value), digest(self.b.value))

    def test_lost_ack_is_resolved_without_overwriting_later_writer(self):
        self.join()
        self.a.value = profile(color="#AAAAAA")
        self.hub.lose_ack = True
        with self.assertRaises(Failure):
            self.step(self.ca, self.a, True)
        newer = profile(color="#BBBBBB")
        self.hub.value = newer
        self.hub.lose_ack = False
        self.ca = Coordinator(self.ad, self.a, self.hub)
        self.step(self.ca, self.a)
        self.assertEqual(self.hub.value, newer)
        self.assertEqual(len(self.hub.writes), 2)
        self.assertIsNone(self.ca.state["pending"])

    def test_lost_ack_matching_readback_does_not_resend(self):
        self.join()
        self.a.value = profile(color="#AAAAAA")
        self.hub.lose_ack = True
        with self.assertRaises(Failure):
            self.step(self.ca, self.a, True)
        self.hub.lose_ack = False
        self.step(self.ca, self.a)
        self.assertEqual(len(self.hub.writes), 2)

    def test_restart_and_resume_do_not_promote_stale_local_values(self):
        self.join()
        self.b.value = profile("catppuccin", "#DDDDDD")
        self.cb = Coordinator(self.bd, self.b, self.hub)
        self.step(self.cb, self.b)
        self.assertEqual(len(self.hub.writes), 1)
        self.assertEqual(digest(self.b.value), digest(self.hub.value))

    def test_missing_font_retains_desired_profile_then_recovers(self):
        self.step(self.ca, self.a)
        self.b.fail = True
        with self.assertRaises(Failure):
            self.step(self.cb, self.b)
        self.assertEqual(self.bd.value["applying"], self.hub.value)
        self.assertIsNone(self.bd.value["observed"])
        self.b.fail = False
        self.cb = Coordinator(self.bd, self.b, self.hub)
        self.step(self.cb, self.b)
        self.assertIsNone(self.cb.state["applying"])
        self.assertEqual(digest(self.b.value), digest(self.hub.value))

    def test_interrupted_application_must_finish_even_when_capture_matches(self):
        self.join()
        self.bd.value["applying"] = copy.deepcopy(self.hub.value)
        self.cb = Coordinator(self.bd, self.b, self.hub)
        before = len(self.b.applications)
        self.step(self.cb, self.b)
        self.assertEqual(len(self.b.applications), before + 1)

    def test_unknown_extension_survives_a_local_color_change(self):
        self.join()
        self.hub.value["future"] = {"example": 123}
        self.ca.state["observed"] = copy.deepcopy(self.hub.value)
        self.a.value = profile(color="#ABCDEF")
        self.step(self.ca, self.a, True)
        self.assertEqual(self.hub.value["future"], {"example": 123})


class LiveHub(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), LiveHandler)
        self.url = "http://127.0.0.1:" + str(self.server_port)
        self.value = None
        self.talon = {"desk": {"ui-prefs": {}}}
        self.channels = {}
        self.events = threading.Condition()
        self.sequence = 0
        self.stopping = False
        self.acks = []
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def client(self):
        return Eyre(self.url, dict(url=self.url, ship="~zod", cookieName="urbauth-~zod", cookieValue="synthetic"), timeout=0.3)

    def close(self):
        with self.events:
            self.stopping = True
            self.events.notify_all()
        self.shutdown()
        self.server_close()
        self.thread.join()

    def event(self, channel, event):
        self.sequence += 1
        self.channels[channel]["frames"].append((self.sequence, event))


class LiveHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, value=None):
        body = b"" if value is None else json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        s = self.server
        if self.path == "/~/scry/settings/desk/" + DESK + ".json":
            return self.reply({"desk": {"appearance": {"current": json.dumps(s.value)}}} if s.value else {"desk": {}})
        if self.path == "/~/scry/settings/desk/talon.json":
            return self.reply(s.talon)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        while not s.stopping:
            with s.events:
                c = s.channels.get(self.path)
                if c is None or c["deleted"]:
                    return
                frames = c["frames"][:]
                c["frames"].clear()
                if not frames:
                    s.events.wait(0.05)
            try:
                for event_id, event in frames:
                    data = f"id: {event_id}\ndata: {json.dumps(event)}\n\n".encode()
                    # Fragment across reads to exercise the actual stream parser.
                    self.wfile.write(data[:7]); self.wfile.flush()
                    self.wfile.write(data[7:]); self.wfile.flush()
                if not frames:
                    self.wfile.write(b": heartbeat\n\n"); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def do_PUT(self):
        s = self.server
        messages = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with s.events:
            channel = s.channels.setdefault(self.path, {"frames": [], "subscribed": False, "deleted": False})
            for m in messages:
                if m["action"] == "subscribe":
                    assert m["path"] == "/desk/" + DESK
                    channel["subscribed"] = True
                    s.event(self.path, {"id": m["id"], "response": "subscribe", "ok": "ok"})
                elif m["action"] == "poke":
                    put = m["json"]["put-entry"]
                    if put["desk"] == DESK:
                        assert put["bucket-key"] == "appearance" and put["entry-key"] == "current"
                        s.value = json.loads(put["value"])
                    else:
                        assert put["desk"] == "talon" and put["bucket-key"] == "ui-prefs"
                        s.talon["desk"]["ui-prefs"][put["entry-key"]] = put["value"]
                    s.event(self.path, {"id": m["id"], "response": "poke", "ok": "ok"})
                    for path, c in s.channels.items():
                        if put["desk"] == DESK and c["subscribed"] and not c["deleted"]:
                            s.event(path, {"id": 1, "response": "diff", "json": {"put-entry": put}})
                elif m["action"] == "ack":
                    s.acks.append(m["event-id"])
                elif m["action"] == "delete":
                    channel["deleted"] = True
            s.events.notify_all()
        self.reply()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.send_header("Set-Cookie", "urbauth-~zod=synthetic; Path=/; HttpOnly")
        self.send_header("Content-Length", "0")
        self.end_headers()


class LiveTransportTests(unittest.TestCase):
    def test_worker_reconnects_after_subscription_is_lost(self):
        server = LiveHub()
        self.addCleanup(server.close)
        watch = Watch(server.url, server.client().session)
        try:
            self.assertTrue(watch.ready.wait(2))
            with server.events:
                for c in server.channels.values():
                    c["deleted"] = True
                server.value = profile(color="#ABCDEF")
                server.events.notify_all()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if len(server.channels) >= 2 and watch.ready.is_set():
                    break
                time.sleep(0.05)
            self.assertGreaterEqual(len(server.channels), 2)
            self.assertTrue(watch.ready.is_set())
            self.assertTrue(watch.changed.is_set())
            self.assertEqual(Remote(server.client()).read()["colors"]["background"], "#ABCDEF")
        finally:
            watch.stop.set()
            watch.thread.join(2)
        self.assertFalse(watch.thread.is_alive())

    def test_live_subscription_publish_ack_reconnect_and_authoritative_read(self):
        server = LiveHub()
        self.addCleanup(server.close)
        client = server.client()
        remote = Remote(client)
        self.assertIsNone(remote.read())
        stop, changed, ready = threading.Event(), threading.Event(), threading.Event()
        errors = []

        def watch():
            try:
                client.watch(DESK, stop, changed, ready)
            except Exception as e:
                errors.append(e)

        thread = threading.Thread(target=watch)
        thread.start()
        try:
            self.assertTrue(ready.wait(2))
            changed.clear()
            value = profile()
            remote.write(value)
            self.assertTrue(changed.wait(2))
            self.assertEqual(remote.read(), value)
            newer = profile(color="#AABBCC")
            remote.write(newer)
            self.assertEqual(remote.read(), newer)
            self.assertGreaterEqual(len(server.acks), 3)
        finally:
            stop.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertFalse(ready.is_set())
        self.assertTrue(all(c["deleted"] for c in server.channels.values()))
        # A new connection scries the latest value, not its pre-disconnect value.
        self.assertEqual(Remote(server.client()).read(), newer)


if __name__ == "__main__":
    unittest.main()
