# tvOS operation settings review — 2026-09-13

Baseline: source `fff8651`, published 0.9.30; clean `agent/codex` at review
start. Architecture review only; no runtime implementation or publication.

## Newly verified evidence

Read only Example Room `Library/Caches/kodi.log` through paired-device Xcode
file receive. Local evidence: `/tmp/bp-architecture.cQeN1J/kodi.log` (temporary,
not a permanent archive). No Kodi action was invoked.

- 09:57:08.920: outer `backup_selection_boundary` allowed.
- 09:57:10.984: inner `backup_selection_consumption_boundary` allowed;
  `unsafe_before=false comparison=performed`.
- 09:57:10.985: inner diagnostic matched the baseline, with database and
  thumbnails false. Thus AF3 capture finished in about two seconds this run.
- 09:57:19.027: `late service initialization adopted existing process baseline`.
- 09:57:19.530: service detected changed fields including database/thumbnails,
  and marked `settings_view_changed`.
- 09:57:29.217: simple selection was
  `addons,addon_data,database,playlists,thumbnails,config`; planned sets added
  `skin_config`.
- 10:00:05.464: Program open blocked with `existing_unsafe_property`, prior
  reason `settings_view_changed`.

The earlier inference that the entire approximately 20-second delay was AF3
capture was incorrect. At backup.py:1203-1208 each selector read is interleaved
with `_addBackupDir()` and its filesystem planning. Earlier directory walks
can delay the database/thumbnails reads even AFTER the inner guard.
The exact individual getter timestamps are not logged. The service mismatch
is the first logged divergence, not proof of the instant each getter flipped.

## Architectural flaw and why the fixes did not solve it

An allowed guard observation and later settings reads are separate operations.
They do not share a captured input object. Moving the guard cannot bind later
reads to earlier validated values. Even one guard directly before the loop
does not cover delays between its iterations. The diagnostic snapshot itself
is another independent observation, not the planner input.

0.9.25 removed a settings write but did not establish consistent operation
inputs. 0.9.28 corrected the false-bootstrap condition. 0.9.29 and 0.9.30
added real checks at successively later points; both passed before the view
changed. Tests substituted collection or toggled unsafe during AF3 capture;
they did not reproduce a flip inside an earlier selected directory's walk.

Fresh wrappers are not immutable snapshots. Kodi 21.3's Python Addon binding
gets an add-on object from the manager and registers/unregisters it as
updateable; getters delegate to that underlying object. It exposes no atomic
multi-setting capture in this interface:
https://raw.githubusercontent.com/xbmc/xbmc/21.3-Omega/xbmc/interfaces/legacy/Addon.cpp

## Service and upstream comparison

The late-initialization message is not proof the service interpreter just
started. The same service log thread was already producing repository lookup
warnings at 09:56:45.904. `poll()` calls `initialize()` repeatedly; the
bootstrap/adoption branch is not restricted to the first initialization.
It can reread a missing marker, rewrite it, and advertise ready later.
The settings flip follows that message, but causation is unproven.

Potential amplifiers introduced by Backup Pro: one-second inventory/settings
polling, repeated wrapper creation even for metadata/log calls, persistent
marker I/O during initialization checks, independently written shared window
properties, and AF3 capture plus more filesystem planning. Reading upstream
Kodi implementation is allowed; Backup Pro must still never directly access
NSUserDefaults. No service-disable, restart, or other device experiment was
performed.

Compared against locally recorded `upstream/matrix` at
`cdd8faf990e688e9ff522acd3375e90539ac49e7` (not claimed to be current remote):
original Backup retains a module-global Addon wrapper and also reads settings
dynamically. It already has a background scheduler, but no Backup Pro guard,
one-second inventory observer, safety marker, or AF3 capture. Its apparent
success does not prove atomicity or immunity; retained-object lifetime and
different timing are plausible explanations requiring controlled evidence.

## Proposed operation contract

Use an immutable, typed, per-operation input object, acquired before
`XbmcBackup` configures its destination and before dialogs/AF3/filesystem work.
No global override of utils; pass inputs explicitly to backup and dependent
helpers. Snapshot once for each actual backup action, not Program-menu open,
and never reuse it for the next operation. Do not retain a Kodi wrapper as
the snapshot: copy primitive values into an immutable representation.

Build selected set IDs entirely in memory from this object before any file
walk. Every later backup decision must use those inputs. Unknown settings
keys must fail in snapshot-backed execution rather than fall back to Kodi.

Snapshot coverage:

| Consumer | Inputs |
| --- | --- |
| Constructor / destination | remote_selection, remote_path, remote_path_2, zip_temp_path; operation profile identity |
| Selection | backup_selection_type, all eight simple selectors, backup_skin_config |
| Archive / finalization | compress_backups, backup_suffix, backup_rotation |
| Planner | exclude_tmdbh_image_cache, verbose_logging; copied advanced rules if enabled |
| Progress | progress_mode and the explicit scheduled/background override |
| Dropbox construction | dropbox_key/secret passed to existing client creation; credential/token state requires a separate private input audit |
| Scheduled operation | enabled/due decision, schedule_interval, schedule_miss, progress_mode, cron_shutdown; schedule expression/time/day as one scheduling configuration |

Also resolve TMDb Helper image_location once for exclusion planning, with
existing path-validation rules. AF3's own live skin snapshot and stability
checks remain independent; freeze the choice to include AF3, not its live
configuration through the Backup Pro settings object.

Keep exact destination strings private and preserve existing translation and
normalization. Do not turn absent paths into defaults, rewrite strings, or
change SMB behavior. Paths can embed credentials. Snapshot repr/serialization
and error logs must not reveal them. Secrets remain process-private for the
operation lifetime; never place them in window properties, files, manifests,
or diagnostics. Do not claim Python can reliably zeroize immutable strings.

The service publishes only a coherent session record: PID, add-on version,
readiness, baseline identity/generation and sticky unsafe reason. Program and
service share that validation contract, not raw secrets or a Python wrapper
across interpreters. Legitimate completed settings edits establish a new
baseline generation for subsequent operations; existing snapshots do not
change. A new PID starts a new session; same-PID live update remains unsafe.
An operation must not clear unsafe state or reinitialize the session mid-run.

Recommended conservative cancellation: after admission, consume snapshot
values exclusively and check shared unsafe/profile/version state at bounded
work cancellation points. Abort and use existing staging/artifact cleanup
when revoked; do not rebuild the plan using new defaults. Before remote
transfer or rotation, require the operation authorization still valid. Those
checks cancel work; they never obtain replacement settings. This preserves
fail-closed behavior without using cancellation timing to guarantee the
selected sets. Freezing inputs and permission to continue are separate.

Scheduled completion/shutdown must use the admitted scheduling inputs and
success result. In particular, current scheduler code dynamically rereads
schedule_interval and ignores backup's False result before its one-off write.
That needs explicit treatment; snapshotting selectors alone is incomplete.
Restore/status/authorization paths must retain their existing behavior until
their own input lifetimes are audited; do not accidentally install a
backup-only snapshot in the shared object used before menu selection.

## Admission protocol: unresolved safety requirement

High confidence: immutable operation inputs eliminate later settings reads
changing the plan. Not yet high confidence: admitting a complete trustworthy
snapshot using the current baseline. Its destination/key/secret fields are
presence flags only. Two distinct nonempty destinations have the same
baseline representation. Simply hashing a candidate's current signature
cannot prove its exact paths/credentials match the earlier trusted view.
The settings signature and diagnostic state are also read separately today.

Proposed acquisition work: capture all typed fields in a short pass from one
wrapper, repeat bounded capture to reject inconsistent reads, derive the
non-sensitive signature FROM the captured values, and validate unchanged
session generation/readiness before and after. No retry-until-accepted loop.
Double reads reject observed instability; they are not an atomic Kodi read
or proof of UI/persisted equality when the source is consistently wrong.

Before implementation, settle exact-value validation for private inputs
without exposing them (for example a session-private keyed equality digest
created at the trusted baseline boundary), including key ownership across
interpreters and legitimate-edit baseline updates. A plain publicly exposed
hash of a password/path is not an acceptable substitute. Also prove baseline
generation changes cannot erase an already observed unsafe transition.
Requiring the service to start earlier alone solves neither input coherence
nor these admission issues. An extra later guard is not the proposed fix.

## Implementation order and acceptance

1. Define and test capture/admission and private-input consistency first,
   including mutation during capture, generation change, edit active,
   initializing, unsafe, live update and new PID.
2. Inject admitted inputs before destination construction in manual/scheduled
   backup; route backup, planner, progress and Dropbox consumers explicitly.
3. Materialize selectors before traversal. Tests flip the backing Kodi view
   during AF3 AND inside the addons walk, forbid later dynamic decision reads,
   and prove database/thumbnails stay excluded or work aborts cleanly.
4. Test next-operation legitimate edits, cancellation/cleanup and scheduler
   completion effects; preserve restore/non-tvOS behavior and no-secret logs.
5. Focused suite, full suite, compilation, diff review, implementation commit,
   then Example Room validation only after release authorization.

No implementation made in this review: the user conditioned it on high
confidence in the complete safety design. The architectural direction is
clear, but the admission/private-input protocol is not yet proven. This is
an engineering dependency, not a request for permission to add another gate.
