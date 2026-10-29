# Headless UI Checks

Run `node tests/qml/run.js` from the repository root. Requires Node, Omarchy
4.x sources, Qt's `qmltestrunner`, Python 3, `quickshell`, `qs`, Bash,
`timeout`, and the installed `/usr/share/omarchy/bin/omarchy-shell` wrapper.
`QMLTESTRUNNER` can override the Qt executable. Missing prerequisites fail by
default; `node tests/qml/run.js --allow-skip` explicitly permits a reported
skip, not a passing QML, SDK, or hook IPC result.
`OMARCHY_SHELL_SOURCE` can override `/usr/share/omarchy/shell`.

The runner copies this plugin and a small set of the installed native UI
components into a temporary directory under Node's `os.tmpdir()`. It replaces
the compositor window and all external IO with inert fixtures for QtTest.
Native inputs, buttons, typography, borders, and layout still render with
the offscreen software backend. No screenshots are captured.

A separate smoke test uses the real Quickshell Process SDK and a fake Python
helper to verify JSON stdin followed by EOF, bounded response parsing,
discarded stderr, handled exit 1, failed start, timeout, consecutive processes,
and preview refresh on the paused theme-hook path. Its
runtime and cache directories are also temporary. It cannot access a ship,
keyring, real helper, or installed plugin configuration.

The final test (`node tests/qml/hook-run.js` to run it alone, with the same
explicit `--allow-skip` rule) exercises the actual repository hook through the
installed `omarchy-shell` wrapper and real `qs ipc` into `Service.qml`'s
`IpcHandler`. It starts a disposable offscreen shell at a temporary
`$OMARCHY_PATH/shell/shell.qml`, with private runtime, cache, home, and XDG
directories and no display or desktop session environment. Only the inert
`helper.py` is copied as its backend; account state stays disconnected.
Diagnostic `testControl` IPC observes queue generations and preview counts,
removes the service, and stops the shell. Checks cover single and burst hook
delivery, exactly one debounced preview per burst, continued shell usability,
and bounded silent exit 0 with a missing service or shell. Processes and
temporary files are cleaned up, including on SIGINT/SIGTERM. Nothing is installed
and the current desktop shell/runtime is never targeted.

Use `TMPDIR=/tmp/opencode node tests/qml/run.js` to keep temporary fixtures there.
These tests do not verify real Wayland popup placement, compositor focus
acquisition, installation, actual theme-file parsing (the helper is inert), or
ship authentication. The older SDK smoke's theme calls are in-process; only
the final hook test verifies real IPC delivery.

## Networkless profile qualification

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/qml/run_profiles.py \
  --output /tmp/omabit-theme-profile-qml-check01
```

Use a fresh output directory. Bubblewrap is required; there is no host fallback.
The first phase runs the existing UI suite and profile routing tests with native
controls and inert I/O. The second starts a real Quickshell process and calls the
installed `omarchy-shell` wrapper through its private IPC socket. Only copied
Service/Model files and the inert fixture helper enter that shell; the plugin's
Python backend is never copied. The namespace excludes the host home, network,
D-Bus and compositor sockets. Source/fixture/native control hashes and all output
are retained. Both phases must pass, and QML warnings fail the first phase.

The tests cover late identity injection, separate stable/dev/edge panel and
service targets, distinct service state, targeted IPC delivery and removal of an
instance address. They do not install hooks, enable a real plugin, apply a desktop
appearance or qualify native Wayland positioning and complete profile switching.

The profile IPC phase also invokes the real copied theme/font hooks for all three
instances. Their notification helpers are inert binding recorders; theme events
reach only the matching real service target, font arguments stay with their own
helper, and remote-application suppression prevents another notification.

File ownership and the real metadata-selected notification command have a separate
networkless check with synthetic appearance files and inert desktop commands:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B tests/run_instance_desktop.py \
  --output /tmp/omabit-theme-profile-hooks-check-check01
```

It covers coexistence, restart, conflicting ownership, partial setup, misplaced
receipts, invalid hook names and preserving another instance's appearance/backup.
The shared visible desktop and native collection activation remain separate gates.
