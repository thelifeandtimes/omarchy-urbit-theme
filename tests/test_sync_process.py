"""Two complete sync processes with isolated homes and inert desktop/keyring commands."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from client.profile import colors_toml, shell_toml
from test_profile import profile
from test_sync import LiveHub

ROOT = Path(__file__).resolve().parents[1]

# No real command fallback, no D-Bus, no inherited desktop or real credential.
SHIM = r'''
import json, os, sys
from pathlib import Path
root = Path(os.environ["MOCK_ROOT"])
assert os.environ["PATH"] == str(root / "bin")
assert not any(k.startswith("DBUS_") for k in os.environ)
home = Path.home()
name, args = Path(sys.argv[0]).name, sys.argv[1:]
runtime = root / "runtime.json"
p = json.loads(runtime.read_text())
def save():
    temp = root / "runtime.next"
    temp.write_text(json.dumps(p))
    temp.replace(runtime)
if name == "secret-tool":
    key = root / "SYNTHETIC-ONLY-keyring.json"
    if args[0] == "store":
        v = json.load(sys.stdin)
        assert v["cookieValue"] == "synthetic" and v["url"].startswith("http://127.0.0.1:")
        key.write_text(json.dumps(v))
    elif args[0] == "lookup":
        print(key.read_text())
    elif args[0] == "clear":
        key.unlink()
    else: raise AssertionError(args)
elif name == "fc-list":
    print("Fixture Mono,FixtureMono")
elif name == "omarchy-shell":
    assert args[:2] == ["shell", "applyTheme"]
elif name == "omarchy":
    if args[:2] == ["theme", "color"]:
        print("\n".join(k + "\t" + v for k,v in p["colors"].items()))
    elif args == ["font", "current"]:
        print(p["font"])
    elif args[:2] == ["font", "set"]:
        assert os.environ["OMARCHY_URBIT_THEME_REMOTE"] == "1"
        p["font"] = args[2]; save()
    elif args == ["theme", "set", "urbit-synced"]:
        assert os.environ["OMARCHY_URBIT_THEME_REMOTE"] == "1"
        desired = json.loads((Path(os.environ["XDG_STATE_HOME"]) / "omarchy-urbit-theme/desktop.json").read_text())["applying"]
        assert desired is not None
        generated = home / ".config/omarchy/themes/urbit-synced"
        assert (generated / "colors.toml").is_file() and (generated / "shell.toml").is_file()
        p = desired; save()
        current = home / ".local/state/omarchy/current"
        (current / "theme.name").write_text("urbit-synced\n")
        for f in ["colors.toml", "shell.toml"] + list(desired["files"]):
            (current / "theme" / f).write_bytes((generated / f).read_bytes())
    else: raise AssertionError(args)
elif name == "hyprctl":
    if args[0] == "--batch":
        for request in args[1].split(";"):
            option = request.removeprefix("j/getoption ")
            if option in p["windows"]:
                print(json.dumps(dict(option=option, **p["windows"][option], set=True)) + "\n\n")
            else: print("no such option\n\n")
    elif args == ["-j", "animations"]:
        rules = [dict(r, overridden=True) for r in p["animations"]["rules"]]
        curves = [dict(name=c["name"], **dict(zip(["X0","Y0","X1","Y1"], c["points"]))) for c in p["animations"]["curves"]]
        print(json.dumps([rules,curves]))
    elif args == ["-j", "configerrors"]: print("[]")
    elif args == ["reload"]: pass
    else: raise AssertionError(args)
else: raise AssertionError("No real-command fallback: " + name)
'''


class Machine:
    def __init__(self, root, initial, url):
        self.root, self.home = root, root / "home"
        current = self.home / ".local/state/omarchy/current"
        (current / "theme").mkdir(parents=True)
        (current / "theme.name").write_text(initial["theme"] + "\n")
        (current / "theme/colors.toml").write_text(colors_toml(initial["colors"]))
        (current / "theme/shell.toml").write_text(shell_toml(initial["shell"]))
        for name, value in initial["files"].items():
            (current / "theme" / name).write_text(value)
        for theme in ("tokyo-night", "catppuccin"):
            (self.home / ".config/omarchy/themes" / theme).mkdir(parents=True)
        config = self.home / ".config/hypr/hyprland.lua"
        config.parent.mkdir(parents=True)
        config.write_text("-- fixture\n")
        (root / "bin").mkdir()
        (root / "run").mkdir()
        self.runtime = root / "runtime.json"
        self.runtime.write_text(json.dumps(initial))
        for name in ("omarchy", "omarchy-shell", "hyprctl", "fc-list", "secret-tool"):
            path = root / "bin" / name
            path.write_text("#!" + sys.executable + "\n" + SHIM)
            path.chmod(0o700)
        self.env = dict(HOME=str(self.home), PATH=str(root / "bin"), MOCK_ROOT=str(root),
                        XDG_STATE_HOME=str(root / "state"), XDG_RUNTIME_DIR=str(root / "run"),
                        OMARCHY_PATH=str(root / "stock"), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "client/main.py"), "login"],
                                input=json.dumps(dict(url=url, code="sampel-code")), text=True,
                                capture_output=True, env=self.env, timeout=10)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        self.row = json.loads(result.stdout)["state"]["ships"][0]
        self.messages = []
        self.process = None

    def start(self):
        self.process = subprocess.Popen([sys.executable, "-B", str(ROOT / "client/sync.py"), "serve"],
                                        env=self.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True)
        def reader():
            for line in self.process.stdout:
                self.messages.append(json.loads(line))
        self.reader = threading.Thread(target=reader)
        self.reader.start()

    def send(self, action):
        self.process.stdin.write(json.dumps({"action": action}) + "\n")
        self.process.stdin.flush()

    def state(self):
        path = self.root / "state/omarchy-urbit-theme/desktop.json"
        return json.loads(path.read_text()) if path.exists() else {}

    def stop(self):
        if self.process:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
            self.reader.join(2)
            stderr = self.process.stderr.read()
            self.process.stdout.close()
            self.process.stderr.close()
            self.process = None
            if stderr:
                raise AssertionError(stderr)


class ProcessSyncTests(unittest.TestCase):
    def wait_for(self, predicate, *machines):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if predicate():
                return
            for m in machines:
                self.assertIsNone(m.process.poll(), m.messages)
            time.sleep(0.1)
        self.fail("Sync timeout: " + repr([m.messages for m in machines]))

    def test_login_join_both_directions_pause_restart_and_talon_bridge(self):
        server = LiveHub()
        self.addCleanup(server.close)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        a = Machine(root / "a", profile(), server.url)
        b = Machine(root / "b", profile("catppuccin", "#445566"), server.url)
        self.addCleanup(a.stop)
        self.addCleanup(b.stop)
        a.start()
        self.wait_for(lambda: bool(a.state().get("observed")), a)
        first = copy.deepcopy(server.value)
        b.start()
        self.wait_for(lambda: (b.state().get("observed") or {}).get("updateId") == first["updateId"], a, b)
        self.assertEqual(server.value["theme"], "tokyo-night")
        self.assertEqual(json.loads(b.runtime.read_text())["colors"]["background"], "#112233")
        self.wait_for(lambda: "themes" in server.talon["desk"]["ui-prefs"], a, b)
        themes = json.loads(server.talon["desk"]["ui-prefs"]["themes"])
        self.assertEqual(themes["themes"][0]["text"], "#EEEEEE")
        self.assertEqual(themes["themes"][0]["error"], "#EE3333")

        changed = json.loads(b.runtime.read_text())
        changed["colors"]["background"] = "#ABCDEF"
        changed["windows"]["decoration:rounding"] = {"int": 14}
        b.runtime.write_text(json.dumps(changed))
        self.wait_for(lambda: (a.state().get("observed") or {}).get("colors", {}).get("background") == "#ABCDEF", a, b)
        self.assertEqual(json.loads(a.runtime.read_text())["windows"]["decoration:rounding"], {"int": 14})

        b.send("pause")
        self.wait_for(lambda: b.state().get("paused"), a, b)
        changed = json.loads(a.runtime.read_text())
        changed["colors"]["background"] = "#654321"
        a.runtime.write_text(json.dumps(changed))
        self.wait_for(lambda: server.value["colors"]["background"] == "#654321", a, b)
        self.assertEqual(json.loads(b.runtime.read_text())["colors"]["background"], "#ABCDEF")
        b.send("resume")
        self.wait_for(lambda: (b.state().get("observed") or {}).get("colors", {}).get("background") == "#654321", a, b)
        final = server.value["updateId"]
        b.stop()
        b.start()
        self.wait_for(lambda: b.messages[-1]["status"] == "Following shared appearance", a, b)
        time.sleep(1)
        self.assertEqual(server.value["updateId"], final)
        for machine in (a, b):
            self.assertNotIn("synthetic", json.dumps(machine.messages))
            self.assertNotIn("synthetic", json.dumps(machine.state()))


if __name__ == "__main__":
    unittest.main()
