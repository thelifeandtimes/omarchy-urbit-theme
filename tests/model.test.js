const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const { spawnSync } = require("node:child_process");

const root = path.join(__dirname, "..");
const M = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(root, "Model.js"), "utf8"), M);
const plain = value => JSON.parse(JSON.stringify(value));
const state = (extra = {}) => ({ ...M.emptyState(), ...extra });
const connected = (extra = {}) => state({ connected: true, ship: "~zod", url: "https://ship.example", ...extra });
const auto = (extra = {}) => connected({ automatic: true, ...extra });
const palette = { id: "omarchy-urbit-theme", name: "Test", dark: true,
  primary: "#123456", secondary: "#abcdef", tertiary: "#987654", background: "#010203", surface: "#040506" };
const ok = (s = state(), p = null) => ({ schemaVersion: 1, ok: true, state: s, palette: p, error: null });
const error = (s, retryable = true, code = "network") => ({ ...ok(s), ok: false,
  error: { code, message: "Safe helper error", retryable } });
const parse = (value, exit = value.ok ? 0 : 1) => M.parseResponse(JSON.stringify(value), exit);
function settled(s = state()) {
  let q = M.next(M.initialQueue(), s);
  q = M.complete(q, ok(s), s);
  q = M.next(q, s);
  return M.complete(q, ok(s, palette), s);
}
function finish(q, response) { return M.complete(q, response, response.state); }

test("only approved state and palette fields survive the protocol boundary", () => {
  const response = parse(ok({ ...connected(), code: "never-copy", session: "never-copy" }, { ...palette, cookie: "never-copy" }));
  assert.equal(response.ok, true);
  assert.deepEqual(plain(response.state), connected());
  assert.deepEqual(plain(response.palette), palette);
  assert.ok(!JSON.stringify(response).includes("never-copy"));
  const bad = error(connected(), false, "unknown");
  bad.error.message = "never-copy";
  bad.state.lastError = "never-copy";
  assert.ok(!JSON.stringify(parse(bad)).includes("never-copy"));
});

test("malformed, oversized, mismatched-exit and unsafe output is rejected without echoing it", () => {
  for (const raw of ["not-json", "{}", "[]", "null", "\"secret\"", JSON.stringify(ok()) + JSON.stringify(ok())]) {
    assert.equal(M.parseResponse(raw, 0).error.code, "invalid_response");
  }
  assert.equal(M.parseResponse("x".repeat(M.maxOutput + 1), 0).error.code, "output_limit");
  for (const change of [
    v => v.schemaVersion = "1", v => v.ok = "true", v => delete v.palette,
    v => v.state.connected = 1, v => v.state.url = "https://user:secret@ship.example",
    v => v.state.url = "https://ship.example?code=secret", v => v.state.ship = "<b>ship</b>",
    v => v.state.lastTheme = "line\nbreak", v => v.state.lastPublished = "yesterday",
    v => v.palette.primary = "red", v => v.palette.dark = "true", v => v.palette.id = "other"
  ]) {
    const value = ok(connected(), { ...palette });
    change(value);
    assert.equal(parse(value).error.code, "invalid_response");
  }
  assert.equal(parse(ok(), 1).error.code, "invalid_response");
  assert.equal(parse(error(state()), 0).error.code, "invalid_response");
  assert.equal(parse(error(auto(), true, "ack-timeout")).error.code, "ack-timeout");
});

test("file URLs decode once, preserve spaces and escaped delimiters, reject remote hosts", () => {
  assert.equal(M.localPath("file:///tmp/a%20b/%23%25/client/main.py"), "/tmp/a b/#%/client/main.py");
  assert.equal(M.localPath("file://localhost/tmp/client/main.py"), "/tmp/client/main.py");
  assert.equal(M.localPath("file:///tmp/a%2520b/main.py"), "/tmp/a%20b/main.py");
  for (const value of ["file://evil/tmp/main.py", "https://host/main.py", "file:///tmp/%00main.py", "file:///tmp/%zz", "file:///tmp/main.py?x", "file:///tmp/main.py#x"]) {
    assert.equal(M.localPath(value), "");
  }
});

