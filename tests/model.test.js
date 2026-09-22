const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const M = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, "../Model.js"), "utf8"), M);
const plain = value => JSON.parse(JSON.stringify(value));
const row = (id = "a", extra = {}) => ({ id: id.repeat(64), ship: id === "a" ? "~zod" : "~nec",
  url: `https://${id}.example`, automatic: true, pending: false, authenticationRequired: false,
  lastPublished: "", lastTheme: "", lastError: "", ...extra });
const a = row(), b = row("b"), c = row("c");
const state = (...ships) => ({ ships });
const palette = { id: "omarchy-urbit-theme", name: "Test", dark: true,
  primary: "#123456", secondary: "#abcdef", tertiary: "#987654", background: "#010203", surface: "#040506" };
const ok = (s = state(), p = null) => ({ schemaVersion: 2, ok: true, state: s, palette: p, error: null, warning: null });
const error = (s, code = "network", retryable = true) => ({ ...ok(s), ok: false,
  error: { code, message: "Never display arbitrary helper text", retryable } });
const parse = (v, exit = v.ok ? 0 : 1) => M.parseResponse(JSON.stringify(v), exit);
function settled(s) {
  let q = M.next(M.initialQueue(), s, 0);
  q = M.complete(q, ok(s), s, 0);
  q = M.next(q, s, 0);
  return M.complete(q, ok(s, palette), s, 0);
}
function finish(q, s, response = ok(s), now = 0) { return M.complete(q, response, s, now); }

test("v2 whitelist, null observations, warnings and bounded 64-row responses", () => {
  const v = ok(state({ ...a, code: "secret" }), { ...palette, cookie: "secret" });
  assert.deepEqual(plain(parse(v).state), state(a));
  assert.deepEqual(plain(parse(v).palette), palette);
  assert.ok(!JSON.stringify(parse(v)).includes("secret"));
  assert.equal(parse(ok(null)).state, null);
  assert.equal(parse(error(null)).state, null);
  v.warning = { code: "network", message: "secret", retryable: false };
  assert.equal(parse(v).ok, true);
  assert.equal(parse(v).warning.message, M.errorText("network"));
  const large = state(...Array.from({ length: 64 }, (_, i) => row("a", {
    id: i.toString(16).padStart(64, "0"), url: `https://${i}.example`, lastError: "x".repeat(512) })));
  assert.equal(parse(ok(large)).state.ships.length, 64);
  large.ships.push(b);
  assert.equal(parse(ok(large)).error.code, "invalid_response");
});

test("reject v1, malformed state, unsafe text, missing warning, mismatched exit and oversized output", () => {
  for (const change of [
    v => v.schemaVersion = 1, v => delete v.warning, v => delete v.palette, v => delete v.state,
    v => v.state.ships[0].id = "bad", v => v.state.ships.push(a),
    v => v.state.ships[0].url = "https://user:secret@ship.example",
    v => v.state.ships[0].url += "?code=secret", v => v.state.ships[0].ship = "<b>ship</b>",
    v => v.state.ships[0].automatic = 1, v => v.state.ships[0].lastTheme = "line\nbreak",
    v => v.state.ships[0].lastPublished = "yesterday", v => v.palette.primary = "red",
    v => v.palette.dark = "true", v => v.palette.id = "other", v => v.warning = {}
  ]) {
    const value = ok(state({ ...a }), { ...palette });
    change(value);
    assert.equal(parse(value).error.code, "invalid_response");
  }
  for (const raw of ["not-json", "{}", "[]", "null", JSON.stringify(ok()) + JSON.stringify(ok())])
    assert.equal(M.parseResponse(raw, 0).error.code, "invalid_response");
  assert.equal(parse(ok(), 1).error.code, "invalid_response");
  assert.equal(parse(error(null), 0).error.code, "invalid_response");
  assert.equal(M.parseResponse("x".repeat(M.maxOutput + 1), 0).error.code, "output_limit");
});

test("startup fans out one exact palette snapshot and failure never starves later ships", () => {
  const s = state(a, b, c);
  let q = settled(s);
  assert.equal(q.jobs.length, 3);
  for (const j of q.jobs) assert.equal(j.palette, palette);
  q = M.next(q, s, 100);
  assert.deepEqual(plain(q.run.expectedAccount), plain(M.identity(a)));
  q = finish(q, s, error(s), 100);
  assert.equal(q.jobs[2].due, 2100);
  q = M.next(q, s, 101);
  assert.equal(q.run.expectedAccount.id, b.id);
  q = finish(q, s);
  q = M.next(q, s, 102);
  assert.equal(q.run.expectedAccount.id, c.id);
  q = finish(q, s);
  assert.equal(M.next(q, s, 2099).running, "");
  assert.equal(M.retryDelay(q, 102), 1998);
  assert.equal(M.next(q, s, 2100).run.expectedAccount.id, a.id);
});

