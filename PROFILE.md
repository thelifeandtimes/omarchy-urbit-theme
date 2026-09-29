# Shared Appearance Profile — Version 1

One superset, one authoritative setting, no new Gall agent or runtime package.
Consumers select the fields they implement. The plugin maintains the live Eyre
subscription and bridges colors into Talon's existing settings.

## Storage and ordering

`%settings`, mark `%settings-event`, desk `omarchy-urbit-theme`, bucket
`appearance`, entry `current`. The entry value is a JSON-encoded string.
Subscribe to `/desk/omarchy-urbit-theme`; scry the same desk after subscription
acknowledgement and after reconnect. A fact invalidates the current snapshot;
it is not a historical value to replay over a newer scry.

The last put accepted by the ship wins. There are no client-clock comparisons,
peer elections, or conflict dialogs. An explicitly queued offline change can
win when delivered later. Startup reads the hub before publishing. An empty
hub is initialized from the first connecting desktop. A removed current entry
can likewise be initialized by a connected desktop.

IDs are 32 lowercase hexadecimal characters. `deviceId` identifies an install;
`updateId` identifies a selection, not a comparable revision. After an ambiguous
write, matching readback confirms storage. If another update replaced the
pre-write value, the stale retry yields to that update.

## Fields

| Field | Meaning |
| --- | --- |
| `schemaVersion` | Integer `1` |
| `updateId`, `deviceId` | Update and originating installation identities |
| `theme` | Installed Omarchy theme slug; not a path or shell command |
| `colors` | Complete resolved `omarchy theme color --all` dictionary, including mode, semantic colors, ANSI aliases, and gradient values |
| `font` | Concrete fontconfig monospace family, also used by the Omarchy shell |
| `shell` | Flat `section.key` dictionary from the applied theme's `shell.toml`; strings mirror Omarchy's parser |
| `windows` | Effective Hyprland appearance options, each with its typed `getoption` representation |
| `animations` | `curves` and explicitly overridden animation `rules` |
| `files` | Supported declarative application theme files |

`shell` covers the theme's typography, spacing, bar dimensions, control states,
surface fills/borders/gradients/opacity, popups, tooltips, notifications,
launcher, menus, polkit, lock screen, and image picker. Machine-level
`~/.config/omarchy/shell.toml` remains a local override (including text zoom).
Theme sizes are shell logical pixels. Alpha values follow Omarchy's 0–1 range.

`windows` keys are the explicit compatibility list `OPTIONS` in
`client/profile.py`. They cover inner/outer gaps, border widths/gradients,
rounding and rounding power, opacity/dimming, blur parameters, shadow/glow
parameters, animation enablement, and group/groupbar appearance. Newer options
absent on an older compositor are omitted at capture. The receiver reports
unsupported application rather than silently claiming success.

Examples of typed values:

```json
{
  "decoration:rounding": {"int": 8},
  "decoration:blur:enabled": {"bool": true},
  "general:gaps_out": {"css": "10 10 10 10"},
  "general:col.active_border": {"gradient": "ff7aa2f7 ffbb9af7 45deg"}
}
```

Hyprland's serialized gradient colors are **AARRGGBB**. The adapter converts
them to its Lua configuration's `rgba(RRGGBBAA)` representation. Vectors, CSS
quad values, floats, booleans, and strings retain their types.

Each curve has `name` and four numeric `points` (X0, Y0, X1, Y1). Each animation
rule has `name`, `enabled`, `speed`, `bezier`, and `style`. Internal compositor
animation leaves are excluded. This preserves Omarchy's configured animation
inheritance rather than replacing inherited children with guessed defaults.

`files` supports btop, Chromium, Helix, icon-theme selection, keyboard RGB,
Obsidian CSS, VS Code color theme, and Pi/Claude/Hermes/T3 theme data. See
`THEME_FILES` for exact filenames. Text-only profiles are limited to 256 KiB,
with individual app files limited to 64 KiB. Fonts, wallpaper images and icon
assets must already be installed. The selected theme supplies local assets;
this protocol does not copy binaries or arbitrary executable theme files.

## Applying on Omarchy

The selected theme must exist and the font family must be installed. Missing
prerequisites leave the shared profile intact and show an actionable status.
The plugin builds an ownership-checked `urbit-synced` working theme from the
installed local theme and shared data, then lets Omarchy generate/apply its
normal application configurations. Git-installed theme code restrictions are
retained. The displayed current theme name is restored to the shared slug.

Effective window appearance is emitted as validated Lua data calls in
`~/.local/state/omarchy-urbit-theme/appearance.lua`. A trailing guarded include
is added to the user's `hyprland.lua`; its original contents are preserved in
`hyprland-before-sync.lua`. The include is active only for the shared slug.
A deliberate local theme selection clears the generated window override before
the next capture. Remote applications suppress both publication hooks.

The application transaction is persisted before applying, because changing the
font can restart the shell. On restart it reconciles against the hub again.
Application failures retain the desired profile and can be retried.

## Talon bridge

The plugin maps the superset into `talon/ui-prefs/themes`, keeping the existing
stable `omarchy-urbit-theme` ID and unrelated saved themes. It also disables the
separate accent override. All six rc74 extras are explicit: text=foreground,
muted=muted, raised=lighter_background, error=red, selection=selection,
link=blue. An unavailable extra is `""` (Auto), never a missing key that would
retain a client's old override. The original five-color mapping remains.

Talon rc74 does not consume font, geometry, or effect fields. Those values are
available to future consumers, but storing them does not change existing apps.
Talon edits never select a desktop profile. The bridge has a separate bounded
publication lane, so an offline secondary ship cannot block desktop application.
It checks the authoritative update before retries. Talon's two entry writes
are still non-atomic and have no compare-and-swap; later reconciliation is
required after a concurrent update or partial failure.

## Local coordination

Credentials remain origin-scoped in Secret Service. `state.json` retains the
version-2 account format. `desktop.json` holds nonsecret desktop state separately:
device identity, chosen hub identity, pause preference, last observed profile,
local baseline, pending publication, in-progress application, and hook receipt.
A single plugin-owned process holds `desktop.lock`. The existing account helper
retains its separate short-lived process protocol and locking.

Native Git installations register the two auxiliary hooks on coordinator
startup, under the same desktop lock and before accessing credentials. Ownership
hashes live in `native-hooks.json`, outside the Git checkout. Identical hooks
are reused; updates replace only a previously recorded, unmodified hook.
Both destinations are checked before either is changed. Snapshot installations
continue to use their installer's hook ownership and upgrade procedure.

The first configured ship is the default desktop hub. Additional ships receive
the same profile's Talon colors. The labeled `Sync desktops` switch explicitly
selects the hub; switching it off pauses desktop sync on this computer. A replacement
login for the same ship/origin adopts the hub afresh rather than replaying an
old connection's pending action. Pausing the hub row also stops desktop sync.
Desktop pause retains the current appearance; resume adopts the hub.