test("startup reconciles exactly once; repeated status and preview never trigger writes", () => {
  let q = settled(auto());
  assert.equal(q.sync, true);
  q = M.next(q, auto());
  assert.equal(q.running, "sync");
  q = finish(q, ok(auto()));
  for (let i = 0; i < 5; i++) {
    q = M.request(q, "refresh", auto());
    q = finish(M.next(q, auto()), ok(auto()));
    q = finish(M.next(q, auto()), ok(auto(), palette));
    assert.equal(M.next(q, auto()).running, "");
  }
  assert.equal(settled(connected()).sync, false);
  assert.equal(settled(auto({ authenticationRequired: true })).sync, false);
});

test("first successful loaded state still reconciles after startup status failure", () => {
  let q = M.next(M.initialQueue(), state());
  q = M.complete(q, M.failure("helper_unavailable"), state());
  assert.equal(q.startup, true);
  q = finish(M.next(q, state()), ok(auto(), palette));
  assert.equal(q.startup, false);
  assert.equal(q.sync, true);
});

test("theme events coalesce, debounce and refresh the preview even while paused", () => {
  let q = settled(connected());
  for (let i = 0; i < 10; i++) q = M.request(q, "theme", connected());
  assert.equal(q.generation, 10);
  assert.equal(M.next(q, connected()).running, "");
  q.debouncing = false;
  q = M.next(q, connected());
  assert.equal(q.running, "preview");
  q = finish(q, ok(connected(), palette));
  assert.equal(M.next(q, connected()).running, "");
});

test("single-flight: latest theme supersedes old retry and is the only queued sync", () => {
  let q = M.next(settled(auto()), auto());
  q = M.request(q, "theme", auto());
  q = M.request(q, "theme", auto());
  assert.equal(M.next(q, auto()).running, "sync");
  q = finish(q, error(auto({ pending: true })));
  assert.equal(q.retryDelay, 0);
  q.debouncing = false;
  q = M.next(q, auto());
  assert.equal(q.running, "preview");
  q = finish(q, ok(auto(), palette));
  q = M.next(q, auto());
  assert.equal(q.running, "sync");
  q = finish(q, ok(auto()));
  assert.equal(M.next(q, auto()).running, "");
});

test("automatic pending retry is bounded, and permanent/auth/paused failures never retry", () => {
  let q = M.next(settled(auto()), auto());
  for (const delay of [2000, 5000, 15000]) {
    q = finish(q, error(auto({ pending: true })));
    assert.equal(q.retryDelay, delay);
    assert.equal(M.next(q, auto()).running, "");
    q.retryDelay = 0;
    q = M.next(q, auto());
    assert.equal(q.running, "sync");
  }
  q = finish(q, error(auto({ pending: true })));
  assert.equal(q.retryDelay, 0);
  assert.equal(M.next(q, auto()).running, "");
  for (const response of [error(auto({ pending: true }), false), error(auto()),
    error(auto({ pending: true, authenticationRequired: true })), error(connected({ pending: true }))]) {
    q = finish(M.next(settled(auto()), auto()), response);
    assert.equal(q.retryDelay, 0);
    assert.equal(q.sync, false);
  }
});

test("a manual publication of the latest theme also satisfies coalesced automatic work", () => {
  let q = M.request(settled(auto()), "publish", auto());
  q = M.request(q, "theme", auto());
  q.debouncing = false;
  q = finish(M.next(q, auto()), ok(auto(), palette));
  q = M.next(q, auto());
  assert.equal(q.running, "publish");
  q = finish(q, ok(auto(), palette));
  assert.equal(M.next(q, auto()).running, "");
});

test("pause/disconnect take priority after running sync, cancel force, themes and retries", () => {
  for (const control of ["pause", "disconnect"]) {
    let q = M.next(settled(auto()), auto());
    q = M.request(q, "theme", auto());
    q = M.request(q, control, auto());
    assert.equal(q.running, "sync");
    assert.equal(q.control, control);
    assert.equal(q.sync, false);
    q = finish(q, error(auto({ pending: true })));
    assert.equal(q.retryDelay, 0);
    q = M.request(q, "theme", auto());
    assert.equal(q.sync, false);
    q = M.next(q, auto());
    assert.equal(q.running, control);
    q = finish(q, ok(control === "pause" ? connected() : state()));
    assert.equal(q.sync, false);
    assert.equal(q.force, false);
  }
  let q = M.request(settled(connected()), "publish", connected());
  q = M.request(q, "pause", connected());
  assert.equal(q.force, false);
  q = M.request(q, "disconnect", connected());
  q = M.request(q, "pause", connected());
  assert.equal(q.control, "disconnect");
});

