// Shared by QML and the Node tests. Only protocol-approved fields enter UI state.
var maxOutput = 32768;
var retryDelays = [2000, 5000, 15000];

function emptyState() {
  return { connected: false, ship: "", url: "", automatic: false, pending: false,
    authenticationRequired: false, lastPublished: "", lastTheme: "", lastError: "" };
}

function object(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function text(value, limit) {
  return typeof value === "string" && value.length <= limit && !/[\x00-\x1f\x7f]/.test(value);
}

function safeUrl(value) {
  return text(value, 2048) && (value === "" || /^https?:\/\/(?:[a-zA-Z0-9.-]+|\[[a-fA-F0-9:.]+\])(?::[0-9]{1,5})?\/?$/.test(value));
}

function localPath(url) {
  var match = /^file:\/\/(?:localhost)?(\/[^?#]*)$/.exec(String(url));
  if (!match) return "";
  try {
    var path = decodeURIComponent(match[1]);
    return /[\x00-\x1f\x7f]/.test(path) ? "" : path;
  } catch (_) { return ""; }
}

function failure(code) {
  return { schemaVersion: 1, ok: false, state: null, palette: null,
    error: { code: code, message: errorText(code), retryable: false } };
}

function errorText(code) {
  var messages = {
    invalid_response: "The helper returned an invalid response.",
    helper_unavailable: "The Python helper could not be started.",
    helper_timeout: "The helper exceeded its time limit. Refresh status before trying again.",
    output_limit: "The helper response exceeded the safe size limit.",
    authentication: "Sign-in is required or was rejected. Disconnect any current account, then sign in again.",
    network: "The ship could not be reached. Check its address and connection.",
    keyring: "Secret Service is unavailable or locked. No plaintext fallback is used.",
    url: "Use HTTPS, or HTTP on localhost or a loopback IP, without credentials, query, or fragment.",
    input: "The request is invalid. Check the ship URL and +code.",
    connected: "Disconnect the current ship before signing in again.",
    "account-changed": "The connected account changed. Review the account and grant consent again.",
    palette: "The current Omarchy palette could not be read.",
    command: "A required local command failed or is unavailable.",
    state: "Private local state could not be read or saved.",
    redirect: "The ship redirected the request. Sign in using its final origin.",
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
  return Object.prototype.hasOwnProperty.call(messages, code) ? messages[code] : "The operation failed. Check the ship connection, session, and current theme.";
}

function parseResponse(raw, exitCode) {
  if (typeof raw !== "string" || raw.length > maxOutput) return failure("output_limit");
  try {
    var value = JSON.parse(raw);
    if (!object(value) || value.schemaVersion !== 1 || typeof value.ok !== "boolean"
        || (value.ok ? exitCode !== 0 : exitCode !== 1)
        || (!object(value.state) && (value.ok || value.state !== null))) throw 0;
    var source = value.state, state = source === null ? null : emptyState();
    if (source !== null) {
      ["connected", "automatic", "pending", "authenticationRequired"].forEach(function(key) {
        if (typeof source[key] !== "boolean") throw 0;
        state[key] = source[key];
      });
      if (!text(source.ship, 256) || (source.ship !== "" && !/^~[a-z-]+$/.test(source.ship))
          || !safeUrl(source.url) || !text(source.lastTheme, 256) || !text(source.lastError, 512)
          || !text(source.lastPublished, 40)
          || (source.lastPublished !== "" && !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|\+00:00)$/.test(source.lastPublished))) throw 0;
      if (state.connected && (!source.ship || !source.url)) throw 0;
      if (!state.connected && (state.automatic || state.pending)) throw 0;
      state.ship = source.ship;
      state.url = source.url;
      state.lastTheme = source.lastTheme;
      state.lastPublished = source.lastPublished;
      // Persisted errors are safe per contract, but arbitrary helper text is never displayed.
      state.lastError = source.lastError ? "The previous publication failed. Publish Now to try again." : "";
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
    var error = null;
    if (value.ok) {
      if (value.error !== null) throw 0;
    } else {
      var e = value.error;
      if (!object(e) || !text(e.code, 64) || !/^[a-z][a-z0-9_-]*$/.test(e.code)
          || !text(e.message, 512) || typeof e.retryable !== "boolean") throw 0;
      error = { code: e.code, message: errorText(e.code), retryable: e.retryable };
    }
    return { schemaVersion: 1, ok: value.ok, state: state, palette: palette, error: error };
  } catch (_) { return failure("invalid_response"); }
}

function initialQueue() {
  // All queued mutations share one captured identity; dispatch never substitutes
  // a newer account. runAccount remains separate while observations/controls queue.
  return { running: "", control: "", status: true, preview: true, sync: false, force: false,
    startup: true, suspended: false, debouncing: false, generation: 0, runGeneration: 0,
    retryCount: 0, retryDelay: 0, retryAction: "", startupRetryCount: 0,
    knownAccount: null, intentAccount: null, runAccount: null };
}

function copy(queue) { return Object.assign({}, queue); }
function identity(state) { return state && state.connected ? { url: state.url, ship: state.ship } : null; }
function sameAccount(a, b) { return a === null || b === null ? a === b : a.url === b.url && a.ship === b.ship; }
function cancelMutations(queue) {
  var q = copy(queue);
  q.control = "";
  q.sync = false;
  q.force = false;
  q.intentAccount = null;
  q.retryDelay = 0;
  q.retryAction = "";
  q.retryCount = 0;
  return q;
}
function canAuto(state) { return state.connected && state.automatic && !state.authenticationRequired; }
function idle(queue) {
  return !queue.running && !queue.control && !queue.status && !queue.preview && !queue.sync && !queue.force && !queue.debouncing;
}

function request(queue, action, state) {
  var q = copy(queue);
  if (q.intentAccount && !sameAccount(q.intentAccount, identity(state))) q = cancelMutations(q);
  if (action === "theme") {
    q.generation++;
    q.preview = true;
    q.debouncing = true;
    q.sync = canAuto(state) && !q.suspended;
    if (q.sync) q.intentAccount = identity(state);
    q.retryCount = 0;
    if (q.retryAction !== "status") { q.retryDelay = 0; q.retryAction = ""; }
  } else if ((action === "pause" || action === "disconnect") && state.connected) {
    q.control = q.control === "disconnect" ? "disconnect" : action;
    q.intentAccount = identity(state);
    q.suspended = true;
    q.sync = false;
    q.force = false;
    q.retryCount = 0;
    q.retryDelay = 0;
    q.retryAction = "";
  } else if (action === "refresh") {
    q.status = true;
    q.preview = true;
  } else if (action === "publish" && !q.running && !q.control && state.connected && !state.authenticationRequired) {
    q.force = true;
    q.intentAccount = identity(state);
    q.sync = false;
    q.retryCount = 0;
    q.retryDelay = 0;
  } else if (action === "enable" && !q.running && !q.control && state.connected && !state.authenticationRequired) {
    q.control = "enable";
    q.intentAccount = identity(state);
    q.retryCount = 0;
    q.retryDelay = 0;
  }
  return q;
}

function next(queue, state) {
  var q = copy(queue), action = "";
  if (q.intentAccount && !sameAccount(q.intentAccount, identity(state))) q = cancelMutations(q);
  if (!q.running) {
    if (q.control) { action = q.control; q.control = ""; }
    else if (q.status && !(q.retryAction === "status" && q.retryDelay)) { action = "status"; q.status = false; }
    else if (q.preview && !q.debouncing) { action = "preview"; q.preview = false; }
    else if (q.force && !q.debouncing) { action = "publish"; q.force = false; }
    else if (q.sync && !q.debouncing && !q.retryDelay) {
      q.sync = false;
      if (canAuto(state) && !q.suspended) action = "sync";
    }
  }
  if (action) {
    q.running = action;
    q.runGeneration = q.generation;
    q.runAccount = ["sync", "publish", "enable", "pause", "disconnect"].indexOf(action) >= 0 ? q.intentAccount : null;
  }
  return q;
}

function complete(queue, response, state) {
  var q = copy(queue), action = q.running;
  q.running = "";
  var accountChanged = response.error && response.error.code === "account-changed";
  if (response.state) {
    if (!sameAccount(q.knownAccount, identity(state))) {
      q = cancelMutations(q);
      q.suspended = false;
    }
    q.knownAccount = identity(state);
  }
  if (accountChanged) {
    q = cancelMutations(q);
    if (!response.state) q.status = true;
  }
  if (q.startup && response.state) {
    q.startup = false;
    q.startupRetryCount = 0;
    if (q.retryAction === "status") { q.retryDelay = 0; q.retryAction = ""; q.status = false; }
    if (!accountChanged && canAuto(state) && !q.suspended) {
      q.sync = true;
      q.intentAccount = identity(state);
    }
  } else if (q.startup && action === "status" && !response.ok && response.error.retryable
      && !accountChanged && q.startupRetryCount < retryDelays.length) {
    q.status = true;
    q.retryAction = "status";
    q.retryDelay = retryDelays[q.startupRetryCount++];
  }
  var matches = sameAccount(q.runAccount, identity(state));
  if (response.ok && (action === "login" || (action === "enable" && matches))) {
    q.suspended = !!q.control;
    if (action === "enable" && !q.suspended) { q.sync = true; q.intentAccount = q.runAccount; }
  }
  if (action === "sync" || action === "publish") {
    if (response.ok) {
      q.retryCount = 0;
      q.retryDelay = 0;
      if (q.runGeneration === q.generation) q.sync = false;
    }
    else if (matches && !accountChanged && q.runGeneration === q.generation && response.error.retryable
        && response.error.code !== "authentication" && canAuto(state)
        && (state.pending || response.error.code === "palette" || (!response.state && response.error.code === "busy"))
        && !q.suspended && q.retryCount < retryDelays.length) {
      q.sync = true;
      q.intentAccount = q.runAccount;
      q.retryAction = "sync";
      q.retryDelay = retryDelays[q.retryCount++];
    }
  }
  if (!canAuto(state) || q.suspended) {
    q.sync = false;
    if (q.retryAction !== "status") { q.retryDelay = 0; q.retryAction = ""; }
  }
  return q;
}

function meaningfulError(previous, action, response) {
  var observation = action === "status" || action === "preview";
  if (!response.ok) return observation && previous ? previous : response.error.message;
  return observation ? previous || response.state.lastError : "";
}