test("per-ship deadlines are independent, ready retries skip earlier sleeping jobs, exhaustion stops", () => {
  const s = state(a, b);
  let q = settled(s);
  q = finish(M.next(q, s, 0), s, error(s), 0);
  q = finish(M.next(q, s, 0), s, error(s), 1000);
  q = finish(M.next(q, s, 2000), s, error(s), 2000);
  assert.deepEqual(plain(q.jobs.map(j => j.due)), [3000, 7000]);
  q = finish(M.next(q, s, 3000), s, error(s), 3000);
  // Deliberately put the later deadline first: FIFO must not block ready work.
  q.jobs.reverse();
  assert.equal(M.next(q, s, 7000).run.expectedAccount.id, a.id);
  let count = 0;
  while (q.jobs.length) {
    const now = Math.min(...q.jobs.map(j => j.due));
    q = finish(M.next(q, s, now), s, error(s), now);
    assert.ok(++count < 10);
  }
  assert.ok(M.idle(q));
  q = M.request(q, "refresh", s);
  q = finish(M.next(q, s, 99999), s);
  q = finish(M.next(q, s, 99999), s, ok(s, palette));
  assert.ok(M.idle(q), "passive observations cannot restart exhausted syncs");
});

test("remove another row while one is in flight cancels only its work and controls run first", () => {
  const s = state(a, b, c);
  let q = M.next(settled(s), s, 0);
  q = M.request(q, "disconnect", s, b);
  assert.equal(q.running, "sync");
  assert.equal(q.jobs.length, 1);
  assert.equal(q.jobs[0].expectedAccount.id, c.id);
  q = finish(q, s, error(s), 0);
  q = M.next(q, s, 0);
  assert.equal(q.running, "disconnect");
  assert.deepEqual(plain(q.run.expectedAccount), plain(M.identity(b)));
  const remaining = state(a, c);
  q = finish(q, remaining);
  q = M.next(q, remaining, 0);
  assert.equal(q.run.expectedAccount.id, c.id);
  assert.equal(q.jobs[0].expectedAccount.id, a.id);
});

test("pause cancels its in-flight retry but preserves others; resume schedules unchanged-palette resend", () => {
  let s = state(a, b), q = M.next(settled(s), s, 0);
  q = M.request(q, "pause", s, a);
  q = finish(q, s, error(s));
  assert.equal(q.jobs.length, 1);
  q = M.next(q, s, 0);
  assert.equal(q.running, "pause");
  s = state({ ...a, automatic: false }, b);
  q = finish(q, s);
  q = M.request(q, "enable", s, s.ships[0]);
  q = M.next(q, s, 0);
  assert.equal(q.running, "enable");
  s = state({ ...a, pending: true }, b);
  q = finish(q, s);
  assert.equal(q.jobs.length, 2);
  assert.equal(q.jobs[1].expectedAccount.id, a.id);
  assert.equal(q.jobs[1].palette, palette);
});

test("new theme discards queued old snapshots and obsolete in-flight retries", () => {
  const s = state(a, b);
  let q = M.next(settled(s), s, 0);
  q = M.request(q, "theme", s);
  q = M.request(q, "theme", s);
  assert.equal(q.jobs.length, 0);
  q = finish(q, s, error(s));
  assert.equal(M.next(q, s, 0).running, "");
  q.debouncing = false;
  q = M.next(q, s, 0);
  const fresh = { ...palette, name: "New", primary: "#654321" };
  q = finish(q, s, ok(s, fresh));
  assert.equal(q.jobs.length, 2);
  for (const j of q.jobs) assert.equal(j.palette, fresh);
});

test("new theme during preview cannot enqueue an obsolete snapshot", () => {
  const s = state(a);
  let q = M.initialQueue();
  q.status = false;
  q = M.next(q, s, 0);
  q = M.request(q, "theme", s);
  q = finish(q, s, ok(s, palette));
  assert.equal(q.jobs.length, 0);
  assert.equal(q.palette, null);
});