test("login is never queued; enable waits for consent mutation before scheduling sync", () => {
  let q = settled();
  assert.equal(M.idle(q), true);
  const unchanged = JSON.stringify(q);
  q = M.request(q, "login", state());
  assert.equal(JSON.stringify(q), unchanged);
  assert.equal(M.idle(M.request(q, "refresh", state())), false);
  q = M.request(settled(connected()), "enable", connected());
  assert.equal(q.sync, false);
  q = M.next(q, connected());
  assert.equal(q.running, "enable");
  q = finish(q, ok(auto()));
  assert.equal(q.sync, true);
  q = M.request(q, "disconnect", auto());
  q = M.request(q, "enable", auto());
  assert.equal(q.control, "disconnect");
});

test("observation preserves meaningful errors until a successful mutation", () => {
  for (const action of ["status", "preview"]) {
    assert.equal(M.meaningfulError("mutation failed", action, ok()), "mutation failed");
    assert.equal(M.meaningfulError("mutation failed", action, error(state())), "mutation failed");
  }
  assert.equal(M.meaningfulError("mutation failed", "sync", ok()), "");
});

test("failed responses may omit trusted state with explicit null, never success", () => {
  const response = parse(error(null, true, "busy"));
  assert.equal(response.state, null);
  assert.equal(response.error.code, "busy");
  assert.equal(response.error.retryable, true);
  assert.equal(parse(ok(null)).error.code, "invalid_response");
  const missing = error(null);
  delete missing.state;
  assert.equal(parse(missing).error.code, "invalid_response");
  assert.equal(M.safeUrl("http://[::ffff:127.0.0.1]:8080"), true);
  assert.equal(parse(ok(connected({ url: "http://[::ffff:127.0.0.1]:8080" }))).ok, true);
});

test("mutation identity is captured at intent, cannot cross either ship or URL", () => {
  const a = auto();
  for (const b of [auto({ ship: "~nec" }), auto({ url: "https://other.example" }), state()]) {
    for (const action of ["publish", "enable", "pause", "disconnect", "theme"]) {
      let q = settled(a);
      q.sync = false;
      q = M.request(q, action, a);
      assert.deepEqual(plain(q.intentAccount), { url: a.url, ship: a.ship });
      q = M.next(q, b);
      assert.ok(!["publish", "enable", "pause", "disconnect", "sync"].includes(q.running));
      assert.equal(q.force, false);
      assert.equal(q.control, "");
      assert.equal(q.sync, false);
    }
  }
  let q = M.request(settled(connected()), "publish", connected());
  const captured = q.intentAccount;
  q = M.next(q, connected());
  assert.equal(q.runAccount, captured);
});

test("status and preview identity transitions cancel pending mutations and retries", () => {
  const a = auto(), b = auto({ ship: "~nec" });
  for (const observation of ["status", "preview"]) {
    for (const action of ["publish", "enable", "pause", "disconnect", "theme"]) {
      let q = M.request(settled(a), action, a);
      q.running = observation;
      q = finish(q, ok(b));
      assert.equal(q.control, "");
      assert.equal(q.force, false);
      assert.equal(q.sync, false);
      assert.equal(q.retryDelay, 0);
      assert.equal(q.intentAccount, null);
    }
    let q = finish(M.next(settled(a), a), error(auto({ pending: true })));
    q.running = observation;
    q = finish(q, ok(b));
    assert.equal(q.retryDelay, 0);
    assert.equal(q.sync, false);
  }
});

