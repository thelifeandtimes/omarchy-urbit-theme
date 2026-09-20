# Helper Protocol

The plugin invokes `python3 -B <plugin>/client/main.py <action>` with a bounded
JSON object on stdin, followed by EOF. No secret goes in argv, environment,
plugin settings, output, or application-managed files. Stdout is exactly one
JSON response. Stderr must not contain secrets or remote response bodies.

Actions:

- `status`: local status only; no keyring access or network.
- `preview`: resolve the current Omarchy palette; no ship writes or keyring access.
- `login`: input `{ "url": "https://ship.example", "code": "..." }`;
  authenticate and store only the session in Secret Service. Automatic
  publishing is off after login. Refuse implicit replacement of a connected
  account: disconnect first.
- `set-auto`: input `{ "enabled": true, "expectedAccount": {"url": "https://ship.example", "ship": "~zod"} }`; save consent only, no ship writes.
  Disabling clears pending intent. Enabling requires a connected account.
  A disabled-to-enabled transition records pending intent to publish now;
  unlike passive startup, explicit re-enabling reasserts the current palette.
- `sync`: input `{ "force": false, "expectedAccount": {"url": "https://ship.example", "ship": "~zod"} }`; publish only when automatic is enabled;
  explicit `force: true` permits a manual publication while automatic is off.
- `disconnect`: input `{ "expectedAccount": {"url": "https://ship.example", "ship": "~zod"} }`; forget the local session/keyring entry and disable publishing;
  leave ship settings alone.

Every response has `schemaVersion: 1`, `ok: boolean`, `state: object|null`,
`palette: object|null`, and `error: object|null`. An error has `code`, a safe
human-readable `message`, and `retryable: boolean`. Exit 0 on success, 1 on
handled failure. Unexpected failures must also be sanitized.

On failures before authoritative state is available (including lock contention),
`state` is null. Consumers must retain the last known state, not treat this as
a disconnect. Failed mutations report reloaded persisted state, never an
uncommitted working copy. `expectedAccount` is mandatory for sync, set-auto,
and disconnect, captured when intent is created and checked under the lock.
A mismatch returns permanent `account-changed` without mutating the account.

State contains `connected`, `ship`, `url`, `automatic`, `pending`,
`authenticationRequired`, `lastPublished` (UTC ISO string or empty),
`lastTheme` (string), and `lastError` (safe string). Empty state uses false
booleans and empty strings. Palette is a Talon CustomTheme object with `id`,
`name`, `dark`, `primary`, `secondary`, `tertiary`, `background`, and `surface`.
Its stable ID is `omarchy-urbit-theme`.

One helper mutation runs at a time under a process lock. State is atomic and
ship-bound under `$XDG_STATE_HOME/omarchy-urbit-theme` (usual home fallback).
Sync always rereads the current palette and remote library. Keep unrelated
themes/fields, reject malformed existing data, do not replace the ui-prefs
bucket. Disable the separate accent override while preserving its fields.
Preservation is relative to the snapshot read: no compare-and-swap exists, and
concurrent Talon edits can be lost. Theme and accent writes are not atomic.
Only mark publication successful after confirming ship state. A lost ack is
not success by itself; verification can resolve an ambiguous outcome.
Actual publications always poke both entries, even if already matching, to
re-notify clients that missed an earlier fact. Passive identical-palette sync
is suppressed before publication. For already-matching entries, readback cannot
resolve a lost acknowledgement: the notification is still unconfirmed.

The QML service owns the operation queue, debounce, and bounded retry timer.
All helper invocations run off the UI thread. Queued work contains no secrets;
login runs only when idle and its stdin buffer is immediately cleared after
handoff. `status` is an observation, never a publication trigger. Startup and
`urbit-theme themeChanged` IPC request `sync` when automatic is enabled.
Do not retry permanent errors or expired authentication. Pause/disconnect
cancel scheduled retries; an already-running network operation finishes first.
