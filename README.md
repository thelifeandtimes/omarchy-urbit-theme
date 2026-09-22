# Omarchy Urbit Theme

Sync Omarchy's palette to multiple Urbit ships. Existing Talon clients on
desktop, Android, and iOS receive it through their normal `%settings` sync.
No Talon code changes or new Gall agent are required, and Talon Desktop does
not need to run on the publishing computer.

The widget has two sections: **current theme colors** and **ships**. Each ship
has its own session, sync/pause preference, retry state, and removal control.

This is an independent prototype, not an official Omarchy or Tlon plugin.
Localhost publication and missed-event recovery were verified with Talon
Desktop 1.7.8. Multi-ship isolation is covered by fake-server tests; mobile and
clean-machine acceptance remain release gates.
After the 0.2.0 upgrade, the operator also confirmed live syncing resumed when
the development ship was rebooted. This is single-ship acceptance, not evidence
of a live multi-ship or mobile rollout.

## Requirements

- Omarchy 4.x with its Quickshell plugin API, `omarchy theme color`, and hooks.
- Python 3.11+ (standard library only).
- `secret-tool` (libsecret) and an available, unlocked Secret Service provider.
- Ship URLs and their `+code`. Use HTTPS origins; HTTP is accepted only for
  literal localhost/loopback development origins. Reverse proxies must expose
  Eyre at the origin root. Redirects and subpath URLs are not followed.

There is no plaintext credential fallback. The helper does not access Talon's
credentials, browser cookies, or arbitrary HTTP proxy environment variables.
Omarchy plugins run as your user, not in a sandbox.

## Install

From a reviewed checkout of this repository:

```sh
python3 -B scripts/install.py install --enable
```

This copies a fixed snapshot to
`~/.config/omarchy/plugins/thelifeandtimes.urbit-theme/`, installs the uniquely
named `theme-set.d/omarchy-urbit-theme` hook using `omarchy hook install`, and
enables the right-side bar widget. Omit `--enable` to install without enabling.
It never edits `/usr/share/omarchy`, logs in, or grants sync consent.

To update an unmodified installer-managed snapshot:

```sh
python3 -B scripts/install.py install --replace --enable --restart-shell
```

Let running operations finish before updating. Existing syncing ships reconcile
on service startup. Editing this repository does not change the installed
snapshot. The installer refuses unmanaged installations, modified installed
files, and conflicting hooks. A failed enable step leaves the snapshot
installed; resolve the shell error, then rescan and enable it again.

The root manifest also supports Omarchy's native Git installer. That installer
does not install auxiliary hooks: explicitly install `hooks/omarchy-urbit-theme`
with `omarchy hook install theme-set <path-to-hook>` afterward. Do not apply the
snapshot installer over a native Git checkout.

## Use

1. Open the `~` widget. Its first section previews the active theme's colors.
2. Select **+ urbit** under Ships to reveal the URL and masked `+code` form.
3. Select **Add & Sync**. This explicitly enables shared Talon theme syncing for
   that ship and schedules an initial publication.
4. Add more ships the same way. Future theme changes fan out to every syncing
   ship using the same resolved palette snapshot.

Rows show only the ship's `@p`. Hover over the name to see its target URL.

| Control | Meaning |
| --- | --- |
| Pause icon | This ship is syncing. Click to pause it. |
| Circular-arrow icon | This ship is paused. Click to resume and resend the current palette. |
| X | Remove this ship, log out its plugin session, and forget its saved connection. |

Pause and removal cancel only that ship's queued updates and retries. Other
ships continue. A currently running operation finishes first; a slow or offline
ship has bounded timeouts and cannot indefinitely prevent later ships from
running. The add form never queues credentials while waiting for another
operation: enter `+code` once the form is ready.

**Adding or resuming changes shared Talon settings on that ship**, including
disabling Talon's separate profile/custom accent override. Native Tlon Messenger
and other ship apps use separate settings namespaces and are unaffected. There
is no new per-device Talon override.

Paused ships retain their last published appearance. Removal also leaves ship
theme settings intact; choose another theme in Talon to change them afterward.
Expired authentication pauses only the affected row. Remove and add that ship
again to renew its session.

### Removal And Logout

X makes a bounded, best-effort logout request using only the plugin's own Eyre
session, then attempts to delete that origin's keyring entry. It never requests
logout of all sessions, and it never reads or removes Talon's credentials.

An offline ship, expired cookie, or missing keyring entry does not trap you in
the list. The row is removed from local state first. If remote logout or keyring
cleanup cannot be confirmed, an inline warning explains the limitation. A
locked/unavailable keyring may still contain the session: unlock it and remove
the **Omarchy Urbit Theme session** item for that origin using your keyring
manager. The plugin does not retain a token to retry remote logout.

Failed local state saves are reported as errors, not successful removal. Removing
and re-adding the same ship creates a new connection ID so stale queued work
cannot target the replacement session.

### Retries And Reconciliation

Each ship retries transient failures independently with bounded backoff (2, 5,
and 15 seconds). Ready work for other ships is not held behind a retry timer.
The latest theme replaces obsolete queued palettes. Changes made while the
shell/plugin is not running reconcile at startup for syncing ships.

Passive refresh and unchanged-palette checks never repeatedly reassert a theme
over a later manual choice made in Talon. To re-send it deliberately, pause and
resume that row. This emits fresh settings events even if the ship already
stores identical values. Refresh Preview only refreshes local presentation and
status; it does not publish.

## Existing Connections

