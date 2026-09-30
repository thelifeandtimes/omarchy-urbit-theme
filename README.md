# Network Theme

One ship-owned appearance profile for your Omarchy computers, with an
eleven-color Talon bridge. Change appearance on one connected computer and the
others follow. Install on another computer, add the same ship, and it adopts
the existing profile before publishing anything.

The plugin uses the ship's existing `%settings` agent. No additional Gall
agent, server, daemon installation, or Python package is required. Talon does
not need to run on the publishing computer.

## What syncs

- The installed Omarchy theme selection and full resolved palette.
- The monospace font family used by Omarchy's shell and terminals.
- Theme typography, shell spacing, bar dimensions, control states, and surface
  colors, opacity and borders.
- Window corners, border widths/gradients, inner/outer gaps, opacity/dimming,
  blur, shadow/glow options, and groupbar appearance.
- Configured animation curves and rules.
- Supported declarative application theme files (btop, Chromium, Helix, icons,
  keyboard RGB, Obsidian, VS Code colors, and AI-terminal theme data).

The profile is one superset in one namespace. Consumers can use whichever
fields they support. [PROFILE.md](PROFILE.md) specifies every field and the
capture/application contract.

Fonts, wallpapers and icon assets must already be installed. A missing named
theme or font is reported in the panel; the desired profile remains available
for Retry after installation. Wallpaper selection follows the installed theme;
image files are not uploaded. Executable theme code comes only from the
installed local theme, subject to Omarchy's Git-theme restrictions. Display
scaling and machine-level `~/.config/omarchy/shell.toml` overrides, including
text zoom, remain local.

Talon rc74+ receives primary, secondary, tertiary, background, surface, text,
muted, raised, error, selection and link colors. Talon currently defines its
fonts and shapes in application code; those additional profile fields are
available to future consumers but do not alter unchanged Talon clients.

## Requirements

- Omarchy 4.x with its Lua-based Hyprland configuration, Quickshell plugin API,
  `omarchy theme color`, and hooks. Developed against Omarchy 4.0.4.
- Python 3.11+ (standard library only).
- `secret-tool` and an available, unlocked Secret Service provider.
- Ship URL and `+code`. HTTPS origins are supported; HTTP is permitted only for
  literal localhost/loopback development origins. Eyre must be exposed at the
  origin root; redirects and subpath URLs are not followed.

This is an independent plugin, not an official Omarchy or Tlon product.

## Install

On the other Omarchy machine, run:

```sh
omarchy plugin add https://github.com/thelifeandtimes/omarchy-urbit-theme.git --enable
```

`omarchy plugin install` is an alias for `omarchy plugin add`. Omarchy asks for
confirmation and bar placement. For a noninteractive install, append `--yes`.
The plugin registers its theme and font hooks automatically when first enabled;
no separate setup script or manual hook commands are needed.

Open the palette-and-tilde widget (**Network Theme**) and select **+ urbit**, enter the same ship URL and `+code`,
then **Add & Sync**. The first connected ship is the desktop hub:

- An **empty hub** starts with this computer's appearance.
- An **existing hub** is adopted by this computer before it publishes anything.

Use this command for subsequent native Git updates:

```sh
omarchy plugin update thelifeandtimes.urbit-theme
```

Hook setup is idempotent and preserves modified or conflicting hooks, reporting
any conflict in the panel. Its ownership receipt is stored in local state, so
the Git checkout stays clean for native updates. Hooks from an older native
version are updated when the new coordinator starts. If an update retains an
old QML instance, `omarchy restart shell` loads the new code.

### Snapshot or bundle installation

From a reviewed checkout or an extracted release bundle, the snapshot installer
is also available:

```sh
python3 -B scripts/install.py install --enable
```

Use the same steps on the second Omarchy machine with the same ship. Its old
local theme is not uploaded on joining. No pairing code or device roster is
needed. Additional ships receive the hub profile's Talon colors. Select a
different desktop hub by turning on that row's **Sync desktops** switch.

The snapshot installer copies reviewed files to
`~/.config/omarchy/plugins/thelifeandtimes.urbit-theme/`, installs uniquely named
`theme-set.d/omarchy-urbit-theme` and `font-set.d/omarchy-urbit-font` hooks, and
enables the widget. It refuses unmanaged or modified installations and hook
conflicts. It never edits `/usr/share/omarchy` or logs in for you.

Do not apply the snapshot installer over a native Git checkout. On a machine
with an existing snapshot, continue using the snapshot upgrade command below,
or pause syncing, uninstall that snapshot, then use the native install command.
Account state and credentials survive uninstall unless explicitly removed.

### Upgrade an existing snapshot

Let running account operations finish, then run:

```sh
python3 -B scripts/install.py install --replace --enable --restart-shell
```

Existing account IDs, sessions, and per-ship preferences are retained. Unmodified
0.2 and earlier 0.3 snapshot inventories are recognized. The first existing
ship becomes the hub; if it is syncing and the namespace is empty, it seeds
the profile from this desktop. A paused hub stays paused.

Restarting the shell loads the new QML and starts the profile coordinator. The
repository is separate from the installed snapshot: editing the checkout does
not update the running plugin.

### Move an unreleased snapshot to another computer

Build a self-contained source/install bundle (no credentials or local state):

```sh
python3 -B scripts/package.py /path/to/omarchy-urbit-theme-0.3.7.tar.gz
```

Copy it to the other machine, extract it, and run the install command above
from its `omarchy-urbit-theme-0.3.7` directory. This works without publishing a
Git commit or installing any extra runtime dependencies.

## Controls and behavior

