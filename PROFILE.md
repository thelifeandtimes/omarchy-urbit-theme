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
them to its Lua configuration's `rgba(RRGGBBAA)` representation. CSS gaps are
expanded to named Lua sides (`top`, `right`, `bottom`, `left`), not arrays:
Hyprland accepts arrays but defaults their missing named sides to zero. One- to
four-value integer shorthand is supported. Vectors and Bezier points remain
arrays; floats, booleans, and strings retain their types.

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
Before success, the coordinator compares all requested theme, font, color,
shell, file, window, and animation values with a fresh capture. Comparison
normalizes gap shorthand, color case, gradient angles, ordering of named
animation entries, and floating-point readback precision. Additional local
defaults are allowed. A mismatch names the differing fields, retains the
desired application, and never adopts incorrect readback as a successful
baseline or publishes it as a local selection. Machine-level shell overrides
are intentionally outside the captured theme layer.

Version 0.3.9 automatically reapplies existing generated overrides from older
adapters on the first connected reconciliation, even when current values happen
to match. The replacement carries an adapter marker, so future restarts need
not reapply a semantically identical profile. Application failures remain
pending and can be retried.

## Talon bridge

The plugin maps the superset into `talon/ui-prefs/themes`, keeping the existing
stable `omarchy-urbit-theme` ID and unrelated saved themes. It also disables the
separate accent override. Preview and publication share one mapping function.
The original five-color mapping remains, including surface=lighter_background
(fallback background). Extras are explicit: text=foreground, error=red,
link=blue, and muted=raised=`""` (Talon Auto). Talon derives secondary text from
the foreground/surface pair and generates its raised-surface ramp. This avoids
using a subdued desktop shade as enabled text or flattening raised surfaces to
the normal surface color. The shared Omarchy `muted` value remains intact.

Selection is accent-based rather than copying Omarchy's already-subdued
selection background: Talon composites the input at 40% opacity. When needed,
the adapter attenuates the accent toward the surface until selected body text
retains at least 4.5:1 contrast on both base background and surface. For source
text already below that threshold, its lower existing contrast is the target.
This checks the sRGB composite; cards and other Material-specific surfaces
still need client visual acceptance. The stored Omarchy palette is not modified.

An unavailable extra is `""` (Auto), never a missing key that would retain a
client's old override. Auto muted and raised are always explicit so old overrides
are cleared on every Talon client. The palette preview labels a derived value
Auto rather than pretending the input string is a rendered Material color.

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

## Development ownership contract

The experimental profile implementation uses a shared HOME-local ownership lock
and versioned active/applied records in
`~/.local/state/omarchy-urbit-theme-ownership/`. The active record includes a random
selection ID retained through same-profile restarts; hook notifications bind to
that selection. Only the owning coordinator changes appearance. Generated includes
check the applied record and an ordinary theme-name guard; exact old include blocks
can acquire the outer owner guard without rewriting their files. The adapter3
content hash and code are replaced as one atomic file. Unknown/unreceipted files
are preserved and application refuses instead of silently adopting edited code.

This supersedes the earlier automatic legacy-override repair and hook-side
removal behavior for this development checkout. Explicit legacy migration and a
complete collection rollback transaction are not yet qualified. The owner record
is coordination metadata, never ship credentials or an inter-plugin security
boundary. Hooks no longer dispatch a second UI publication request.

The next migration checkpoint narrows the unreceipted-file refusal above: adapter2
bytes reconstructed exactly from desktop.json's observed or applying profile can
be adopted automatically. The original is retained as appearance-before-owner-v3.lua
before atomic replacement. A mismatched backup, linked/special file, edited bytes,
unknown header or missing matching application evidence refuses in place. Migration
itself runs no desktop command and does not claim applied ownership; normal startup
still reconciles and verifies appearance. Interrupted backup/replacement can retry
without replacing the retained original. Arbitrary legacy code is never evaluated
to decide whether it is owned.

## Experimental offline appearance checkpoints

The development helper exposes explicit local checkpoints for collection recovery:

```sh
python3 -B client/sync.py checkpoint-appearance
python3 -B client/sync.py restore-appearance CHECKPOINT_ID RESTORE_REQUEST_ID
```

Run these from the exact retained plugin instance after its helper has drained and
exited. The HOME ownership lock refuses a concurrent cooperating Theme helper.
`CHECKPOINT_ID` is the returned SHA-256 (64 lowercase hexadecimal characters).
Choose a fresh 32-character lowercase hexadecimal request ID for an intentional
restoration; retry that same pair after an interruption. A completed request only
checks current readback, without reapplying. Another intentional restoration needs
a new request. These are development recovery primitives; they do not select or
activate a plugin collection, and their results keep `collectionReady: false`.

A checkpoint requires this instance to own a verified applied appearance, with no
pending local application. Uncertain ship publication may remain and is preserved.
The private version1 snapshot lives in the instance's HOME appearance directory,
`~/.local/state/<instance-state-name>/checkpoints/<sha256>.json`, separately from
XDG ship/account and sync journals. It binds the exact instance, effective profile,
frozen local theme files and a hash of personal Hyprland configuration after exact
generated includes are removed. Snapshot/restore never reads a ship credential or
contacts Eyre. Stock themes and the mutable generated source are not consulted
while restoring. Locally trusted ancillary theme files are retained as bytes;
remote profiles still cannot supply executable theme code.

Snapshot bounds are 64 regular owned files, 1 MiB per file, 4 MiB combined data and
8 MiB serialized JSON. Unknown versions, changed hashes, symlinks, special files,
changed personal configuration or edited retained restore assets refuse intact.
Native theme-set locking covers observation and frozen files, avoiding a mixed
snapshot during a theme switch. A running native switch yields a retryable refusal.

Each restore request has a private version1 receipt under `restore-receipts/`.
An `applying` receipt is synced before native/configuration effects; `verified` is
synced only after effective profile, current theme files and owner readback pass.
An interrupted attempt remains applying and blocks this profile's ordinary sync
startup until its exact retry succeeds. A new request cannot displace an incomplete
one. The bounded history retains up to 128 requests; it never prunes evidence to
make room. Existing `desktop.json`, including pending/attempted publication and
applying intent, is not rewritten by either command. Resuming normal sync remains
an independent action that must reconcile that retained intent.

Wallpaper is explicitly outside version1: the checkpoint records
`backgroundPolicy: preserve-current`, excludes background directories/links and
restores with `OMARCHY_THEME_SKIP_BACKGROUND=1`. This is not full desktop rollback.
Native compositor behavior, font/shell restart, app-retint subprocesses, arbitrary
older helpers and cross-profile transition fencing require separate qualification.
A completed local receipt does not authorize starting another collection or
restoring an older plugin that does not understand this recovery contract.
