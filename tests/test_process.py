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
fixture = json.loads((root / "synthetic-session-fixture.json").read_text())
session_file = root / "MOCK-keyring-synthetic-only.json"
if name == "omarchy":
    colors = Path.home() / ".local/state/omarchy/current/theme/colors.toml"
    assert args == ["theme", "color", "--file", str(colors), "--all"]
elif name == "secret-tool":
    action = args[0]
    assert action in ("store", "lookup", "clear")
    label = ["--label=Omarchy Urbit Theme session"] if action == "store" else []
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
    session_file.write_text(json.dumps(session))
    session_file.chmod(0o600)
elif action == "lookup":
    session = json.loads(session_file.read_text())
    assert session == fixture
    sys.stdout.write(json.dumps(session))
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
        (self.root / "synthetic-session-fixture.json").write_text(json.dumps(self.server.session()))
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
        self.assertEqual(set(response), {"schemaVersion", "ok", "state", "palette", "error"})
        self.assertEqual(response["schemaVersion"], 1)
        self.assertIs(response["ok"], ok)
        if ok:
            self.assertIsNone(response["error"])
        self.responses.append([result.stdout, result.returncode])
        return response

    def login(self):
        response = self.cli("login", url=self.server.url, code=CODE)
        self.assertTrue(response["state"]["connected"])
        self.assertFalse(response["state"]["automatic"])
        self.assertEqual(response["state"]["lastPublished"], "")
        return {key: response["state"][key] for key in ("url", "ship")}

    def test_publish_restart_and_disconnect(self):
        preview = self.cli("preview")
        self.assertEqual(preview["palette"], PALETTE)
        self.assertEqual(self.server.requests, [])
        calls_file = self.root / "mock-calls.jsonl"
        calls = [json.loads(line) for line in calls_file.read_text().splitlines()]
        self.assertEqual([call[0] for call in calls], ["omarchy"])
        self.assertFalse(self.mock_session.exists())

        expected = self.login()
        self.assertEqual(expected, {"url": self.server.url, "ship": "~zod"})
        self.assertEqual(json.loads(self.mock_session.read_text()), self.server.session())
        self.assertEqual([(r[0], r[1]) for r in self.server.requests], [("POST", "/~/login")])
        self.assertEqual(self.server.messages, [])

        enabled = self.cli("set-auto", enabled=True, expectedAccount=expected)
        self.assertTrue(enabled["state"]["automatic"])
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.server.messages, [])
        synced = self.cli("sync", expectedAccount=expected)
        self.assertEqual(synced["palette"], PALETTE)
        self.assertFalse(synced["state"]["pending"])
        self.assertTrue(synced["state"]["lastPublished"])
        self.assertEqual(synced["state"]["lastTheme"], PALETTE["name"])
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
        self.assertEqual(json.loads(self.state_file.read_text())["state"], synced["state"])
        before = copy.deepcopy(self.server.body)
        disconnected = self.cli("disconnect", expectedAccount=expected)
        self.assertFalse(disconnected["state"]["connected"])
        self.assertFalse(disconnected["state"]["automatic"])
        self.assertFalse(self.mock_session.exists())
        self.assertEqual(self.cli("status")["state"], disconnected["state"])
        self.assertEqual(self.server.body, before)
        self.assertEqual(len(self.server.requests), requests)

    def test_expired_session_is_safe_and_persists_without_retry(self):
        expected = self.login()
        self.cli("set-auto", enabled=True, expectedAccount=expected)
        self.server.scry_status = 401
        self.server.body = {"private-response": SECRET + CODE}
        failed = self.cli("sync", ok=False, expectedAccount=expected)
        self.assertEqual(failed["error"]["code"], "authentication")
        self.assertFalse(failed["error"]["retryable"])
        self.assertTrue(failed["state"]["authenticationRequired"])
        self.assertEqual(failed["state"]["lastPublished"], "")
        self.assertEqual(self.server.scry_count, 1)
        requests = len(self.server.requests)
        self.assertEqual(self.cli("status")["state"], failed["state"])
        self.cli("sync", ok=False, expectedAccount=expected)
        self.assertEqual(len(self.server.requests), requests)
        self.assertEqual(self.server.messages, [])

    @unittest.skipUnless(NODE, "Node is optional; required only for the Model.js boundary test")
    def test_actual_cli_responses_are_accepted_by_model(self):
        self.cli("preview")
        expected = self.login()
        self.cli("sync", force=True, expectedAccount=expected)
        self.server.scry_status = 401
        self.cli("sync", ok=False, force=True, expectedAccount=expected)
        script = r'''
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const Model = vm.createContext({});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), Model);
for (const [raw, exitCode] of JSON.parse(fs.readFileSync(0, "utf8"))) {
    const expected = JSON.parse(raw);
    if (expected.state.lastError)
        expected.state.lastError = "The previous publication failed. Publish Now to try again.";
    if (expected.error) expected.error.message = Model.errorText(expected.error.code);
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
