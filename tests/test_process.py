"""Real CLI processes; loopback Eyre and a SYNTHETIC-ONLY mock keyring."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_backend import CODE, SECRET, FakeEyre
from client.support import account


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "omarchy-urbit-theme"
NODE = shutil.which("node")
PALETTE = dict(id=PLUGIN_ID, name="Process Fixture", dark=True, primary="#ABCDEF",
               secondary="#334455", tertiary="#667788", background="#111111", surface="#222222")

# This executable is a test double, NOT a plaintext keyring implementation.
# It accepts only the exact synthetic session issued by this test's FakeEyre.
FAKE_COMMAND = r'''
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["MOCK_ROOT"])
assert not any(key.startswith("DBUS_") for key in os.environ)
assert os.environ["PATH"] == str(root / "bin")
name, args = Path(sys.argv[0]).name, sys.argv[1:]
fixtures = json.loads((root / "synthetic-session-fixture.json").read_text())
session_file = root / "MOCK-keyring-synthetic-only.json"
if name == "omarchy":
    colors = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
    assert args == ["theme", "color", "--file", str(colors), "--all"]
elif name == "secret-tool":
    action = args[0]
    assert action in ("store", "lookup", "clear")
    label = ["--label=Omarchy Urbit Theme session"] if action == "store" else []
    fixture = fixtures[args[-1]]
    assert args == [action] + label + ["application", "omarchy-urbit-theme", "origin", fixture["url"]]
else:
    raise AssertionError("Unexpected command; no real-command fallback is permitted")
with (root / "mock-calls.jsonl").open("a") as log:
    log.write(json.dumps([name] + args) + "\n")
if name == "omarchy":
    sys.stdout.write("background\t#111111\nforeground\t#eeeeee\nmode\tdark\n"
                     "accent\t#abcdef\nblue\t#334455\ngreen\t#667788\nlighter_background\t#222222\n")
elif action == "store":
    session = json.load(sys.stdin)
    assert session == fixture, "Only the synthetic fixture may be stored"
    sessions = json.loads(session_file.read_text()) if session_file.exists() else {}
    sessions[fixture["url"]] = session
    session_file.write_text(json.dumps(sessions))
    session_file.chmod(0o600)
elif action == "lookup":
    session = json.loads(session_file.read_text())[fixture["url"]]
    assert session == fixture
    sys.stdout.write(json.dumps(session))
else:
    sessions = json.loads(session_file.read_text()) if session_file.exists() else {}
    if fixture["url"] not in sessions:
        sys.exit(1)
    del sessions[fixture["url"]]
    if sessions:
        session_file.write_text(json.dumps(sessions))
    else:
        session_file.unlink(missing_ok=True)
'''


class ProcessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="urbit-theme-process-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.server = FakeEyre()
        self.addCleanup(self.server.close)
        self.other = FakeEyre("~nec")
        self.addCleanup(self.other.close)
        self.home = self.root / "home"
        current = self.home / ".local/state/omarchy/current"
        current.mkdir(parents=True)
        (current / "theme.name").write_text(PALETTE["name"] + "\n")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        for name in ("secret-tool", "omarchy"):
            shim = self.bin / name
            shim.write_text("#!" + sys.executable + "\n" + FAKE_COMMAND)
            shim.chmod(0o700)
        (self.root / "synthetic-session-fixture.json").write_text(json.dumps(
            {server.url: server.session() for server in (self.server, self.other)}))
        self.mock_session = self.root / "MOCK-keyring-synthetic-only.json"
        self.state_file = self.root / "state" / PLUGIN_ID / "state.json"
        # No inherited PATH, D-Bus, Python import path, proxy, or real HOME.
        # A missing shim fails closed: there is no system command fallback.
        self.env = dict(HOME=str(self.home), XDG_STATE_HOME=str(self.root / "state"),
                        PATH=str(self.bin), MOCK_ROOT=str(self.root), PYTHONDONTWRITEBYTECODE="1")
        self.responses = []

    def cli(self, action, *, ok=True, **value):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "client/main.py"), action],
                                input=json.dumps(value), text=True, capture_output=True,
                                cwd=self.root, env=self.env, timeout=10)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.splitlines()), 1)
        for secret in (SECRET, CODE, "urbauth-~zod"):
            self.assertNotIn(secret, result.stdout)
            if self.state_file.exists():
                self.assertNotIn(secret, self.state_file.read_text())
        response = json.loads(result.stdout)
        self.assertEqual(set(response), {"schemaVersion", "ok", "state", "palette", "error", "warning"})
        self.assertEqual(response["schemaVersion"], 2)
        self.assertIs(response["ok"], ok)
        if ok:
            self.assertIsNone(response["error"])
        self.responses.append([result.stdout, result.returncode])
        return response

    def login(self):
        response = self.cli("login", url=self.server.url, code=CODE)
        row = response["state"]["ships"][0]
        self.assertTrue(row["automatic"])
        self.assertTrue(row["pending"])
        self.assertEqual(row["lastPublished"], "")
        return {key: row[key] for key in ("id", "url", "ship")}

    def test_publish_restart_and_disconnect(self):
        preview = self.cli("preview")
        self.assertEqual(preview["palette"], PALETTE)
        self.assertEqual(self.server.requests, [])
        calls_file = self.root / "mock-calls.jsonl"
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
        self.assertEqual([call[0] for call in calls], ["omarchy"])
        self.assertFalse(self.mock_session.exists())

        expected = self.login()
        self.assertEqual(expected["url"], self.server.url)
        self.assertEqual(expected["ship"], "~zod")
        self.assertRegex(expected["id"], "^[0-9a-f]{64}$")
        self.assertEqual(json.loads(self.mock_session.read_text()), {self.server.url: self.server.session()})
        self.assertEqual([(r[0], r[1]) for r in self.server.requests], [("POST", "/~/login")])
        self.assertEqual(self.server.messages, [])

        enabled = self.cli("set-auto", enabled=True, expectedAccount=expected)
        self.assertTrue(enabled["state"]["ships"][0]["automatic"])
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.messages, [])
        synced = self.cli("sync", expectedAccount=expected)
        self.assertEqual(synced["palette"], PALETTE)
        self.assertFalse(synced["state"]["ships"][0]["pending"])
        self.assertTrue(synced["state"]["ships"][0]["lastPublished"])
        self.assertEqual(synced["state"]["ships"][0]["lastTheme"], PALETTE["name"])
        puts = [m["json"]["put-entry"] for m in self.server.messages if m["action"] == "poke"]
        self.assertEqual([p["entry-key"] for p in puts], ["themes", "accent"])
        remote = self.server.body["desk"]["ui-prefs"]
        self.assertEqual(json.loads(remote["themes"]), {"themes": [PALETTE], "activeId": PLUGIN_ID})
        self.assertEqual(json.loads(remote["accent"]), {"enabled": False})
        for put in puts:
            self.assertEqual(put["desk"], "talon")
            self.assertEqual(put["bucket-key"], "ui-prefs")
            self.assertEqual(json.loads(put["value"]), json.loads(remote[put["entry-key"]]))
        self.assertGreaterEqual(self.server.scry_count, 4)
        self.assertEqual(self.server.requests[-1][:2], ("GET", "/~/scry/settings/desk/talon.json"))

        # Every invocation is a fresh process, including this read-only restart.
        requests, calls = len(self.server.requests), calls_file.read_text()
        self.assertEqual(self.cli("status")["state"], synced["state"])
        self.assertEqual(calls_file.read_text(), calls)
        self.assertEqual(len(self.server.requests), requests)
        disk = json.loads(self.state_file.read_text())
        self.assertEqual(disk["version"], 2)
        self.assertEqual([{k: v for k, v in row.items() if k != "fingerprint"} for row in disk["ships"]],
                         synced["state"]["ships"])
        before = copy.deepcopy(self.server.body)
        disconnected = self.cli("disconnect", expectedAccount=expected)
        self.assertEqual(disconnected["state"], {"ships": []})
        self.assertIsNone(disconnected["warning"])
        self.assertFalse(self.mock_session.exists())
        self.assertEqual(self.cli("status")["state"], disconnected["state"])
        self.assertEqual(self.server.body, before)
        self.assertEqual(len(self.server.requests), requests + 1)
        self.assertEqual(self.server.requests[-1][:3], ("POST", "/~/logout", b""))

    def test_expired_session_is_safe_and_persists_without_retry(self):
        expected = self.login()
        self.cli("set-auto", enabled=True, expectedAccount=expected)
        self.server.scry_status = 401
        self.server.body = {"private-response": SECRET + CODE}
        failed = self.cli("sync", ok=False, expectedAccount=expected)
        self.assertEqual(failed["error"]["code"], "authentication")
        self.assertFalse(failed["error"]["retryable"])
        self.assertTrue(failed["state"]["ships"][0]["authenticationRequired"])
        self.assertFalse(failed["state"]["ships"][0]["automatic"])
        self.assertEqual(failed["state"]["ships"][0]["lastPublished"], "")
        self.assertEqual(self.server.scry_count, 1)
        requests = len(self.server.requests)
        self.assertEqual(self.cli("status")["state"], failed["state"])
        self.cli("sync", expectedAccount=expected)
        self.assertEqual(len(self.server.requests), requests)
        self.assertEqual(self.server.messages, [])

    def test_two_ships_shared_snapshot_independent_pause_failure_and_remove(self):
        first = self.login()
        added = self.cli("login", url=self.other.url, code=CODE)
        second = {k: added["state"]["ships"][1][k] for k in ("id", "url", "ship")}
        self.assertEqual(len(added["state"]["ships"]), 2)
        preview = self.cli("preview")["palette"]
        self.server.scry_status = 503
        failed = self.cli("sync", ok=False, expectedAccount=first, palette=preview)
        self.assertEqual(failed["state"]["ships"][1], added["state"]["ships"][1])
        synced = self.cli("sync", expectedAccount=second, palette=preview)
        self.assertEqual(synced["state"]["ships"][0], failed["state"]["ships"][0])
        self.assertEqual(synced["state"]["ships"][1]["lastTheme"], preview["name"])
        calls_file = self.root / "mock-calls.jsonl"
        calls, requests = calls_file.read_text(), len(self.other.requests)
        paused = self.cli("set-auto", enabled=False, expectedAccount=second)
        self.assertFalse(paused["state"]["ships"][1]["pending"])
        self.cli("sync", expectedAccount=second)
        self.assertEqual(calls_file.read_text(), calls)
        self.assertEqual(len(self.other.requests), requests)
        self.server.logout_status = 503
        removed = self.cli("disconnect", expectedAccount=first)
        self.assertEqual(removed["state"]["ships"], [paused["state"]["ships"][1]])
        self.assertEqual(removed["warning"]["code"], "logout-unconfirmed")
        self.assertEqual(set(json.loads(self.mock_session.read_text())), {self.other.url})
        readded = self.cli("login", url=self.server.url, code=CODE)
        self.assertNotEqual(readded["state"]["ships"][1]["id"], first["id"])
        before = len(self.server.requests)
        stale = self.cli("disconnect", ok=False, expectedAccount=first)
        self.assertEqual(stale["error"]["code"], "account-changed")
        self.assertEqual(stale["state"], readded["state"])
        self.assertEqual(len(self.server.requests), before)

    def test_missing_keyring_session_does_not_trap_removal(self):
        expected = self.login()
        self.mock_session.unlink()
        removed = self.cli("disconnect", expectedAccount=expected)
        self.assertEqual(removed["state"], {"ships": []})
        self.assertEqual(removed["warning"]["code"], "cleanup")
        self.assertEqual(self.cli("status")["state"], {"ships": []})

    def test_v1_migration_reuses_origin_keyring_without_reading_it_on_observation(self):
        expected = self.login()
        self.cli("sync", expectedAccount=expected)
        disk = json.loads(self.state_file.read_text())
        row = disk["ships"][0]
        legacy_state = {k: v for k, v in row.items() if k not in ("id", "fingerprint")}
        legacy_state["connected"] = True
        legacy_id = account(row["url"], row["ship"])
        legacy = dict(version=1, state=legacy_state, account=legacy_id, fingerprint=row["fingerprint"])
        self.state_file.write_text(json.dumps(legacy))
        raw = self.state_file.read_bytes()
        keyring = self.mock_session.read_bytes()
        calls_file = self.root / "mock-calls.jsonl"
        calls = calls_file.read_text()
        requests = len(self.server.requests)
        observed = self.cli("status")
        self.assertEqual(observed["state"]["ships"][0], dict(
            {k: v for k, v in row.items() if k != "fingerprint"}, id=legacy_id))
        self.assertEqual(calls_file.read_text(), calls)
        self.cli("preview")
        extra = [json.loads(line) for line in calls_file.read_text()[len(calls):].splitlines()]
        self.assertEqual([call[0] for call in extra], ["omarchy"])
        self.assertEqual(self.state_file.read_bytes(), raw)
        self.assertEqual(self.mock_session.read_bytes(), keyring)
        self.assertEqual(len(self.server.requests), requests)
        expected["id"] = legacy_id
        synced = self.cli("sync", expectedAccount=expected, palette=PALETTE)
        self.assertEqual(synced["state"], observed["state"])
        self.assertEqual(json.loads(self.state_file.read_text())["version"], 2)
        self.assertEqual(self.mock_session.read_bytes(), keyring)
        self.assertEqual(len(self.server.requests), requests + 1)

    @unittest.skipUnless(NODE, "Node is optional; required only for the Model.js boundary test")
    def test_actual_cli_responses_are_accepted_by_model(self):
        self.cli("preview")
        expected = self.login()
        self.cli("sync", force=True, expectedAccount=expected)
        self.server.scry_status = 401
        self.cli("sync", ok=False, force=True, expectedAccount=expected)
        self.server.logout_status = 503
        self.cli("disconnect", expectedAccount=expected)
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const Model = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), Model);
for (const [raw, exitCode] of JSON.parse(fs.readFileSync(0, "utf8"))) {
    const expected = JSON.parse(raw);
    for (const row of expected.state.ships)
        if (row.lastError) row.lastError = "Sync failed. Pause then resume to retry.";
    if (expected.error) expected.error.message = Model.errorText(expected.error.code);
    if (expected.warning) expected.warning.message = Model.errorText(expected.warning.code);
    const actual = JSON.parse(JSON.stringify(Model.parseResponse(raw, exitCode)));
    assert.deepEqual(actual, expected);
}
'''
        result = subprocess.run([NODE, "-e", script, str(ROOT / "Model.js")],
                                input=json.dumps(self.responses), text=True, capture_output=True,
                                cwd=self.root, env=self.env, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
