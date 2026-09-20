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