test("login adds only newly identified pending ships, never retransmits existing ships", () => {
  const s = state({ ...a, automatic: false });
  let q = settled(s);
  assert.ok(M.idle(q));
  q.running = "login"; q.run = { action: "login" }; q.loginIds = [a.id];
  const added = state({ ...a, pending: true }, { ...b, pending: true });
  q = finish(q, added);
  assert.equal(q.jobs.length, 1);
  assert.equal(q.jobs[0].expectedAccount.id, b.id);
  assert.equal(q.jobs[0].palette, palette);
});

test("all three identity fields bind mutations; removed/re-added same origin is not retargeted", () => {
  const s = state(a, b);
  for (const extra of [{ id: "c".repeat(64) }, { ship: "~bud" }, { url: "https://new.example" }]) {
    let q = settled(s);
    q = M.request(q, "disconnect", s, a);
    const replacement = state({ ...a, ...extra }, b);
    q = M.next(q, replacement, 0);
    assert.equal(q.running, "sync");
    assert.equal(q.run.expectedAccount.id, b.id);
  }
});

test("auth failures stop only that row, null state remains usable, and no error loops", () => {
  let s = state(a, b), q = M.next(settled(s), s, 0);
  s = state({ ...a, automatic: false, authenticationRequired: true }, b);
  q = finish(q, s, error(s, "authentication"));
  assert.equal(q.jobs.length, 1);
  q = M.next(q, s, 0);
  assert.equal(q.run.expectedAccount.id, b.id);
  q = finish(q, s, error(null, "busy"));
  assert.equal(q.jobs.length, 1);
  q = finish(M.next(q, s, 2000), s, error(null, "account-changed", false));
  assert.equal(q.jobs.length, 0);
  assert.equal(q.status, true);
});

test("inverse toggle has action labels and a circular sync glyph; paths and URLs are bounded", () => {
  assert.equal(M.toggleLabel(a), "Pause automatic syncing");
  assert.equal(M.toggleIcon(a), "");
  assert.equal(M.toggleLabel({ ...a, automatic: false }), "Resume automatic syncing");
  assert.equal(M.toggleIcon({ ...a, automatic: false }), "\u21bb");
  assert.equal(M.localPath("file:///tmp/a%20b/client/main.py"), "/tmp/a b/client/main.py");
  for (const p of ["https://host/file", "file://host/file", "file:///tmp/%00", "file:///tmp/%xx"])
    assert.equal(M.localPath(p), "");
  assert.ok(M.safeUrl("http://[::1]:8080"));
  assert.ok(!M.safeUrl("https://host/#secret"));
});

test("startup and theme preview backoff preserve targets and publish one recovered snapshot", () => {
  const s = state(a, b);
  for (const theme of [false, true]) {
    let q = theme ? settled(state()) : M.initialQueue();
    if (theme) { q = M.request(q, "theme", s); q.debouncing = false; }
    else q = finish(M.next(q, s, 0), s);
    q = finish(M.next(q, s, 0), s, error(s, "busy"), 100);
    assert.equal(q.previewDue, 2100);
    assert.equal(q.previewAttempts, 1);
    assert.equal(q.targets.length, 2);
    assert.equal(M.next(q, s, 2099).running, "");
    assert.equal(M.retryDelay(q, 100), 2000);
    q = finish(M.next(q, s, 2100), s, error(null, "palette"), 2100);
    assert.equal(q.previewDue, 7100);
    assert.equal(q.previewAttempts, 2);
    const recovered = { ...palette, name: "Recovered" };
    q = finish(M.next(q, s, 7100), s, ok(s, recovered), 7100);
    assert.equal(q.targets.length, 0);
    assert.equal(q.jobs.length, 2);
    for (const expected of [a, b]) {
      q = M.next(q, s, 7100);
      assert.equal(q.running, "sync");
      assert.equal(q.run.expectedAccount.id, expected.id);
      assert.equal(q.run.palette, recovered);
      q = finish(q, s);
    }
    assert.ok(M.idle(q));
  }
});

test("startup busy status can recover through preview state or its own bounded retry", () => {
  const s = state(a, b), empty = state();
  for (const previewState of [s, null]) {
    let q = finish(M.next(M.initialQueue(), empty, 0), empty, error(null, "busy"));
    assert.equal(q.statusDue, 2000);
    assert.equal(q.statusAttempts, 1);
    q = M.next(q, empty, 0);
    assert.equal(q.running, "preview", "status backoff must not block preview");
    q = finish(q, previewState || empty, ok(previewState, palette));
    if (!previewState) {
      assert.equal(q.jobs.length, 0);
      assert.equal(M.retryDelay(q, 0), 2000);
      q = M.next(q, empty, 2000);
      assert.equal(q.running, "status");
      q = finish(q, s);
    }
    assert.equal(q.status, false);
    assert.equal(q.jobs.length, 2);
    for (const job of q.jobs) assert.equal(job.palette, palette);
    assert.equal(q.fanout, false);
  }
});