Version 0.2 migrates the version-1 single-account state into the ship list while
retaining its URL, ship, sync/pause preference, pending status, timestamps, and
palette fingerprint. The existing origin-scoped Secret Service entry is reused:
no re-login or credential migration is required. An empty old account becomes
an empty list. Observation can migrate in memory; the next mutation persists
the new disk format atomically. Do not downgrade to the single-account helper
after the new format has been saved.

For this protocol upgrade, restart the Omarchy shell after replacing the files
(the explicit `--restart-shell` flag above does this). A plugin rescan alone can
retain old QML/JavaScript in the running engine, pairing the old panel with the
new helper and showing an invalid-response error. Restarting refreshes the bar
and popups; it does not restart Talon or the ships.

Up to 64 origins can be configured. Duplicate normalized URLs are rejected;
different origins remain independently authenticated even if they identify the
same ship. Avoid adding aliases for the same ship unless that is intentional.

## Palette And Ship Contract

| Omarchy | Talon |
| --- | --- |
| `accent` (fallback `blue`, then `foreground`) | `primary` |
| `blue` (fallback primary) | `secondary` |
| `green` (fallback secondary) | `tertiary` |
| `background` | `background` |
| `lighter_background` (fallback background) | `surface` |
| `mode` | `dark` |

Omarchy's resolver supplies aliases and derived colors from
`~/.local/state/omarchy/current/theme/colors.toml`. Edit a user theme and apply
it normally; unapplied source edits are not published.

The publisher uses `%settings`, mark `%settings-event`, namespace `talon`,
bucket `ui-prefs`, entry `themes`. It updates and selects the stable
`omarchy-urbit-theme` ID, preserving unrelated saved themes from the snapshot
it read. Entry values are JSON serialized as strings. The `accent` entry is
updated separately with `enabled:false`, preserving its other fields. Malformed
existing data is rejected, never replaced with an empty library.

Actual publications re-send both entries, process Gall acknowledgements, and
verify ship storage. Already-matching values cannot prove an unacknowledged
re-send succeeded. Confirmation means ship storage, not that every client has
rendered it. Theme and accent updates are not atomic; partial writes remain
pending and the next attempt reconciles both.

### Compatibility Limits

- This uses Talon's existing five-color model. Text, selection, errors, and
  surface ramps are derived by Talon, not exact Omarchy copies. Fonts and other
  styles cannot be added to unchanged Talon clients through this schema.
- `%settings` has no compare-and-swap. A concurrent Talon edit between read and
  write can be overwritten even when verification succeeds. Use one automatic
  publisher per ship and avoid editing its theme library during publication.
- A client can miss an event between initial settings scry and subscription.
  Let it finish connecting, then pause/resume its plugin row to resend. This
  recovery was verified with Talon 1.7.8 without restarting or modifying Talon.
- Sleeping clients catch up on reconnect. This plugin cannot repair client sync
  defects. Include Android fresh-login and ship-switch behavior in acceptance
  testing; affected versions may need a restart.
- Omarchy compatibility is based on the installed 4.0 interfaces and upstream
  4.0.4 sources, not a promise covering every future 4.x release.

## Credentials And Diagnostics

Login uses stdin, not process arguments. Only an origin-bound Eyre session is
saved in Secret Service under application `omarchy-urbit-theme`; `+code` is
never saved. The session grants ordinary ship-login access, not theme-only
permission. Trust the plugin and destination. QML/Python cannot guarantee
memory erasure, but credential fields and handoff buffers are cleared promptly.

Private nonsecret state lives in
`$XDG_STATE_HOME/omarchy-urbit-theme/state.json` (normally
`~/.local/state/omarchy-urbit-theme/state.json`), with permissions, atomic writes,
and a process lock. Ship mutations carry the connection ID, origin, and `@p`;
the helper validates all three before using credentials.

Local observation commands, requiring no credentials:

```sh
python3 -B client/main.py status <<< '{}'
python3 -B client/main.py preview <<< '{}'
```

Status never accesses the keyring or network. Preview runs Omarchy's read-only
color resolver. See [CONTRACT.md](CONTRACT.md) for protocol v2.

## Uninstall

Pause all rows and let the current operation finish. Use X on each row first
if you want sessions logged out and forgotten, then run:

```sh
python3 -B scripts/install.py uninstall
```

This removes only the unmodified installer-owned plugin and its hook. It
preserves state and ship settings. Retained syncing rows can resume on reinstall.
Native Git installations should use Omarchy's plugin removal tools and remove
their auxiliary hook separately after checking ownership.

## Testing

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py'
node --test tests/model.test.js
omarchy plugin validate .
node tests/qml/run.js
```

Python tests use local fake Eyre HTTP/SSE servers, isolated state, and fake
keyrings. They cover migration, distinct sessions, partial failures, logout,
offline removal, stale actions, and full helper subprocesses. Model tests cover
shared snapshots, per-ship retries and cancellation, and error recovery.

QML checks use offscreen fixtures and installed Omarchy controls, including
credential clearing and row interactions. Real SDK/process and hook IPC tests
run in a disposable shell with an inert backend, never the current desktop.
Missing prerequisites fail unless `--allow-skip` is explicit. The popup test
adapter does not prove real Wayland placement, focus, or screen constraints.

Before release, verify real panel login/keyring persistence; two independent
test ships receiving the same theme; one offline while the other succeeds;
pause/resume/removal; a retained v1 connection after upgrade; desktop/mobile
propagation; and a clean Omarchy installation with larger text/long ship lists.
No automated test installs hooks, logs into a production ship, or changes Talon.

## License

MIT.