The Network Theme hero shows the current theme and light/dark mode, with a
custom palette outline with an optically centered tilde, tinted by the theme. Ten compact
swatches fit on one line; hover for each role and hex value. Muted is hidden
from the preview but remains part of the eleven-color Talon publication.
Desktop sync and its status live directly on each ship row. **+ urbit** sits
beside the Ships heading.

| Control | Meaning |
| --- | --- |
| Sync desktops switch | Select that ship as the one desktop hub and adopt its profile; switch it off to pause desktop sync |
| Retry arrow beside a ship error | Retry connection or application after resolving a missing theme/font |
| Ship pause / play button | Pause / resume that ship's Talon publishing; pausing the hub also stops desktop sync |
| × | Remove the connection, best-effort log out the plugin's session, and forget its saved credential |
| Refresh arrow in the title | Refresh local palette and account status; does not create publication intent |

Theme/font hooks and local appearance observation detect deliberate changes.
The latest unsent local change replaces older queued work. A live Eyre
subscription invalidates the hub snapshot and triggers a fresh read; startup
and reconnect also reconcile. Normal online propagation is within seconds,
plus the time Omarchy takes to apply a theme.

**The last profile accepted by the ship wins.** There are no client-clock
comparisons. An explicitly queued offline selection may become the latest
selection when delivered on reconnect. A lost acknowledgement does not let a
stale retry overwrite a subsequently accepted update. An ordinary startup does
not promote the computer's stale local appearance.

Remote application does not echo as a new user choice. The application is
recorded before it starts so font-triggered shell restarts can recover. Talon
publication has a separate bounded lane: an offline secondary ship cannot
block desktop propagation. Account pause/removal waits for an in-flight
account mutation to finish. Login credentials are never queued behind it.

Adding/resuming a Talon destination selects the stable `omarchy-urbit-theme`
theme and disables Talon's separate accent override, preserving unrelated
saved themes. Talon edits do not control Omarchy. Passive unchanged-profile
checks do not repeatedly override a later manual Talon theme selection.
Theme and accent updates are separate, without compare-and-swap; concurrent
Talon library edits can still race. Confirmation is ship storage, not proof
that every desktop/mobile client has rendered the update.

## Local files and credentials

`+code` is handed over on stdin and never saved. Only the plugin's origin-bound
Eyre session is stored in Secret Service under application
`omarchy-urbit-theme`. There is no plaintext credential fallback. Existing
Talon/browser credentials are not accessed. The session grants ordinary ship
login access rather than theme-only permission.

Nonsecret account and desktop state lives under
`$XDG_STATE_HOME/omarchy-urbit-theme` (normally
`~/.local/state/omarchy-urbit-theme`). Atomic state writes and separate account
and desktop process locks protect it. The plugin-owned live helper runs only
while the shell owns the plugin; it is not a separately installed service.

On the first remote application the plugin creates:

- An ownership-checked working theme at `~/.config/omarchy/themes/urbit-synced`.
- A generated `~/.local/state/omarchy-urbit-theme/appearance.lua` and a guarded
  trailing include in `~/.config/hypr/hyprland.lua`.
- `hyprland-before-sync.lua`, preserving the original configuration before that
  include was added.

The generated window override applies only to the selected shared slug. A
deliberate local theme selection clears it before capturing the new theme.
Existing theme sources are preserved. Edits to the generated working theme are
detected and must be preserved before it can be regenerated.

Removal leaves published settings intact. Logout and keyring cleanup are
bounded best-effort operations; a locked keyring or offline ship produces an
inline warning without trapping the row in the list.

## Diagnostics and tests

Read-only local observations:

```sh
python3 -B client/main.py status <<< '{}'
python3 -B client/main.py preview <<< '{}'
omarchy-shell urbit-theme desktopStatus
python3 -B scripts/check-desktop.py
```

The final command captures local appearance and validates generated Lua using
Hyprland's config-only verifier, without applying it.

Version 0.3.2 fixes a first-sync failure on computers with large font collections:
font availability checks query only the requested family rather than exceeding
the command runner's output limit with the entire catalog. Desktop command
errors now identify the command and distinguish missing executables, timeouts,
output limits, and nonzero exits without exposing arguments or captured output.

```sh
python3 -B -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py'
node --test tests/model.test.js
omarchy plugin validate .
node tests/qml/run.js
```

Tests include two complete sync processes with isolated homes, synthetic-only
keyrings, inert desktop commands, and a loopback HTTP/SSE ship. They cover
first login, both directions, effects, pause/resume, restart, the Talon bridge,
lost acknowledgements, concurrent arrivals, missing fonts, unsafe profile
rejection, and installer upgrades/rollback. QML tests include real SDK/process
and hook IPC checks in a disposable shell. Missing QML prerequisites fail unless
`--allow-skip` is explicitly requested.

Two physical machines and Talon desktop/mobile visual acceptance remain useful
operator checks; the simulations do not claim those have been performed.

## Uninstall

Pause desktop sync and all ship rows. Use X first if you want their plugin
sessions logged out and forgotten. For a native Git installation:

```sh
omarchy plugin remove thelifeandtimes.urbit-theme
```

Omarchy's native remover does not run plugin cleanup callbacks. The two hook
files may remain; their helper is no longer present, so they cannot publish.
They can be removed manually or reused by a later installation. For an
installer-managed snapshot:

```sh
python3 -B scripts/install.py uninstall
```

This removes the unmodified installer-owned plugin and both hooks. State, the
last applied appearance, generated appearance files, and ship settings are
retained. To stop using the last window override, remove the clearly marked
`omarchy-urbit-theme appearance` include from `hyprland.lua` and reload Hyprland;
do not replace later personal edits with the old backup wholesale.

## License

MIT.