test("both startup observations may fail with null state and recover without a dead queue", () => {
  let q = finish(M.next(M.initialQueue(), state(), 0), state(), error(null, "busy"));
  q = finish(M.next(q, state(), 0), state(), error(null, "busy"), 100);
  assert.equal(M.retryDelay(q, 100), 1900);
  q = M.next(q, state(), 2000);
  assert.equal(q.running, "status");
  q = finish(q, state(), error(null, "busy"), 2000);
  assert.equal(q.statusDue, 7000);
  assert.equal(M.retryDelay(q, 2000), 100);
  q = M.next(q, state(), 2100);
  assert.equal(q.running, "preview");
  q = finish(q, state(a, b), ok(state(a, b), palette), 2100);
  assert.equal(q.jobs.length, 2);
  assert.equal(q.status, false, "authoritative preview cancels redundant startup status retry");
});

test("failed startup preview can still supply authoritative state and retire the busy status retry", () => {
  const s = state(a, b);
  let q = finish(M.next(M.initialQueue(), state(), 0), state(), error(null, "busy"));
  q = finish(M.next(q, state(), 0), s, error(s, "palette"));
  assert.equal(q.knownState, true);
  assert.equal(q.status, false);
  assert.equal(q.targets.length, 2);
  assert.equal(q.preview, true);
  assert.equal(M.retryDelay(q, 0), 2000);
  q = finish(M.next(q, s, 2000), s, ok(s, palette));
  assert.equal(q.jobs.length, 2);
  for (const job of q.jobs) assert.equal(job.palette, palette);
});

test("observation exhaustion is bounded and passive refresh cannot resurrect auto intent", () => {
  let q = M.initialQueue(), now = 0, statuses = 0, previews = 0;
  while (!M.idle(q)) {
    q = M.next(q, state(), now);
    if (!q.running) {
      const delay = M.retryDelay(q, now);
      assert.ok(delay > 0, "waiting work must have a wake-up");
      now += delay;
      continue;
    }
    if (q.running === "status") statuses++;
    else if (q.running === "preview") previews++;
    else assert.fail("no sync before an authoritative state and successful preview");
    assert.ok(statuses + previews <= 8);
    q = finish(q, state(), error(null, "busy"), now);
  }
  assert.equal(statuses, 4);
  assert.equal(previews, 4);
  assert.equal(q.fanout, false);
  assert.equal(q.targets.length, 0);
  q = M.request(q, "refresh", state());
  q = finish(M.next(q, state(), now), state(a, b));
  q = finish(M.next(q, state(a, b), now), state(a, b), ok(state(a, b), palette));
  assert.ok(M.idle(q), "refresh observes recovered ships but creates no sync intent");
});

test("healthy jobs run while a passive preview backs off, with independent deadlines", () => {
  const s = state(a, b);
  let q = M.request(settled(s), "refresh", s);
  q = finish(M.next(q, s, 0), s);
  q = finish(M.next(q, s, 0), s, error(s, "busy"), 0);
  assert.equal(q.targets.length, 0);
  q = M.next(q, s, 1);
  assert.equal(q.running, "sync");
  assert.equal(q.run.expectedAccount.id, a.id);
  assert.equal(q.run.palette, palette);
  q = finish(q, s, error(s), 1000);
  q = M.next(q, s, 1001);
  assert.equal(q.run.expectedAccount.id, b.id);
  q = finish(q, s);
  assert.equal(M.retryDelay(q, 1001), 999);
  q = M.next(q, s, 2000);
  assert.equal(q.running, "preview");
  q = finish(q, s, error(s, "busy"), 2000);
  assert.equal(M.retryDelay(q, 2000), 1000);
  q = M.next(q, s, 3000);
  assert.equal(q.running, "sync");
  assert.equal(q.run.expectedAccount.id, a.id);
  q = finish(q, s);
  q = finish(M.next(q, s, 7000), s, ok(s, { ...palette, name: "Passive" }));
  assert.ok(M.idle(q), "passive preview does not republish the recovered palette");
});

