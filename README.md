# Omarchy Urbit Theme

Publish Omarchy's active palette to your Urbit ship. Existing Talon clients on
desktop, Android, and iOS receive it through their normal `%settings` sync.
Talon does not need to be running on this computer, and no Talon code changes
or new Gall agent are required.

The Omarchy panel includes ship sign-in, a palette preview, manual publication,
automatic theme following, and delivery status. Sign-in alone never publishes.
This is a prototype with automated fixture coverage and verified live localhost
publication to Talon Desktop 1.7.8. Mobile and clean-machine acceptance remain
release gates. See [Testing](#testing).

## Requirements

- Omarchy 4.x with its Quickshell plugin API, `omarchy theme color`, and hooks.
- Python 3.11+ (standard library only).
- `secret-tool` (libsecret) and an available, unlocked Secret Service provider.
- An HTTPS ship origin and its `+code`. HTTP is accepted only for literal
  localhost/loopback development origins. Reverse proxies must expose Eyre at
  the origin root; redirects and subpath URLs are not followed.

There is no plaintext credential fallback. The helper does not read Talon's
credentials, use browser cookies, or support arbitrary HTTP proxy environment
variables. Omarchy plugins run as your user, not in a sandbox.

## Install From This Repository

Run from the repository root after reviewing the code:

```sh
python3 -B scripts/install.py install --enable
```

This copies a fixed snapshot to
`~/.config/omarchy/plugins/thelifeandtimes.urbit-theme/`, installs its uniquely
named `theme-set.d/omarchy-urbit-theme` hook using `omarchy hook install`, and
enables the widget in the right side of the bar. It never changes files under
`/usr/share/omarchy`, authenticates, or publishes a theme.

Omit `--enable` to install without enabling. The installer refuses existing
unmanaged installations and hooks. For an update to an unmodified managed
snapshot:

```sh
python3 -B scripts/install.py install --replace --enable
```

Pause publishing and wait for any operation to finish before updating. Editing
this repository does not alter the installed snapshot. Locally modified
installed files are never silently overwritten. A failed enable step leaves the
snapshot installed; rescan and enable it again after resolving the shell error.

The root manifest is also compatible with `omarchy plugin add` once a trusted
Git URL is published. Native plugin installation does not install auxiliary
hooks: explicitly install `hooks/omarchy-urbit-theme` with
`omarchy hook install theme-set <path-to-hook>` in that case. Do not run the
snapshot installer over a native Git checkout.

## Use

1. Open the `~` bar widget, enter the ship URL and `+code`, and select **Sign In**.
2. Review the resolved palette and the ship identity.
3. Accept the ship-wide publishing notice, then select **Publish Now** or
   **Enable Auto**.
4. Change themes through Omarchy normally. Automatic mode publishes after the
   theme-set hook runs, even with Talon Desktop closed.

The shared theme applies to Talon installations connected to that ship. This
does **not** change native Tlon Messenger's separate theme setting or other
applications on the ship. No new per-device Talon override is introduced.
The plugin also disables Talon's separate ship-backed profile/custom accent
override so it cannot replace the imported primary color.

**Pause** stops queued publications and automatic retries, but an in-flight
operation finishes first. **Disconnect** disables publishing and forgets this
plugin's local keyring session. Neither restores an old theme nor removes
themes from the ship. Disconnect is local credential removal, not server-side
session revocation. To change the shared appearance afterward, select another
theme in Talon.

Changes made while the shell/plugin is not running are reconciled on its next
startup if automatic publishing was previously enabled. Unchanged palettes are
not repeatedly asserted over a later manual theme choice made in Talon;
**Publish Now** explicitly reasserts the current Omarchy theme and sends fresh
settings events even if the ship already stores the same palette. Retries use the
latest local palette, not a queue of obsolete theme changes. There are three
automatic retries (2, 5, and 15 seconds); after that, retry manually or wait for
the next theme change/startup. Expired authentication requires signing in again.

## Palette And Ship Contract

| Omarchy | Talon |
| --- | --- |
| `accent` (fallback `blue`, then `foreground`) | `primary` |
| `blue` (fallback primary) | `secondary` |
| `green` (fallback secondary) | `tertiary` |
| `background` | `background` |
| `lighter_background` (fallback background) | `surface` |
| `mode` | `dark` |

Omarchy's own resolver supplies legacy aliases and derived colors. Inputs come
from `~/.local/state/omarchy/current/theme/colors.toml` and `theme.name`, matching
Omarchy 4.x's own paths. Edit a user theme and apply it normally; unapplied source
edits are not published.

The publisher uses `%settings`, mark `%settings-event`, namespace `talon`,
bucket `ui-prefs`, entry `themes`. The entry value is JSON serialized as a string.
It updates the stable `omarchy-urbit-theme` theme ID and selects it with
`activeId`, preserving unrelated themes from the snapshot it read. The `accent`
entry is updated separately with `enabled: false`, preserving its other fields.
Malformed existing data is rejected, never replaced with an empty library.

Publication processes Gall's acknowledgement and scries the resulting state.
An HTTP success or missing acknowledgement alone is not confirmation. Matching
readback can resolve an ambiguous network outcome. Partial writes remain
pending and visible as failures.

An unchanged re-send requires a Gall acknowledgement: reading values that were
already present cannot prove another notification was sent. Confirmation refers
to the ship, not to every client's rendering state.

### Talon Still Shows An Old Theme

Let Talon finish connecting, then select **Publish Now** again. Talon can miss
a settings event between its initial settings scry and subscription, especially
during first-login seeding. The ship may already contain the new palette while
that client still shows its old one. Re-sending the same entry emits a fresh
fact without restarting Talon or changing its code. Passive startup/unchanged
theme checks remain read-only, so they do not fight manual Talon theme choices.

This recovery was verified live with Talon 1.7.8: the initial publication landed
before its subscription, and re-sending immediately updated the running UI and
its theme cache. If a client remains stale, verify it is connected to the same
ship and has an active settings subscription; a ship acknowledgement cannot
guarantee every client has applied the event.

### Limits

- This maps to Talon's existing **five-color** model. Foreground, selection,
  error colors, and surface ramps are derived by Talon, not exact Omarchy copies.
- Fonts, font sizes, and other styles cannot be published to unchanged Talon
  clients. This version deliberately does not pretend to support them.
- `%settings` has no compare-and-swap. A concurrent Talon theme edit between
  reading and writing can be overwritten, even if verification succeeds. Use
  one automatic publisher and avoid editing Talon's theme library during a
  publication. One publisher alone does not eliminate races with Talon edits.
- Theme and accent updates are separate writes, not a transaction. A failure
  may leave one applied; the next publication reconciles both.
- Connected clients receive live settings events; sleeping/disconnected clients
  catch up when they reconnect. This plugin cannot repair a client's sync bugs.
  Source review of Talon identified a possible Android fresh-login/ship-switch
  attachment defect. Include those cases in acceptance testing; a restart may
  be needed with affected versions.
- API compatibility has been checked against installed Omarchy 4.0 alpha
  interfaces and upstream 4.0.4 sources, not every future 4.x release.

## Credentials And Local State

The panel hands login input to the helper over stdin. The `+code` is never saved
or passed in process arguments. Only an origin-bound Eyre session is persisted
in Secret Service under application `omarchy-urbit-theme`. This session grants
normal ship-login access, not a narrowly scoped theme-only permission. Use only
a plugin and ship origin you trust. QML/Python cannot guarantee secret memory
erasure, but the input and handoff buffers are cleared promptly.

Nonsecret status, consent, and publication fingerprint live in
`$XDG_STATE_HOME/omarchy-urbit-theme/state.json` (normally
`~/.local/state/omarchy-urbit-theme/state.json`), with a private directory,
atomic writes, and a process lock. Pending intent and queued actions are bound
to the connected ship and origin. No credentials or filled configuration
belong in this repository.

For diagnostics, these commands observe local state only and accept no secrets:

```sh
python3 -B client/main.py status <<< '{}'
python3 -B client/main.py preview <<< '{}'
```

The preview runs Omarchy's read-only palette resolver. Status never accesses
the keyring or network. The helper's machine protocol is in [CONTRACT.md](CONTRACT.md).

## Uninstall

Pause and wait for the current operation. Select **Disconnect** first if you
want the stored session forgotten, then run:

```sh
python3 -B scripts/install.py uninstall
```

This disables and removes only an unmodified installer-owned plugin and its
hook. It preserves local state and ship settings. A retained automatic setting
can resume on reinstall, so pause/disconnect first. A native Git installation
should instead be removed with Omarchy's plugin tools, with its auxiliary hook
removed separately after checking ownership.

## Testing

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py'
node --test tests/model.test.js
omarchy plugin validate .
node tests/qml/run.js
```

The Python suite uses a local fake Eyre HTTP/SSE server, isolated state, and
injected keyring adapters, including full helper subprocess tests. It never
accesses a live ship or real keyring. The
model suite checks consent/account binding, queues, retries, and safe responses.
The QML runner requires Qt 6's `qmltestrunner`, Quickshell, and Omarchy's installed
shell sources; it uses temporary fixtures/offscreen rendering without installing
the plugin. It also sends the actual repository hook through the installed
`omarchy-shell` IPC wrapper to a disposable shell with an inert backend.
Missing prerequisites fail unless `--allow-skip` is explicit. QML tests are not
evidence of real Wayland popup placement or live ship delivery.

Before release, use a dedicated test ship and unchanged Talon desktop/mobile:

1. Sign in through the real panel and check keyring persistence across shell restart.
2. Publish a conspicuous palette; verify two clients update and saved themes survive.
3. Close Talon Desktop, switch Omarchy themes, and verify the phone still updates.
4. Test real hook IPC, rapid changes, offline retry, expired login, pause, and disconnect.
5. Reconnect an offline client, then test Android fresh login and A/B ship switching.
6. Test a clean Omarchy release, narrow screens, keyboard focus, and larger text.

No automated test installs hooks, logs into a production ship, or changes Talon.

## License

MIT. This is an independent integration, not an official Omarchy or Tlon plugin.
