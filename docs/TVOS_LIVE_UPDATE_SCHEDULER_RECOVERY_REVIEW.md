# tvOS live-update scheduler recovery — architecture review, 2026-09-16

Architecture/forensic review only. No runtime implementation, no publication,
no version bump, no Apple TV access (Example Room untouched; Secondary Room not
needed — see "Real-device evidence" below). Baseline: source `1f2a3d3`,
published 0.9.35.

## Question

Can a due scheduled backup safely recover and run after a live/silent
Backup Pro update without requiring a Kodi restart, without ever running
from guessed/default/stale tvOS settings?

## A — live-update lifecycle

Proven from code:

- `service.py` constructs one `TvOSSettingsGuard(service_initialization=True)`
  and one `BackupScheduler` at module-import time; this runs once per
  service-interpreter lifetime.
- The safety marker (`settings-safety-session.xml`, under the add-on
  profile dir, VFS-backed) stores `{schema, version, pid}`.
  `marker_decision()`: `None` → `bootstrap`; malformed → `invalid`;
  `pid` differs → `restart`; `pid` same + `version` differs → `live_update`;
  else → `same_session`.
- On `live_update`, `initialize()` unconditionally writes a fresh marker
  (new version, same pid) then calls `_mark_unsafe('live_update')` — no
  adoption/recovery branch exists for this decision today (unlike
  `bootstrap`/`invalid`, which have a late-service-adoption branch).
  `_mark_unsafe` sets `UNSAFE_PROPERTY`, `UNSAFE_REASON_PROPERTY`,
  `SAFETY_READY_PROPERTY` on `Window(10000)`.
- The only code path that ever clears `UNSAFE_PROPERTY` is
  `decision == 'restart'` (pid change) inside `initialize()`
  (`_clear_process_state()`). `rebaseline_settings()` cannot clear it —
  it explicitly no-ops when `is_unsafe()`. So once poisoned by
  `live_update`, the session stays poisoned until Kodi's OS process
  actually restarts (new pid) — indefinitely, with no code-level timeout.
- `BackupOperationSettings` (frozen dataclass, `repr=False` at the class
  level to prevent accidental logging of secrets) already captures every
  scheduler-relevant input, including private destination/credential
  fields, from a fresh `xbmcaddon.Addon()` wrapper via `capture()`.

Inferred from documented/established Kodi behavior (not directly provable
from this repo alone, but consistent with how this project's own marker
design and prior Example Room evidence already treat pid/version):

- Program-type invocations (`default.py`) run as a fresh Python
  interpreter/sub-interpreter per invocation; `xbmc.service`-type add-ons
  (`service.py`) run in one long-lived interpreter for the life of the
  Kodi app process. Both share the same OS process, so `os.getpid()` is
  stable across them and changes only on a genuine Kodi restart — this is
  exactly what the marker's pid field is designed to detect.
- `Window(10000)` properties are a Kodi GUI-layer global, shared across
  every Python interpreter in that one process — this is why Program and
  the service can coordinate through `UNSAFE_PROPERTY` at all; the whole
  guard architecture already depends on this being true.
- A live/silent add-on update rewrites on-disk files and Kodi's AddonMgr
  metadata cache immediately (so a *fresh* `xbmcaddon.Addon()` wrapper's
  `getAddonInfo('version')` reflects the new version right away — the
  `live_update` branch exists specifically because this project's own
  design already assumes this). It is a different, unproven question
  whether Kodi also tears down and re-imports the *already-running*
  service interpreter's Python module cache, or leaves it running the old
  bytecode until Kodi fully restarts. Community Kodi behavior for
  `xbmc.service` add-ons is that a running service keeps executing
  already-imported modules until the app restarts — but this project has
  no direct on-device evidence of it specifically for a live update (only
  for genuine restarts, evidenced in the Example Room 0.9.28-0.9.31 logs).

Requires real-device validation (see "Real-device evidence" below):