test("new generations replace backoff and obsolete completions cannot restore old intent", () => {
  const s = state(a, b);
  let q = finish(M.next(M.initialQueue(), s, 0), s);
  q = finish(M.next(q, s, 0), s, error(s, "busy"));
  q = M.request(q, "theme", s);
  assert.equal(q.previewAttempts, 0);
  assert.equal(q.previewDue, 0);
  q.debouncing = false;
  q = M.next(q, s, 0);
  q = M.request(q, "theme", state(b));
  q = finish(q, s, error(s, "busy"));
  assert.equal(q.previewAttempts, 0);
  assert.deepEqual(plain(q.targets.map(t => t.id)), [b.id]);
  q.debouncing = false;
  const fresh = { ...palette, name: "Newest generation" };
  q = finish(M.next(q, s, 0), s, ok(s, fresh));
  assert.equal(q.jobs.length, 1);
  assert.equal(q.jobs[0].expectedAccount.id, b.id);
  assert.equal(q.jobs[0].palette, fresh);
});

test("targeted enable and login retain only their new target through preview retries and exhaustion", () => {
  for (const action of ["enable", "login"]) {
    for (const exhaust of [false, true]) {
      const before = state(a, { ...b, automatic: false });
      let q = settled(before);
      q = finish(M.next(q, before, 0), before);
      q.palette = null;
      q.running = action;
      q.run = { action, expectedAccount: M.identity(b) };
      q.loginIds = [a.id];
      const s = state(a, { ...b, pending: true });
      q = finish(q, s);
      assert.deepEqual(plain(q.targets.map(t => t.id)), [b.id]);
      let now = 0;
      const failures = exhaust ? 4 : 2;
      for (let i = 0; i < failures; i++) {
        q = M.next(q, s, now);
        assert.equal(q.running, "preview");
        q = finish(q, s, error(s, "palette"), now);
        if (q.preview) now = q.previewDue;
      }
      if (exhaust) {
        assert.ok(M.idle(q));
        assert.equal(q.targets.length, 0);
        q = M.request(q, "refresh", s);
        q = finish(M.next(q, s, now), s);
        q = finish(M.next(q, s, now), s, ok(s, palette));
        assert.ok(M.idle(q));
      } else {
        q = finish(M.next(q, s, now), s, ok(s, palette));
        assert.equal(q.jobs.length, 1);
        assert.equal(q.jobs[0].expectedAccount.id, b.id);
        assert.equal(q.jobs[0].palette, palette);
      }
    }
  }
});

test("pause/remove during preview backoff cancel only that identity, even if the control fails", () => {
  for (const action of ["pause", "disconnect"]) {
    const s = state(a, b);
    let q = finish(M.next(M.initialQueue(), s, 0), s);
    q = finish(M.next(q, s, 0), s, error(s, "busy"));
    q = M.request(q, action, s, a);
    q = M.next(q, s, 1);
    assert.equal(q.running, action);
    q = finish(q, s, error(null, "state", false));
    assert.deepEqual(plain(q.targets.map(t => t.id)), [b.id]);
    // Even an authoritative auto=true for the canceled row cannot recreate its intent.
    q = finish(M.next(q, s, 2000), s, ok(s, palette));
    assert.equal(q.jobs.length, 1);
    assert.equal(q.jobs[0].expectedAccount.id, b.id);
  }
  let q = finish(M.next(M.initialQueue(), state(a, b), 0), state(a, b));
  q = finish(M.next(q, state(a, b), 0), state(a, b), error(null, "busy"));
  const replaced = state({ ...a, id: c.id }, b);
  q = finish(M.next(q, replaced, 2000), replaced, ok(replaced, palette));
  assert.equal(q.jobs.length, 1, "re-added same URL and ship must not inherit captured preview intent");
  assert.equal(q.jobs[0].expectedAccount.id, b.id);
});

test("canceling the sole targeted preview intent cannot revive it on a successful retry", () => {
  for (const action of ["pause", "disconnect"]) {
    const before = state({ ...a, automatic: false }, b), s = state(a, b);
    let q = settled(before);
    q = finish(M.next(q, before, 0), before);
    q.palette = null;
    q = M.request(q, "enable", before, before.ships[0]);
    q = finish(M.next(q, before, 0), s);
    q = finish(M.next(q, s, 0), s, error(null, "busy"));
    assert.deepEqual(plain(q.targets.map(t => t.id)), [a.id]);
    q = M.request(q, action, s, a);
    q = finish(M.next(q, s, 0), s, error(null, "state", false));
    assert.equal(q.targets.length, 0);
    q = finish(M.next(q, s, 2000), s, ok(s, palette));
    assert.ok(M.idle(q));
    assert.equal(q.jobs.length, 0, "neither the canceled target nor unrelated ships should sync");
  }
});
