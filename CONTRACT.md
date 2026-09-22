# Helper Protocol (Version 2)

Invoke `python3 -B <plugin>/client/main.py <action>` with one bounded JSON
object on stdin followed by EOF. Stdout is one JSON response. Secrets must
never enter argv, environment, plugin settings, response output, or ordinary
application files. Credentials remain in Secret Service, scoped to origin.

## Actions

- `status {}`: local observation; no network or keyring access.
- `preview {}`: resolve the active Omarchy palette; no network/keyring access.
- `login {"url":"https://ship.example","code":"..."}`: add a ship without
  disconnecting other ships. Reject duplicate normalized origins before login.
  Store the new session, then add an independently identified row with
  `automatic:true` and `pending:true`. This records Add & Sync consent but does
  not itself poke settings; the service schedules that row's sync next.
- `set-auto {"enabled":false,"expectedAccount":{"id":"...","url":"...","ship":"~zod"}}`:
  change only that ship. Pause clears pending intent; enabling records pending
  intent even for an unchanged palette. No network or keyring access.
- `sync {"force":false,"expectedAccount":{"id":"...","url":"...","ship":"~zod"},"palette":{...}}`:
  publish to exactly that ship, only if automatic unless force is true.
  The optional palette is the validated preview snapshot shared by a fan-out
  batch. Without it, resolve the current local palette. No helper-wide fan-out:
  the service queues one bounded operation per ship, so one failure cannot
  prevent later ships from running. Each ship has independent pending/error/
  fingerprint state. A new theme replaces obsolete queued snapshots.
- `disconnect {"expectedAccount":{"id":"...","url":"...","ship":"~zod"}}`:
  stop and remove exactly that row. Best-effort revoke only the plugin's own
  Eyre session via an empty POST to `/~/logout`, never all/sid/host, never follow
  redirects. Attempt keyring deletion even if offline or expired. Failure to
  contact the ship or clean an unavailable keyring must not trap the row in the
  list; return a safe warning. Do not retain a token to retry remote logout.
  Failed local state saves remain errors, not successful removal.

`expectedAccount` is mandatory and captured when intent is created. Compare all
three fields under the process lock before any mutation. Each new login gets a
new opaque 64-hex ID, including removal/re-add at the same URL and ship: stale
queued actions must never affect a replacement login.

## Responses

Every response has `schemaVersion:2`, `ok:boolean`, `state:object|null`,
`palette:object|null`, `error:object|null`, and `warning:object|null`.
Errors/warnings contain `code`, safe `message`, and `retryable:boolean`.
Exit 0 on success (possibly with a warning), 1 on handled failure. Null state
means no authoritative state was available; retain the last trusted UI state.
Never return an unsaved working copy after a persistence failure.

Public state is `{"ships":[...]}`. Each row contains `id`, `ship`, `url`,
`automatic`, `pending`, `authenticationRequired`, `lastPublished` (UTC ISO or
empty), `lastTheme`, and `lastError` (safe text). No single active account exists.
An empty list is valid. Limit to 64 rows and bound response output accordingly.
Authentication failures pause that row; all other rows remain unaffected.

Palette is the existing Talon CustomTheme object: stable
`id:"omarchy-urbit-theme"`, name, dark, and six-digit RGB primary, secondary,
tertiary, background, and surface. Do not change Talon's ship schema.

## Persistence And Migration

Private nonsecret state stays under `$XDG_STATE_HOME/omarchy-urbit-theme`, using
the existing file lock, permissions, and atomic writes. Disk version 2 uses
`{"version":2,"ships":[...]}`; each record is a public row plus `fingerprint`.
The existing version-1 single-account record is validated and mapped into this
list, retaining its URL, ship, automatic preference, pending status, timestamps,
fingerprint, and existing origin-scoped keyring entry. Its old account hash is
the migrated ID. Empty version-1 state becomes an empty list. Migration can be
in memory for observations and persists on the next mutation; never read or
rewrite credentials just to migrate. No runtime protocol-v1 compatibility is
needed: the panel and helper are installed as one snapshot.

## Queue And UI

The QML service owns single-flight dispatch, coalescing, and per-ship retry
budgets. Startup and a debounced `urbit-theme themeChanged` hook first preview
once, then enqueue all syncing ships against that same palette. Refresh/status
alone never publish. Each failed ship retries with bounded backoff without
holding up ready work for other ships. Pause/remove cancel only their own queued
syncs/retries and take priority after the current operation. An in-flight
operation finishes first. Login secrets are never placed in a deferred queue.

Panel has exactly two normal sections: palette and ships. `+ urbit` reveals a
temporary URL/+code form under ships, with Add & Sync and Cancel. Rows display
@p, URL on hover only, X remove, and inverse sync/pause action. No separate
global consent or account section. Explain shared Talon appearance and accent
override in the add form. Clear passwords on submission, cancel, close, and
destruction. Errors and cleanup warnings belong inline with the ship section.

## Publication Guarantees

Keep the existing read/merge/put-entry behavior: preserve unrelated saved themes
from the snapshot, never replace ui-prefs, disable the separate accent override,
and re-send both entries for real publications even when matching. Passive
unchanged-palette checks remain read-only. Matching preexisting state cannot
confirm an unacknowledged resend. Theme and accent writes are not atomic, and
no compare-and-swap exists: concurrent Talon edits can still race. Confirmation
means ship storage, not proof that every Talon client rendered the update.
