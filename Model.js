// Shared by QML and Node. Only validated protocol fields enter UI state.
var maxOutput = 262144;
var retryDelays = [2000, 5000, 15000];
function emptyState() { return { ships: [] }; }
function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function text(value, limit) {
  return typeof value === "string" && value.length <= limit && !/[\x00-\x1f\x7f]/.test(value);
}
function safeUrl(value) {
  return text(value, 2048) && /^https?:\/\/(?:[a-zA-Z0-9.-]+|\[[a-fA-F0-9:.]+\])(?::[0-9]{1,5})?\/?$/.test(value);
}
function localPath(url) {
  var match = /^file:\/\/(?:localhost)?(\/[^?#]*)$/.exec(String(url));
  if (!match) return "";
  try {
    var path = decodeURIComponent(match[1]);
    return /[\x00-\x1f\x7f]/.test(path) ? "" : path;
  } catch (_) { return ""; }
}
function errorText(code) {
  var messages = {
    invalid_response: "The helper returned an invalid response.",
    helper_unavailable: "The Python helper could not be started.",
    helper_timeout: "The helper exceeded its time limit. Refresh status before trying again.",
    output_limit: "The helper response exceeded the safe size limit.",
    authentication: "Session expired or sign-in rejected. Remove this ship, then add it again.",
    network: "The ship could not be reached. Check its connection.",
    keyring: "Secret Service is unavailable or locked. No plaintext fallback is used.",
    url: "Use HTTPS, or HTTP on loopback, without credentials, query, or fragment.",
    input: "Check the ship URL and +code.",
    "duplicate-origin": "This ship URL is already added.",
    "ship-limit": "The maximum of 64 ships has been reached.",
    "logout-unconfirmed": "Remote session logout could not be confirmed.",
    "keyring-cleanup": "Keyring cleanup could not be confirmed; a saved session may remain.",
    cleanup: "Remote logout and keyring cleanup could not be confirmed; a saved session may remain.",
    "account-changed": "This ship login changed. Review the current ship list.",
    palette: "The current Omarchy palette could not be read.",
    command: "A required local command failed or is unavailable.",
    state: "Private local state could not be read or saved.",
    redirect: "The ship redirected the request. Add it using its final origin.",
    http: "The ship could not complete the request.",
    size: "The ship response exceeded the safe size limit.",
    protocol: "The ship returned an unexpected settings response.",
    malformed: "Existing data is malformed. Nothing was replaced.",
    nack: "The ship's settings agent rejected the update.",
    "ack-timeout": "The ship did not acknowledge the update. Publication remains unconfirmed.",
    verification: "The ship update could not be confirmed. Publication remains pending.",
    timeout: "The operation timed out. Publication remains unconfirmed.",
    internal: "The helper could not safely complete the operation.",
    busy: "Another helper operation is running. Try again shortly."
  };
  return Object.prototype.hasOwnProperty.call(messages, code) ? messages[code]
    : "The operation could not be completed. Check the ship connection and session.";
}
function failure(code) {
  return { schemaVersion: 2, ok: false, state: null, palette: null, warning: null,
    error: { code: code, message: errorText(code), retryable: false } };
}
function parseResponse(raw, exitCode) {
  if (typeof raw !== "string" || raw.length > maxOutput) return failure("output_limit");
  try {
    var value = JSON.parse(raw);
    if (!object(value) || value.schemaVersion !== 2 || typeof value.ok !== "boolean"
        || (value.ok ? exitCode !== 0 : exitCode !== 1)) throw 0;
    var state = null;
    if (value.state !== null) {
      if (!object(value.state) || !Array.isArray(value.state.ships) || value.state.ships.length > 64) throw 0;
      var ids = {}, urls = {};
      state = { ships: value.state.ships.map(function(s) {
        if (!object(s) || !text(s.id, 64) || !/^[0-9a-f]{64}$/.test(s.id) || ids[s.id]
            || !text(s.ship, 256) || !/^~[a-z-]+$/.test(s.ship) || !safeUrl(s.url) || urls[s.url]
            || !text(s.lastTheme, 256) || !text(s.lastError, 512) || !text(s.lastPublished, 40)
            || (s.lastPublished !== "" && !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)$/.test(s.lastPublished))) throw 0;
        ids[s.id] = true; urls[s.url] = true;
        var row = { id: s.id, ship: s.ship, url: s.url, lastTheme: s.lastTheme,
          lastPublished: s.lastPublished, lastError: s.lastError ? "Sync failed. Pause then resume to retry." : "" };
        ["automatic", "pending", "authenticationRequired"].forEach(function(key) {
          if (typeof s[key] !== "boolean") throw 0;
          row[key] = s[key];
        });
        return row;
      }) };
    }
    var palette = null;
    if (value.palette !== null) {
      var p = value.palette;
      if (!object(p) || p.id !== "omarchy-urbit-theme" || !text(p.name, 256) || !p.name || typeof p.dark !== "boolean") throw 0;
      palette = { id: p.id, name: p.name, dark: p.dark };
      ["primary", "secondary", "tertiary", "background", "surface"].forEach(function(key) {
        if (typeof p[key] !== "string" || !/^#[0-9a-fA-F]{6}$/.test(p[key])) throw 0;
        palette[key] = p[key];
      });
    }
    function notice(n) {
      if (!object(n) || !text(n.code, 64) || !/^[a-z][a-z0-9_-]*$/.test(n.code)
          || !text(n.message, 512) || typeof n.retryable !== "boolean") throw 0;
      return { code: n.code, message: errorText(n.code), retryable: n.retryable };
    }
    var error = value.error === null ? null : notice(value.error);
    var warning = value.warning === null ? null : notice(value.warning);
    if (value.ok === !!error) throw 0;
    return { schemaVersion: 2, ok: value.ok, state: state, palette: palette, error: error, warning: warning };
  } catch (_) { return failure("invalid_response"); }
}

function identity(row) { return { id: row.id, url: row.url, ship: row.ship }; }
function sameAccount(a, b) { return !!a && !!b && a.id === b.id && a.url === b.url && a.ship === b.ship; }
function account(state, expected) { return state.ships.filter(function(s) { return sameAccount(s, expected); })[0]; }
function canAuto(row) { return !!row && row.automatic && !row.authenticationRequired; }
function toggleLabel(row) { return row.automatic ? "Pause automatic syncing" : "Resume automatic syncing"; }
function toggleIcon(row) { return row.automatic ? "" : "\u21bb"; }
function initialQueue() {
  return { running: "", run: null, controls: [], jobs: [], targets: [], status: true, preview: true,
    fanout: true, knownState: false, statusDue: 0, statusAttempts: 0, previewDue: 0, previewAttempts: 0,
    debouncing: false, generation: 0, palette: null, loginIds: [] };
}
function copy(queue) {
  var q = Object.assign({}, queue);
  q.controls = queue.controls.slice(); q.jobs = queue.jobs.slice(); q.targets = queue.targets.slice();
  return q;
}
function idle(q) { return !q.running && !q.controls.length && !q.jobs.length && !q.status && !q.preview && !q.debouncing; }
function controlled(q, row) { return q.controls.some(function(c) { return sameAccount(c.expectedAccount, row); }); }
function enqueue(q, row) {
  if (!canAuto(row) || controlled(q, row)) return;
  q.jobs = q.jobs.filter(function(j) { return !sameAccount(j.expectedAccount, row); });
  q.jobs.push({ action: "sync", expectedAccount: identity(row), palette: q.palette,
    generation: q.generation, attempts: 0, due: 0 });
}
function request(queue, action, state, row) {
  var q = copy(queue);
  if (action === "theme") {
    q.generation++; q.preview = true; q.fanout = !q.knownState; q.debouncing = true;
    q.previewDue = 0; q.previewAttempts = 0;
    q.jobs = [];
    q.targets = state.ships.filter(function(s) { return canAuto(s) && !controlled(q, s); }).map(identity);
  } else if (action === "refresh") {
    if (!q.status && q.running !== "status") { q.statusDue = 0; q.statusAttempts = 0; }
    if (!q.preview && q.running !== "preview") { q.previewDue = 0; q.previewAttempts = 0; }
    q.status = true; q.preview = true;
  } else if (["pause", "enable", "disconnect"].indexOf(action) >= 0 && account(state, row)) {
    var expected = identity(row);
    q.jobs = q.jobs.filter(function(j) { return !sameAccount(j.expectedAccount, expected); });
    q.targets = q.targets.filter(function(t) { return !sameAccount(t, expected); });
    // Keep other ships' control order; removal supersedes a queued toggle.
    var prior = q.controls.filter(function(c) { return sameAccount(c.expectedAccount, expected); })[0];
    q.controls = q.controls.filter(function(c) { return !sameAccount(c.expectedAccount, expected); });
    q.controls.push({ action: prior && prior.action === "disconnect" ? "disconnect" : action, expectedAccount: expected });
  }
  return q;
}
function next(queue, state, now) {
  var q = copy(queue);
  if (q.running) return q;
  q.controls = q.controls.filter(function(c) { return !!account(state, c.expectedAccount); });
  q.jobs = q.jobs.filter(function(j) { return canAuto(account(state, j.expectedAccount)); });
  var job = null;
  if (q.controls.length) job = q.controls.shift();
  else if (q.status && q.statusDue <= now) { q.status = false; job = { action: "status" }; }
  else if (q.preview && !q.debouncing && q.previewDue <= now) {
    q.preview = false;
    job = { action: "preview", generation: q.generation };
  } else if (!q.debouncing) {
    for (var i = 0; i < q.jobs.length; i++) {
      if (q.jobs[i].due <= now) { job = q.jobs.splice(i, 1)[0]; break; }
    }
  }
  if (job) { q.run = job; q.running = job.action; }
  return q;
}
function retryDelay(q, now) {
  if (q.running) return 0;
  var deadlines = q.debouncing ? [] : q.jobs.map(function(j) { return j.due; });
  if (q.status) deadlines.push(q.statusDue);
  if (q.preview && !q.debouncing) deadlines.push(q.previewDue);
  return deadlines.length ? Math.max(1, Math.min.apply(null, deadlines) - now) : 0;
}
function complete(queue, response, state, now) {
  var q = copy(queue), action = q.running, run = q.run;
  q.running = ""; q.run = null;
  q.controls = q.controls.filter(function(c) { return !!account(state, c.expectedAccount); });
  q.jobs = q.jobs.filter(function(j) { return canAuto(account(state, j.expectedAccount)); });
  q.targets = q.targets.filter(function(t) { return canAuto(account(state, t)) && !controlled(q, t); });
  if (response.state) {
    q.knownState = true;
    if (q.statusAttempts) { q.status = false; q.statusDue = 0; q.statusAttempts = 0; }
    // Resolve fan-out once, not again on retries: a removed/re-added login is new intent.
    if (q.fanout) {
      q.targets = state.ships.filter(function(s) { return canAuto(s) && !controlled(q, s); }).map(identity);
      q.fanout = false;
    }
  }
  if (action === "status" && !q.knownState && !response.ok && response.error.retryable
      && q.statusAttempts < retryDelays.length) {
    q.status = true;
    q.statusDue = now + retryDelays[q.statusAttempts++];
  }
  if (run && run.expectedAccount && response.error && response.error.code === "account-changed" && !response.state)
    q.status = true;
  if (action === "preview" && run.generation === q.generation) {
    q.palette = response.ok ? response.palette : null;
    if (!response.ok && response.error.retryable && q.previewAttempts < retryDelays.length) {
      q.preview = true;
      q.previewDue = now + retryDelays[q.previewAttempts++];
    } else {
      q.previewDue = 0; q.previewAttempts = 0;
      if (!q.palette) { q.targets = []; q.fanout = false; q.preview = false; }
    }
  }
  if (q.palette && !q.preview && !q.debouncing) {
    q.targets.forEach(function(t) { enqueue(q, account(state, t)); });
    q.targets = [];
  }
  if (response.ok && (action === "login" || action === "enable")) {
    state.ships.forEach(function(row) {
      var selected = action === "login" ? q.loginIds.indexOf(row.id) < 0 && row.pending
        : sameAccount(run.expectedAccount, row);
      if (!selected || !canAuto(row) || controlled(q, row)) return;
      if (q.preview || !q.palette) {
        if (!q.preview) { q.previewDue = 0; q.previewAttempts = 0; }
        q.targets.push(identity(row)); q.preview = true;
      } else enqueue(q, row);
    });
  }
  if (action === "login") q.loginIds = [];
  if (!q.knownState && !q.status && !q.preview) q.fanout = false;
  if (action === "sync" && !response.ok && response.error.retryable
      && response.error.code !== "authentication" && response.error.code !== "account-changed"
      && run.generation === q.generation && canAuto(account(state, run.expectedAccount))
      && !controlled(q, run.expectedAccount) && run.attempts < retryDelays.length) {
    var retry = Object.assign({}, run);
    retry.due = now + retryDelays[retry.attempts++];
    q.jobs.push(retry);
  }
  return q;
}