test("account-changed rejects even without state and never retries or enables B", () => {
  for (const result of [error(null, true, "account-changed"), error(auto({ ship: "~nec" }), true, "account-changed")]) {
    let q = M.next(settled(auto()), auto());
    q = M.request(q, "disconnect", auto());
    q = M.complete(q, result, result.state || auto());
    assert.equal(q.control, "");
    assert.equal(q.sync, false);
    assert.equal(q.retryDelay, 0);
  }
  let q = M.next(M.request(settled(connected()), "enable", connected()), connected());
  q = finish(q, ok(auto({ ship: "~nec" })));
  assert.equal(q.sync, false);
});

test("transient palette preflight and null-state lock errors retry with bound identity", () => {
  for (const result of [error(auto(), true, "palette"), error(null, true, "busy")]) {
    let q = M.next(settled(auto()), auto());
    for (const delay of [2000, 5000, 15000]) {
      q = M.complete(q, result, result.state || auto());
      assert.equal(q.retryDelay, delay);
      assert.deepEqual(plain(q.intentAccount), { ship: "~zod", url: "https://ship.example" });
      q.retryDelay = 0;
      q = M.next(q, auto());
      assert.equal(q.running, "sync");
    }
    q = M.complete(q, result, result.state || auto());
    assert.equal(q.retryDelay, 0);
    assert.equal(q.sync, false);
  }
  let q = M.next(settled(auto()), auto());
  q = finish(q, error(auto({ pending: true }), true, "authentication"));
  assert.equal(q.sync, false);
});

test("startup lock contention retries boundedly and successful load reconciles once", () => {
  let q = M.next(M.initialQueue(), state());
  q.preview = false;
  for (const delay of [2000, 5000, 15000]) {
    q = M.complete(q, error(null, true, "busy"), state());
    assert.equal(q.startup, true);
    assert.equal(q.retryDelay, delay);
    assert.equal(M.next(q, state()).running, "");
    q.retryDelay = 0;
    q = M.next(q, state());
    assert.equal(q.running, "status");
  }
  const exhausted = M.complete(q, error(null, true, "busy"), state());
  assert.equal(exhausted.retryDelay, 0);
  assert.equal(exhausted.status, false);
  q = finish(q, ok(auto()));
  assert.equal(q.startup, false);
  assert.equal(q.sync, true);
  q = finish(M.next(q, auto()), ok(auto()));
  assert.equal(q.sync, false);
});

test("root manifest and secret-transport invariants", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8"));
  assert.equal(manifest.id, "thelifeandtimes.urbit-theme");
  assert.equal(manifest.keepLoaded, true);
  assert.deepEqual(manifest.kinds, ["service", "bar-widget"]);
  assert.deepEqual(manifest.entryPoints, { service: "Service.qml", barWidget: "Panel.qml" });
  const service = fs.readFileSync(path.join(root, "Service.qml"), "utf8");
  assert.match(service, /write\(root.pendingInput\)\s+root.pendingInput = ""\s+stdinEnabled = false/);
  assert.match(service, /\["python3", "-B", path, action\]/);
  assert.doesNotMatch(service, /\.end\(|console\.|StdioCollector|FileView|Connections\s*\{/);
  const panel = fs.readFileSync(path.join(root, "Panel.qml"), "utf8");
  assert.match(panel, /codeField.text = ""/);
  assert.match(panel, /Qt.ImhSensitiveData/);
  assert.match(panel, /passwordMaskDelay: 0/);
  assert.doesNotMatch(panel, /required property var service/);
});

test("missing QML prerequisites fail unless an explicit skip flag is supplied", () => {
  const runner = path.join(root, "tests/qml/run.js");
  const env = { ...process.env, OMARCHY_SHELL_SOURCE: path.join(root, "tests/absent-omarchy-source") };
  const failed = spawnSync(process.execPath, [runner], { env, encoding: "utf8" });
  assert.equal(failed.status, 1);
  assert.match(failed.stderr, /FAIL: QML prerequisites unavailable/);
  const skipped = spawnSync(process.execPath, [runner, "--allow-skip"], { env, encoding: "utf8" });
  assert.equal(skipped.status, 0);
  assert.match(skipped.stderr, /SKIP: QML prerequisites unavailable/);
  assert.match(skipped.stderr, /No QML, real SDK, or hook IPC checks ran/);
  assert.doesNotMatch(skipped.stdout, /passed/i);
});