- Whether Kodi tvOS ever silently updates an installed add-on while Kodi
  keeps running at all (vs. only applying queued updates at next app
  launch), and if so, whether that leaves the running service interpreter
  on old bytecode or restarts it in place. Neither outcome changes the
  `pid`/`version` marker semantics above (pid is process-scoped either
  way), but it does determine *which already-shipped version's guard
  code* is the one that actually detects the transition, which matters
  only for staged rollout of a recovery fix (see "Implementation outline").

## B — scheduler behavior today

- `BackupScheduler.start()`'s loop: `if guard and not guard.poll(): self.enabled
  = False; sleep(500); continue`. Since `poll()` returns `False` forever
  once unsafe, this `continue` skips the `if self.enabled: if next_run <=
  now: doScheduledBackup()` block on every subsequent tick — permanently,
  not just once.
- **Does live update poison future scheduler attempts until restart?
  Yes**, unconditionally, with no code-level recovery today.
- **Does the scheduler remain due after a blocked attempt? Yes** —
  `next_run` (in-memory) and `next_run.txt` (on disk) are never written
  while blocked, since `doScheduledBackup()`/`findNextRun()` are never
  reached. The due state is preserved, just never re-evaluated.
- **Does it retry at the next foreground opportunity? Only via a full
  Kodi restart.** `BackupScheduler.__init__()`'s missed-schedule catch-up
  (`next_run.txt` + `schedule_miss`) only runs when a *new*
  `BackupScheduler` is constructed — i.e., only when `service.py` runs
  again, i.e., only on an actual Kodi restart (new pid, which is also what
  clears `UNSAFE_PROPERTY` via the `restart` decision). Before that, no
  in-process retry exists at all.
- **Can a scheduled run be silently marked consumed even though it never
  ran? No** — confirmed no write to `next_run`/`next_run.txt` occurs on
  the blocked path.
- **Is duplicate execution possible? No** — `doScheduledBackup()` is only
  reachable from the single `next_run <= now` check inside one serialized
  loop.
- **Real, separate gap found**: once poisoned, the scheduler goes fully
  silent to the user. `_mark_unsafe()` only logs to `kodi.log` (not
  user-visible); the existing `getString(30237)` "restart required"
  notification is shown by `doScheduledBackup()`'s own guard checks, which
  are never reached because `start()`'s `continue` skips that call
  entirely. A user has no way to learn why their scheduled backups
  stopped short of reading logs. This is orthogonal to the recovery
  question and worth fixing regardless of which design is chosen.

## C — candidate designs

**Design 1 — fresh re-admission after live update.** Viable, and the
basis of the recommendation, but only if the "require exact match against
the existing session baseline" step is dropped. That baseline
(`SETTINGS_SIGNATURE_PROPERTY`) was captured *before* the code (and
possibly `settings.xml` schema) changed — a version bump can legitimately
add/rename/re-default a setting, which would make an old-baseline
equality check fail permanently after every real release, defeating the
purpose. The safe version of Design 1 instead treats live-update recovery
like the existing `restart` path treats a fresh session: establish a
**new** baseline from live data rather than validate against the old one,
but — unlike `restart` (a full app relaunch, presumed to guarantee a
non-stale tvOS settings view by construction) — still requires the
existing double-capture consistency check *and* a non-empty-destination
check before trusting it, since a live update does **not** guarantee the
tvOS settings cache has flushed. This carries the same residual
stale/default risk this project already explicitly accepted for ordinary
same-session `admit_backup_snapshot()` (see
`docs/TVOS_OPERATION_SETTINGS_REVIEW.md` → "Admission protocol:
unresolved safety requirement") — not a new risk category, an extension
of an already-shipped one. Viable on tvOS to the same extent the existing
snapshot admission already is.

**Design 2 — in-memory last-known-good snapshot held by the service.**
Only helps in the world where the service interpreter survives a live
update unrestarted (unproven — see Task A); if Kodi instead restarts the
service interpreter on update, this design has nothing to fall back on and
Design 1 is needed anyway. It also opens a materially larger secret-in-
memory exposure window (held indefinitely until the next due time, not
just for one bounded operation) and needs its own invalidation logic
against legitimate mid-window settings edits (`rebaseline_settings()`
already exists but isn't wired to invalidate a held snapshot). Not
recommended as the primary mechanism; a plausible future optimization
*once* Task A's open lifecycle question is resolved on real hardware, not
before.

**Design 3 — durable scheduler configuration (secrets included).** Would
require persisting `dropbox_key`/`dropbox_secret`/`remote_path`/
`remote_path_2` (which can embed SMB `user:pass@host`) into a second,
Backup-Pro-owned store outside Kodi's own settings storage. This directly
contradicts the project's own existing invariant ("private destination/
provider values are not persisted into public window properties,
manifests, logs, or `.agent`") and creates exactly the stale-secret risk
this task is trying to prevent: a credential rotated in Kodi's real
settings would not automatically update a shadow copy. Not recommended;
explicitly rejected per the task's own instruction not to casually
recommend secret duplication.

**Design 4 — durable non-sensitive state + separate credential
recovery.** Solves only the half that was never the bottleneck — the
non-sensitive fields are cheap and safe to re-read live (Design 1 already
does this reliably) — and still needs *some* credential-recovery
mechanism for the other half, which collapses back into Design 3's
rejected secret duplication or an equally complex undesigned mechanism.
It shifts the trust problem rather than solving it. Not recommended as a
standalone design.

**Design 5.** No better alternative is suggested by the current code; a
refined Design 1 is the recommendation.

## D — security invariant

An unattended scheduled backup may execute after a detected `live_update`
transition if and only if **all** of:

1. The detected transition is specifically `live_update` — `bootstrap`,
   `invalid`, `initialization_failed`, `snapshot_capture_failed`,
   `safety_check_failed`, and `settings_rebaseline_failed` remain
   unconditionally blocked pending restart, exactly as today.
2. At least two independently captured, complete `BackupOperationSettings`
   snapshots, taken via a freshly constructed `Addon()` wrapper and spaced
   at least one scheduler tick apart (not back-to-back in the same
   call), compare fully equal to each other (dataclass equality,
   including private fields, compared only in memory).
3. The add-on version observed in both captures is identical, non-empty,
   and matches the currently installed version (rejects a mid-install
   transient read).
4. The captured destination is non-empty (`remote_selection` plus the
   corresponding presence flag) in both captures — a blank/default
   destination never admits a scheduled run, matching the existing
   `remoteConfigured()` gate.
5. Admission is evaluated fresh for the one currently-due operation only
   and is never cached or reused for a later due time.
6. Admission never clears, weakens, or bypasses `UNSAFE_PROPERTY` /
   `SAFETY_READY_PROPERTY` / `SETTINGS_SIGNATURE_PROPERTY` for any
   interactive (Program) code path.
7. No private field, or anything derived from one, is ever written to a
   window property, log line, marker file, manifest, or `.agent`/durable
   file — only presence/boolean indicators, matching the existing
   `_PRESENCE_SETTINGS` pattern.
8. Any failure at (2)-(4) leaves the schedule's due state untouched (no
   `next_run` write, no consumption) and defers to the next eligible
   scheduler tick or the next real Kodi restart's catch-up path, with at
   most one recovery attempt per due cycle (see Task F).

Each clause is independently unit-testable against a mocked host, in the
same style as `tests/test_tvos_settings_guard.py`'s existing coverage.

## E — interactive vs. scheduler policy

| Action | After `live_update`, before restart |
| --- | --- |
| Interactive Program open / Settings / Restore / Advanced editor / Status / Help / Launcher | Blocked, restart required — unchanged from today. |
| Manual backup | Blocked, restart required — unchanged from today. |
| Scheduled backup | **New**: may attempt the recovery protocol in Task D at its own next due cycle; on success, runs exactly one snapshot-backed operation; on any failure, stays blocked and defers. |

A successful scheduled recovery does **not** clear interactive
restart-required state. This falls out structurally, not from a flag:
the recovery path only ever calls a new, narrowly scoped guard method
invoked solely from `doScheduledBackup()`; it never touches
`UNSAFE_PROPERTY`/`SAFETY_READY_PROPERTY`/`SETTINGS_SIGNATURE_PROPERTY`,
so `default.py`'s existing `allow_operation()` gate at Program-open time
keeps seeing the sticky unsafe state exactly as before. This matches the
existing precedent of `operation_revoked(admitted_snapshot=True)`, which
already lets one admitted snapshot finish despite a *later* unsafe
transition without ever un-poisoning the session for anything else —
this design applies the same asymmetry going forward instead of backward.

## F — missed-schedule semantics

- **Due status preserved**: yes, today and under the recommendation —
  `next_run`/`next_run.txt` are never written while blocked.
- **Retry on next eligible opportunity**: today, only via full Kodi
  restart. The recommendation adds an earlier opportunity (the next
  scheduler tick where `next_run <= now`) specifically for `live_update`,
  without changing the missed-schedule file/flag semantics.
- **No duplicate run**: preserved — the recovery attempt only ever
  happens inside the existing single `doScheduledBackup()` call site,
  itself gated by the existing serialized `next_run <= now` check.
- **No silent loss**: violated today (see Task B) — independent of this
  recovery design, add one non-blocking notification the first time
  `live_update` is observed while the scheduler is enabled, mirroring the
  existing restart-required notification that currently never fires on
  this specific silent-`continue` path.
- **No retry storm**: preserved only if the recovery attempt is placed
  inside `doScheduledBackup()` (naturally rate-limited to once per due
  cycle) rather than inside the generic `poll()` (called every ~500ms
  regardless of due-ness). This is an explicit implementation constraint,
  not a detail — placing it in `poll()` would reintroduce a retry storm.
- **Required scheduler-state changes**: none to `next_run.txt`'s format.
  The only new state is ephemeral (Window properties for the
  recovery-specific baseline), not durable/file-based — no migration or
  versioning concern.

## G — real-device evidence

None gathered or required this session. The remaining lifecycle question
(Task A's "requires real-device validation" item) cannot be answered by
read-only log inspection alone — it requires *observing* a live update
happen while Kodi keeps running, which is a mutation this task's boundary
forbids performing. No Secondary Room access occurred.

**Documented future experiment, human-authorized only**: on a disposable
or otherwise expendable Apple TV, with explicit human authorization to let
Kodi's own auto-update (or a manual in-UI update) apply while Kodi stays
running (no force-quit, no manual restart), then read-only:
(a) check whether `kodi.log` shows the service's own startup-only log
lines (e.g. "late service initialization adopted existing process
baseline") recurring near the update timestamp — presence suggests the
service interpreter restarted in place; absence alongside a clean
`live_update`-reason transition suggests it kept running old code; (b)
read the marker XML file's `pid` attribute before/after via the existing
read-only file-access method — a changed pid there would mean Kodi's own
process restarted, which is a different (and already-handled) case
entirely. This still requires a new, explicit human decision, since
letting an update apply is itself an "update on Apple TV" event under this
task's stated boundary — it is not something to treat as pre-authorized
by this review.

## H — implementation-ready test matrix

Already covered by existing tests (`tests/test_tvos_settings_guard.py`),
unaffected by this recommendation:
`test_live_update_in_same_pid_is_unsafe`,
`test_admitted_snapshot_continues_after_later_settings_view_change`,
`test_admitted_snapshot_still_stops_for_lifecycle_or_readiness_risks`
(includes `live_update` among reasons that still revoke an in-flight
admitted snapshot), `test_sensitive_values_are_not_present_in_signature`,
`test_simple_selection_diagnostic_excludes_sensitive_values`,
`test_guard_never_calls_set_setting`.

Net new, required before implementation:

1. Clean session + due scheduled backup — unaffected regression.
2. Live update before scheduled time — recovery engages at the due tick,
   backup runs without restart.
3. Live update after scheduled time, before the next tick — recovery
   engages at the very next tick (not gated on Kodi app foreground/
   background; that tvOS constraint is orthogonal and unchanged by this
   design — see the note below).
4. Service interpreter restarted by the update (if real-device evidence
   ever shows this happens) — pid changes, ordinary `restart` baseline
   path handles it; assert the new recovery code is a no-op here.
5. Settings remain correct after update — recovery admits, values match.
6. Settings become stale/default after update — double-capture mismatch
   or empty-destination check refuses; due state preserved.
7. Non-sensitive fields stable but a private field changed mid-capture
   (e.g. a concurrent edit) — double-capture equality catches it, refuse.
8. Successful scheduler recovery, end to end.
9. Failed scheduler recovery, end to end, including the new notification.
10. Interactive access remains restart-blocked immediately after a
    successful scheduled recovery — the critical structural-isolation
    regression test.
11. Admitted scheduled backup later sees `settings_view_changed` —
    existing coverage, confirm unaffected.
12. Repeated ticks while due do not duplicate the backup or the recovery
    attempt (gated by the single `doScheduledBackup()` call site).
13. Missed run stays due across one or more failed recovery attempts,
    and the full-restart catch-up path still works as final fallback.
14. Secrets never enter logs/window properties/durable state — extend
    the existing secret-safety tests to cover the new recovery code path
    and its new properties/log lines specifically.
15. No retry storm: recovery attempted at most once per due cycle even
    across many ticks before the next due time (placement test, proving
    it isn't reachable from `poll()`).
16. Only `live_update` engages scheduler recovery — `bootstrap`,
    `invalid`, and every other unsafe reason are proven to still hard-block
    scheduled runs with no recovery attempt.

Note on tvOS foreground: the product requirement's background-suspension
behavior ("Kodi cannot run scheduled backups while backgrounded") is a
separate, pre-existing, already-accepted platform limitation this
recovery design does not change — it only removes the *additional*,
Backup-Pro-caused block that a live update currently adds on top of it.

## I — recommendation

Adopt the refined Design 1: scheduler-only fresh re-admission after
`live_update`, gated by double-capture consistency and non-empty
destination, never comparing against the pre-update baseline, never
touching interactive-facing guard state, implemented solely inside
`doScheduledBackup()`'s existing call site. This is the least invasive
option consistent with the security invariant in Task D, does not
duplicate secrets (Design 3/4 rejected), does not depend on an unproven
Kodi lifecycle fact to be safe (unlike Design 2, which only helps in one
of two possible worlds), and does not leave unattended scheduling
needlessly stranded when a real, safe recovery path exists — matching the
task's explicit instruction not to over-conservatively disable scheduling
when it doesn't have to be.

Bootstrapping note: because a running service interpreter may keep
executing old bytecode across a live update (Task A), this recovery logic
only takes effect starting with the *next* live update after it ships —
the very next release, and every one after it, self-perpetuates the
capability, mirroring the already-accepted "first marker-aware run
requires one restart" precedent this project shipped in 0.9.23.

## Implementation outline (not performed this session)

1. Add `TvOSSettingsGuard.admit_scheduler_recovery_snapshot(capture)` —
   narrowly scoped, called only from `doScheduledBackup()`. Requires
   `UNSAFE_REASON_PROPERTY == 'live_update'`; performs the double-capture
   plus non-empty-destination checks from Task D; on success returns one
   `BackupOperationSettings`, writing only a new, separate,
   non-interactive-facing baseline property; on any failure returns
   `None` and leaves all existing state untouched.
2. Wire it into `doScheduledBackup()` only, immediately after the
   existing `allow_operation('scheduler_backup')` check returns blocked
   for `live_update` specifically — never inside `poll()`.
3. Add the missing user notification for the silent-`continue` gap found
   in Task B (independent, small, low-risk fix).
4. Full Task H matrix, focused then full suite, compilation, diff review.
5. Release and Example Room validation only after explicit human
   authorization, as with every prior tvOS-guard change in this project.

## Smallest next step

Get an explicit go/no-go decision on this recommendation from Eric. If
approved, implement it as one focused, testable commit scoped exactly to
the outline above — no Apple TV work is needed to make that decision; it
is a pure code-design choice, and the one open real-device fact (Task A)
does not change which design is safe, only when its self-perpetuation
begins.
