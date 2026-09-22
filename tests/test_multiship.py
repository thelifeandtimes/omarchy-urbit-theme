"""Multi-ship isolation, disk migration, and removal failure boundaries."""

import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_backend import CODE, SECRET, PALETTE, FakeEyre, MemoryKeyring
from client.main import Helper
from client.support import (Failure, Keyring, StateStore, account, dumps, empty_ship,
                            fingerprint, loads)


def expected(row):
    return {k: row[k] for k in ("id", "url", "ship")}


class MultiShipTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = StateStore(Path(temp.name) / "private")
        self.keyring = MemoryKeyring()
        self.first = FakeEyre()
        self.second = FakeEyre("~nec")
        self.addCleanup(self.first.close)
        self.addCleanup(self.second.close)
        self.helper = Helper(self.store, self.keyring, lambda: PALETTE, self.first.transport)

    def add(self, server):
        response = self.helper.run("login", dict(url=server.url, code=CODE))
        self.assertTrue(response["ok"], response)
        return response["state"]["ships"][-1]

    def run_row(self, action, row, **options):
        return self.helper.run(action, dict(expectedAccount=expected(row), **options))

    def disk(self):
        with self.store.locked():
            return self.store.load()

    def test_independent_errors_auth_pause_resume_and_removal(self):
        first, second = self.add(self.first), self.add(self.second)
        untouched = copy.deepcopy(self.disk()["ships"][1])
        self.first.scry_status = 401
        failed = self.run_row("sync", first)
        self.assertFalse(failed["ok"])
        self.assertTrue(failed["state"]["ships"][0]["authenticationRequired"])
        self.assertFalse(failed["state"]["ships"][0]["automatic"])
        self.assertFalse(failed["state"]["ships"][0]["pending"])
        self.assertEqual(self.disk()["ships"][1], untouched)
        failed_first = copy.deepcopy(self.disk()["ships"][0])
        shared = dict(PALETTE, name="Shared snapshot", primary="#123ABC")
        with patch.object(self.helper, "palette", side_effect=AssertionError("must use shared palette")):
            synced = self.run_row("sync", second, palette=shared)
        self.assertTrue(synced["ok"], synced)
        self.assertEqual(synced["palette"], shared)
        self.assertEqual(self.disk()["ships"][0], failed_first)
        self.assertEqual(self.disk()["ships"][1]["fingerprint"], fingerprint(shared))
        calls, requests = list(self.keyring.calls), len(self.second.requests)
        for enabled in (False, True, True, False):
            response = self.run_row("set-auto", second, enabled=enabled)
            self.assertTrue(response["ok"])
            self.assertIs(response["state"]["ships"][1]["pending"], enabled)
            self.assertIs(response["state"]["ships"][1]["automatic"], enabled)
            self.assertEqual(self.disk()["ships"][0], failed_first)
        self.assertEqual(self.keyring.calls, calls)
        self.assertEqual(len(self.second.requests), requests)
        second_before = copy.deepcopy(self.disk()["ships"][1])
        self.first.logout_status = 401
        removed = self.run_row("disconnect", first)
        self.assertTrue(removed["ok"], removed)
        self.assertIsNone(removed["warning"])
        self.assertEqual(self.disk()["ships"], [second_before])
        self.assertEqual(set(self.keyring.sessions), {self.second.url})
        self.assertEqual(len(self.second.requests), requests)

    def test_same_ship_on_different_origins_has_independent_sessions_and_ids(self):
        second_secret = "0v.second-origin-session"
        self.second.ship = "~zod"
        self.second.login_cookie = "urbauth-~zod=" + second_secret + "; Path=/; HttpOnly"
        self.second.sessions = {second_secret, "0v.other-client-session"}
        first, second = self.add(self.first), self.add(self.second)
        self.assertEqual(first["ship"], second["ship"])
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(len(self.keyring.sessions), 2)
        self.assertEqual(self.keyring.sessions[self.first.url]["cookieValue"], SECRET)
        self.assertEqual(self.keyring.sessions[self.second.url]["cookieValue"], second_secret)
        for row in (first, second):
            response = self.run_row("sync", row, palette=PALETTE)
            self.assertTrue(response["ok"], response)
        self.assertTrue(self.run_row("disconnect", first)["ok"])
        self.assertEqual(self.first.sessions, {"0v.other-client-session"})
        self.assertIn(second_secret, self.second.sessions)
        self.assertEqual(set(self.keyring.sessions), {self.second.url})
        self.assertTrue(self.run_row("disconnect", second)["ok"])
        self.assertEqual(self.second.sessions, {"0v.other-client-session"})
        self.assertEqual(self.keyring.sessions, {})
        for server, cookie in ((self.first, SECRET), (self.second, second_secret)):
            for method, path, body, headers in server.requests:
                if path == "/~/login":
                    self.assertNotIn("Cookie", headers)
                else:
                    self.assertEqual(headers["Cookie"], "urbauth-~zod=" + cookie)

    def test_readd_generates_new_id_and_cookie_and_rejects_old_intent(self):
        first = self.add(self.first)
        other = self.add(self.second)
        self.assertTrue(self.run_row("disconnect", first)["ok"])
        new_secret = "0v.new-independent-session"
        self.first.login_cookie = "urbauth-~zod=" + new_secret + "; Path=/"
        replacement = self.add(self.first)
        self.assertNotEqual(first["id"], replacement["id"])
        self.assertEqual(self.keyring.sessions[self.first.url]["cookieValue"], new_secret)
        before = self.disk()
        calls, requests = list(self.keyring.calls), len(self.first.requests)
        for action, options in (("sync", {"force": True}), ("set-auto", {"enabled": False}), ("disconnect", {})):
            response = self.run_row(action, first, **options)
            self.assertEqual(response["error"]["code"], "account-changed")
            for key, bad in (("id", other["id"]), ("url", other["url"]), ("ship", other["ship"])):
                response = self.run_row(action, dict(replacement, **{key: bad}), **options)
                self.assertEqual(response["error"]["code"], "account-changed")
        self.assertEqual(self.disk(), before)
        self.assertEqual(self.keyring.calls, calls)
        self.assertEqual(len(self.first.requests), requests)

    def test_transient_error_and_force_resend_affect_only_target(self):
        first, second = self.add(self.first), self.add(self.second)
        self.assertTrue(self.run_row("sync", first)["ok"])
        self.assertTrue(self.run_row("sync", second)["ok"])
        other = copy.deepcopy(self.disk()["ships"][1])
        self.first.mode = "timeout-no-write"
        failed = self.run_row("sync", first, force=True)
        self.assertEqual(failed["error"]["code"], "ack-timeout")
        self.assertTrue(failed["state"]["ships"][0]["pending"])
        self.assertEqual(self.disk()["ships"][1], other)
        self.first.mode = "success"
        self.assertTrue(self.run_row("sync", first)["ok"])
        self.assertEqual(self.disk()["ships"][1], other)

    def test_remove_missing_cookie_offline_locked_and_clear_failure(self):
        other = self.add(self.second)
        untouched = self.disk()["ships"][0]
        for mode in ("missing", "offline", "locked", "clear-failed", "clear-exit-one"):
            with self.subTest(mode=mode):
                row = self.add(self.first)
                if mode == "missing":
                    self.keyring.sessions.pop(self.first.url)
                self.keyring.fail = mode == "locked"
                transport = self.helper.transport
                clear = self.keyring.clear
                if mode == "offline":
                    def offline(*args):
                        raise Failure("network", SECRET + CODE, True)
                    self.helper.transport = offline
                if mode == "clear-failed":
                    def fail_clear(url):
                        raise RuntimeError(SECRET + CODE)
                    self.keyring.clear = fail_clear
                if mode == "clear-exit-one":
                    self.keyring.clear = Keyring(lambda *args, **kwargs: (1, SECRET.encode())).clear
                try:
                    response = self.run_row("disconnect", row)
                finally:
                    self.helper.transport = transport
                    self.keyring.clear = clear
                    self.keyring.fail = False
                self.assertTrue(response["ok"], response)
                self.assertEqual(response["state"]["ships"], [other])
                self.assertEqual(self.disk()["ships"], [untouched])
                self.assertIsNotNone(response["warning"])
                self.assertFalse(response["warning"]["retryable"])
                self.assertNotIn(SECRET, dumps(response))
                self.assertNotIn(CODE, dumps(response))
                self.assertIn(self.second.url, self.keyring.sessions)
                if mode in ("missing", "offline", "locked"):
                    self.assertEqual(self.keyring.calls[-2:], ["lookup", "clear"])

    def test_committed_removal_save_failure_still_cleans_up_without_resurrection(self):
        first, second = self.add(self.first), self.add(self.second)
        real_save = self.store.save

        def committed_then_failed(record):
            real_save(record)
            raise OSError(SECRET)

        with patch.object(self.store, "save", side_effect=committed_then_failed) as save:
            response = self.run_row("disconnect", first)
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "state")
        self.assertEqual(response["state"]["ships"], [second])
        self.assertEqual(save.call_count, 1)
        self.assertNotIn(self.first.url, self.keyring.sessions)
        self.assertEqual(self.first.requests[-1][:3], ("POST", "/~/logout", b""))
        self.assertEqual(len(self.second.requests), 1)

    def test_uncommitted_add_save_failure_preserves_other_ship_and_cleans_new_session(self):
        other = self.add(self.second)
        before = self.disk()
        with patch.object(self.store, "save", side_effect=OSError(SECRET)):
            response = self.helper.run("login", dict(url=self.first.url, code=CODE))
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "state")
        self.assertEqual(response["state"]["ships"], [other])
        self.assertEqual(self.disk(), before)
        self.assertEqual(set(self.keyring.sessions), {self.second.url})
        self.assertEqual(self.first.sessions, {"0v.other-client-session"})
        self.assertEqual(len(self.second.requests), 1)
        self.assertNotIn(SECRET, dumps(response))

    def test_shared_palette_validation_before_credentials_or_network(self):
        row = self.add(self.first)
        calls, requests = list(self.keyring.calls), len(self.first.requests)
        for palette in (None, [], {}, dict(PALETTE, id="other"), dict(PALETTE, dark=1),
                        dict(PALETTE, primary="#AABBCCDD"), dict(PALETTE, name="x\n"),
                        dict(PALETTE, name="x" * 257), dict(PALETTE, extra=SECRET)):
            with self.subTest(palette=palette):
                self.assertFalse(self.run_row("sync", row, palette=palette)["ok"])
        self.assertEqual(self.keyring.calls, calls)
        self.assertEqual(len(self.first.requests), requests)

    def test_limit_and_strict_disk_records(self):
        row = self.add(self.first)
        record = self.disk()
        records = [dict(record, version=True), dict(record, ships={}), dict(record, extra=True)]
        for key, bad in (("automatic", 1), ("pending", "true"), ("authenticationRequired", 0),
                         ("id", "bad"), ("fingerprint", False), ("ship", "zod"),
                         ("url", self.first.url + "/"), ("lastError", "x" * 513), ("lastTheme", []),
                         ("lastTheme", "Old\x7fTheme"), ("lastPublished", "invalid")):
            records.append(dict(version=2, ships=[dict(record["ships"][0], **{key: bad})]))
        records.append(dict(version=2, ships=[record["ships"][0]] * 2))
        records.append(dict(version=2, ships=[record["ships"][0], dict(record["ships"][0], id="f" * 64)]))
        records.append(dict(version=2, ships=[record["ships"][0], dict(record["ships"][0], url=self.second.url)]))
        path = self.store.root / "state.json"
        for bad in records:
            path.write_text(dumps(bad))
            response = self.helper.run("status", {})
            self.assertFalse(response["ok"], bad)
            self.assertIsNone(response["state"])
            self.assertEqual(loads(path.read_text()), bad)
        full = dict(version=2, ships=[dict(record["ships"][0], id=f"{i:064x}", url=f"https://ship{i}.example")
                                     for i in range(64)])
        path.write_text(dumps(full))
        self.assertTrue(self.helper.run("status", {})["ok"])
        requests, calls = len(self.first.requests), list(self.keyring.calls)
        response = self.helper.run("login", dict(url=self.first.url, code=CODE))
        self.assertEqual(response["error"]["code"], "ship-limit")
        self.assertEqual(len(response["state"]["ships"]), 64)
        self.assertEqual(len(self.first.requests), requests)
        self.assertEqual(self.keyring.calls, calls)
        full["ships"].append(dict(full["ships"][0], id="f" * 64, url=self.first.url))
        path.write_text(dumps(full))
        self.assertIsNone(self.helper.run("status", {})["state"])

    def test_v1_empty_and_connected_migrate_without_observation_writes_or_secrets(self):
        state = {k: v for k, v in empty_ship().items() if k != "id"}
        state["connected"] = False
        legacy = dict(version=1, state=state, account="", fingerprint="")
        with self.store.locked():
            self.store.save(legacy)
        path = self.store.root / "state.json"
        raw = path.read_bytes()
        for action in ("status", "preview"):
            self.assertEqual(self.helper.run(action, {})["state"], {"ships": []})
            self.assertEqual(path.read_bytes(), raw)
        for automatic, pending in ((False, False), (True, False), (True, True)):
            state.update(connected=True, url=self.first.url, ship="~zod", automatic=automatic, pending=pending,
                         lastPublished="2026-09-22T01:02:03Z", lastTheme="Old theme", lastError="A safe error.")
            legacy.update(account=account(self.first.url, "~zod"), fingerprint=fingerprint(PALETTE))
            path.write_text(dumps(legacy))
            raw = path.read_bytes()
            before_calls = list(self.keyring.calls)
            for action in ("status", "preview"):
                response = self.helper.run(action, {})
                self.assertTrue(response["ok"], response)
                row = response["state"]["ships"][0]
                self.assertEqual(row, dict({k: v for k, v in state.items() if k != "connected"}, id=legacy["account"]))
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(self.keyring.calls, before_calls)
                self.assertEqual(self.first.requests, [])
            self.keyring.sessions[self.first.url] = self.first.session()
            response = self.run_row("set-auto", row, enabled=True)
            self.assertTrue(response["ok"])
            self.assertEqual(self.keyring.calls, before_calls)
            disk = loads(path.read_text())
            self.assertEqual(disk["version"], 2)
            self.assertEqual(disk["ships"][0]["fingerprint"], legacy["fingerprint"])
            self.assertEqual(disk["ships"][0]["id"], legacy["account"])
        self.assertTrue(self.run_row("sync", row)["ok"])
        self.assertEqual(self.keyring.calls, ["lookup"])

    def test_invalid_legacy_identity_is_not_migrated(self):
        state = dict(connected=True, **{k: v for k, v in empty_ship().items() if k != "id"})
        state.update(url=self.first.url, ship="~zod")
        legacy = dict(version=1, state=state, account="f" * 64, fingerprint="")
        with self.store.locked():
            self.store.save(legacy)
        response = self.helper.run("status", {})
        self.assertFalse(response["ok"])
        self.assertIsNone(response["state"])
        self.assertEqual(self.keyring.calls, [])
        self.assertEqual(self.first.requests, [])

    def test_v1_display_metadata_is_normalized_without_changing_account_or_reading_secrets(self):
        timestamp = "2026-09-22T01:02:03Z"
        fixtures = (
            ({"lastTheme": "Old\x7fTheme", "lastError": "", "lastPublished": timestamp},
             {"lastTheme": "OldTheme", "lastError": "", "lastPublished": timestamp}),
            ({"lastTheme": "Old\x00\n\x7f" + "x" * 300, "lastError": "\x7f\n" + "e" * 600,
              "lastPublished": "x" * 100},
             {"lastTheme": "Old" + "x" * 253, "lastError": "e" * 512, "lastPublished": ""}),
            ({"lastTheme": "", "lastError": "", "lastPublished": "invalid"},
             {"lastTheme": "", "lastError": "", "lastPublished": ""}),
        )
        for metadata, normalized in fixtures:
            for automatic, pending in ((False, False), (True, False), (True, True)):
                with self.subTest(metadata=metadata, automatic=automatic, pending=pending):
                    state = {k: v for k, v in empty_ship().items() if k != "id"}
                    state.update(connected=True, url=self.first.url, ship="~zod", automatic=automatic,
                                 pending=pending, **metadata)
                    legacy = dict(version=1, state=state, account=account(self.first.url, "~zod"),
                                  fingerprint=fingerprint(dict(PALETTE, name=metadata["lastTheme"])))
                    with self.store.locked():
                        self.store.save(legacy)
                    path = self.store.root / "state.json"
                    raw = path.read_bytes()
                    expected_row = dict({k: v for k, v in state.items() if k != "connected"},
                                        id=legacy["account"])
                    expected_row.update(normalized)
                    with patch.object(self.helper, "keyring") as keyring, \
                            patch.object(self.helper, "transport") as transport, \
                            patch.object(self.store, "save") as save:
                        for action in ("status", "preview"):
                            response = self.helper.run(action, {})
                            self.assertTrue(response["ok"], response)
                            self.assertEqual(response["state"], {"ships": [expected_row]})
                            self.assertEqual(path.read_bytes(), raw)
                        self.assertEqual(self.disk(), dict(version=2, ships=[
                            dict(expected_row, fingerprint=legacy["fingerprint"])]))
                        self.assertEqual(keyring.mock_calls, [])
                        transport.assert_not_called()
                        save.assert_not_called()
                    self.assertEqual(self.keyring.calls, [])
                    self.assertEqual(self.first.requests, [])
                    self.assertEqual(self.second.requests, [])
                    # A missing/locked keyring still cannot trap the migrated row.
                    with patch.object(self.helper, "keyring") as keyring, \
                            patch.object(self.helper, "transport") as transport:
                        keyring.lookup.side_effect = Failure("keyring", "Unavailable.")
                        keyring.clear.side_effect = Failure("keyring", "Unavailable.")
                        removed = self.run_row("disconnect", expected_row)
                        self.assertTrue(removed["ok"], removed)
                        self.assertEqual(removed["state"], {"ships": []})
                        keyring.clear.assert_called_once_with(self.first.url)
                        transport.assert_not_called()
                    self.assertEqual(loads(path.read_text()), {"version": 2, "ships": []})


if __name__ == "__main__":
    unittest.main()
