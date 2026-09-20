// Headless native controls, with inert process/IPC/file adapters and no install.
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const { spawnSync } = require("node:child_process");
const hookSmoke = require("./hook-run.js");
const root = path.resolve(__dirname, "../..");
const shell = process.env.OMARCHY_SHELL_SOURCE || "/usr/share/omarchy/shell";
const qmlRunner = process.env.QMLTESTRUNNER || (fs.existsSync("/usr/lib/qt6/bin/qmltestrunner") ? "/usr/lib/qt6/bin/qmltestrunner" : "qmltestrunner");
const ui = ["Panel", "PanelController", "BarIconButton", "WidgetButton", "OpticalGlyph",
  "Button", "TextField", "PanelHero", "PanelSeparator", "PanelSectionHeader", "BorderSurface", "BorderOverlay"];
const commons = ["Style.qml", "Util.qml", "Border.qml", "BorderGeometry.js"];
const missing = [...ui.map(n => path.join(shell, "Ui", n + ".qml")), ...commons.map(n => path.join(shell, "Commons", n))].filter(p => !fs.existsSync(p));
for (const command of [qmlRunner, "quickshell", "python3"]) {
  const candidates = command.includes(path.sep) ? [command] : (process.env.PATH || "").split(path.delimiter).map(p => path.join(p, command));
  if (!candidates.some(p => { try { fs.accessSync(p, fs.constants.X_OK); return true; } catch (_) { return false; } })) missing.push(command);
}
missing.push(...hookSmoke.missingPrerequisites());
if (missing.length) {
  const allowSkip = process.argv.includes("--allow-skip");
  process.stderr.write(`${allowSkip ? "SKIP" : "FAIL"}: QML prerequisites unavailable: ${missing.join(", ")}\nNo QML, real SDK, or hook IPC checks ran.\n`);
  process.exit(allowSkip ? 0 : 1);
}
const temp = fs.mkdtempSync(path.join(os.tmpdir(), "urbit-theme-qml-"));
try {
  fs.mkdirSync(path.join(temp, "runtime"), { mode: 0o700 });
  const environment = { ...process.env, QT_QPA_PLATFORM: "offscreen", QT_QUICK_BACKEND: "software", QML_DISABLE_DISK_CACHE: "1",
    QT_QPA_PLATFORMTHEME: "", QT_QUICK_CONTROLS_STYLE: "Basic", WAYLAND_DISPLAY: undefined, DISPLAY: undefined,
    XDG_RUNTIME_DIR: path.join(temp, "runtime"), XDG_CACHE_HOME: path.join(temp, "cache") };
  fs.cpSync(__dirname, path.join(temp, "tests/qml"), { recursive: true });
  for (const name of ["Panel.qml", "Service.qml", "Model.js"]) fs.copyFileSync(path.join(root, name), path.join(temp, name));
  const imports = path.join(temp, "tests/qml/imports");
  for (const name of ui) fs.copyFileSync(path.join(shell, "Ui", name + ".qml"), path.join(imports, "qs/Ui", name + ".qml"));
  fs.writeFileSync(path.join(imports, "qs/Ui/qmldir"), "module qs.Ui\n" + [...ui, "KeyboardPanel"].map(n => `${n} 1.0 ${n}.qml\n`).join(""));
  for (const name of commons) fs.copyFileSync(path.join(shell, "Commons", name), path.join(imports, "qs/Commons", name));
  const result = spawnSync(qmlRunner, ["-input", path.join(temp, "tests/qml"), "-import", imports], {
    env: environment,
    encoding: "utf8", timeout: 30000
  });
  process.stdout.write(result.stdout || "");
  process.stderr.write(result.stderr || "");
  if (result.error) process.stderr.write(result.error.message + "\n");
  process.exitCode = result.status === 0 && !/QWARN|ReferenceError|TypeError/.test((result.stdout || "") + (result.stderr || "")) ? 0 : 1;
  if (process.exitCode === 0) {
    // A second, separate engine uses the real Quickshell Process SDK, but only
    // a fixture helper. All its cache/runtime files stay under the temp tree.
    const sdk = path.join(temp, "sdk");
    fs.mkdirSync(path.join(sdk, "client"), { recursive: true });
    for (const name of ["Service.qml", "Model.js"]) fs.copyFileSync(path.join(root, name), path.join(sdk, name));
    fs.copyFileSync(path.join(__dirname, "service-smoke.qml"), path.join(sdk, "shell.qml"));
    fs.copyFileSync(path.join(__dirname, "helper.py"), path.join(sdk, "client/main.py"));
    const smoke = spawnSync("quickshell", ["--no-color", "--path", path.join(sdk, "shell.qml")], {
      env: environment,
      encoding: "utf8", timeout: 10000
    });
    const output = (smoke.stdout || "") + (smoke.stderr || "");
    if (smoke.status !== 0 || !output.includes("SERVICE_SMOKE_PASS") || /FIXTURE_STDERR|ERROR|ReferenceError|TypeError/.test(output)) {
      process.stderr.write(output);
      if (smoke.error) process.stderr.write(smoke.error.message + "\n");
      process.exitCode = 1;
    } else process.stdout.write("Real Quickshell SDK: stdin/EOF, handled exit 1, failed start, timeout, consecutive processes, stderr discard and paused theme hook passed\n");
  }
} finally {
  fs.rmSync(temp, { recursive: true, force: true });
}
if (process.exitCode === 0) hookSmoke.run().catch(error => { console.error(error); process.exitCode = 1; });
