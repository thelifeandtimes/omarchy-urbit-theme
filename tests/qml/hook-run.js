// Real hook -> installed omarchy-shell -> qs IPC -> real Service, never the desktop.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { performance } = require("node:perf_hooks");

const root = path.resolve(__dirname, "../..");
const wrapper = "/usr/share/omarchy/bin/omarchy-shell";
// Do not inherit a user PATH that could substitute a wrapper or IPC client.
const safePath = "/usr/share/omarchy/bin:/usr/bin:/bin";
function missingPrerequisites() {
  return [wrapper, "quickshell", "qs", "python3", "timeout", "bash"].filter(command => {
    const candidates = command.includes("/") ? [command] : safePath.split(":").map(p => path.join(p, command));
    return !candidates.some(p => {
      try { fs.accessSync(p, fs.constants.X_OK); return true; } catch (_) { return false; }
    });
  });
}

async function run() {
  const missing = missingPrerequisites();
  if (missing.length) {
    if (!process.argv.includes("--allow-skip")) throw new Error(`Hook IPC prerequisites unavailable: ${missing.join(", ")}`);
    process.stderr.write(`SKIP: Hook IPC prerequisites unavailable: ${missing.join(", ")}\nNo real hook IPC checks ran.\n`);
    return;
  }
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "urbit-theme-hook-"));
  const children = new Set();
  let shellOutput = "";
  let shell;
  let interruptCode = 0;
  function killGroup(child, signal) {
    if (!child.pid) return; // A failed spawn has no process group to reap.
    try { process.kill(-child.pid, signal); } catch (error) { if (error.code !== "ESRCH") throw error; }
  }
  function start(command, args, env) {
    if (interruptCode) throw new Error("Hook IPC test interrupted");
    const child = spawn(command, args, { env, cwd: temp, detached: true, stdio: ["ignore", "pipe", "pipe"] });
    children.add(child);
    child.done = new Promise(resolve => {
      child.once("error", error => resolve({ error }));
      child.once("close", (code, signal) => resolve({ code, signal }));
    });
    return child;
  }
  async function command(file, args, limit = 4500) {
    const child = start(file, args, environment);
    let stdout = "", stderr = "", timedOut = false;
    child.stdout.on("data", data => { stdout = (stdout + data).slice(-16384); });
    child.stderr.on("data", data => { stderr = (stderr + data).slice(-16384); });
    const timer = setTimeout(() => { timedOut = true; killGroup(child, "SIGKILL"); }, limit);
    try {
      const result = await child.done;
      assert.equal(timedOut, false, `${path.basename(file)} exceeded ${limit}ms`);
      if (result.error) throw result.error;
      return { ...result, stdout: stdout.trim(), stderr: stderr.trim() };
    } finally {
      clearTimeout(timer);
      killGroup(child, "SIGKILL");
      children.delete(child);
    }
  }
  async function ipc(method) {
    const result = await command(wrapper, ["testControl", method]);
    assert.equal(result.code, 0, `testControl.${method}: ${result.stderr}`);
    return result.stdout;
  }
  async function waitFor(predicate) {
    const deadline = performance.now() + 8000;
    let last;
    while (performance.now() < deadline) {
      assert.equal(shell.exitCode, null, `Disposable shell exited: ${shellOutput}`);
      const result = await command(wrapper, ["testControl", "snapshot"]);
      if (result.code === 0) {
        last = JSON.parse(result.stdout);
        if (predicate(last)) return last;
      }
      await new Promise(resolve => setTimeout(resolve, 40));
    }
    throw new Error(`IPC readiness timed out: ${JSON.stringify(last)}\n${shellOutput}`);
  }
  async function hook() {
    const begin = performance.now();
    const result = await command("bash", [path.join(root, "hooks/omarchy-urbit-theme")]);
    assert.equal(result.code, 0, "Hook must exit 0, including when the service is missing");
    assert.equal(result.stdout + result.stderr, "", "Hook must remain silent");
    assert.ok(performance.now() - begin < 4000, "Hook must be bounded by its 3s timeout (plus scheduling margin)");
  }
  function healthy(state, generation, previews) {
    assert.equal(state.ready, true);
    assert.equal(state.generation, generation);
    assert.equal(state.themeRequests, generation);
    assert.equal(state.previews, previews);
    assert.equal(state.statuses, 1);
    assert.equal(state.unexpectedActions, 0);
    assert.equal(state.previewsDuringDebounce, 0);
    assert.equal(state.disconnected, true);
    assert.equal(state.clean, true);
    if (generation) assert.ok(state.previewDelay >= 300, `Preview bypassed the 350ms debounce: ${state.previewDelay}ms`);
  }
  const environment = {
    PATH: safePath, HOME: path.join(temp, "home"), TMPDIR: temp, LANG: "C.UTF-8",
    OMARCHY_PATH: temp, XDG_RUNTIME_DIR: path.join(temp, "runtime"),
    XDG_CACHE_HOME: path.join(temp, "cache"), XDG_CONFIG_HOME: path.join(temp, "config"),
    XDG_DATA_HOME: path.join(temp, "data"), XDG_STATE_HOME: path.join(temp, "state"),
    QT_QPA_PLATFORM: "offscreen", QT_QUICK_BACKEND: "software", QML_DISABLE_DISK_CACHE: "1",
    QT_QPA_PLATFORMTHEME: "", QT_QUICK_CONTROLS_STYLE: "Basic"
    // No DISPLAY, WAYLAND_DISPLAY, DBus, QML import overrides, or desktop session environment.
  };
  async function cleanup() {
    for (const child of children) killGroup(child, "SIGKILL");
    await Promise.all([...children].map(child => child.done));
    fs.rmSync(temp, { recursive: true, force: true });
  }
  const interrupted = async signal => {
    interruptCode = signal === "SIGINT" ? 130 : 143;
    await cleanup();
    process.exit(interruptCode);
  };
  const onInt = () => interrupted("SIGINT"), onTerm = () => interrupted("SIGTERM");
  process.once("SIGINT", onInt);
  process.once("SIGTERM", onTerm);
  try {
    for (const name of ["home", "runtime", "cache", "config", "data", "state", "shell/client"])
      fs.mkdirSync(path.join(temp, name), { recursive: true, mode: 0o700 });
    for (const name of ["Service.qml", "Model.js"])
      fs.copyFileSync(path.join(root, name), path.join(temp, "shell", name));
    fs.copyFileSync(path.join(__dirname, "hook-smoke.qml"), path.join(temp, "shell/shell.qml"));
    fs.copyFileSync(path.join(__dirname, "helper.py"), path.join(temp, "shell/client/main.py"));
    shell = start("quickshell", ["--no-color", "--path", path.join(temp, "shell/shell.qml")], environment);
    for (const stream of [shell.stdout, shell.stderr])
      stream.on("data", data => { shellOutput = (shellOutput + data).slice(-32768); });
    healthy(await waitFor(s => s.ready), 0, 1);

    await hook();
    healthy(await waitFor(s => s.ready && s.generation === 1), 1, 2);
    // A burst must deliver both IPC calls but launch only one debounced preview.
    await Promise.all([hook(), hook()]);
    healthy(await waitFor(s => s.ready && s.generation === 3), 3, 3);
    await new Promise(resolve => setTimeout(resolve, 750));
    healthy(JSON.parse(await ipc("snapshot")), 3, 3);

    assert.equal(await ipc("removeService"), "removed");
    await waitFor(s => !s.servicePresent);
    const missingTarget = await command(wrapper, ["urbit-theme", "themeChanged"]);
    assert.notEqual(missingTarget.code, 0, "Confirm the real Service IPC target was removed");
    await hook();
    const alive = JSON.parse(await ipc("snapshot"));
    assert.equal(alive.servicePresent, false);
    assert.equal(alive.previews, 3);
    assert.equal(alive.themeRequests, 3);
    await ipc("stop");
    const stopped = await Promise.race([shell.done, new Promise(resolve => setTimeout(() => resolve(null), 2000))]);
    assert.ok(stopped, "Disposable shell did not stop");
    assert.equal(stopped.code, 0);
    await hook(); // No shell at all is also a bounded, silent success.
    assert.doesNotMatch(shellOutput, /FIXTURE_STDERR|ERROR|ReferenceError|TypeError/);
    process.stdout.write("Real hook IPC: repository hook -> installed omarchy-shell -> qs ipc -> Service IpcHandler passed; single/burst debounce, usable shell, missing service/shell exit 0 verified\n");
  } catch (error) {
    if (shellOutput) process.stderr.write(shellOutput);
    throw error;
  } finally {
    await cleanup();
    process.removeListener("SIGINT", onInt);
    process.removeListener("SIGTERM", onTerm);
  }
}

module.exports = { run, missingPrerequisites };
if (require.main === module) run().catch(error => { console.error(error); process.exitCode = 1; });
