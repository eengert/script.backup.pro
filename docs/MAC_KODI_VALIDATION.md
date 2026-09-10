# Autonomous Kodi-on-Mac Validation

This is the boundary for safe local validation of Backup Pro. It records what
is established by the repository and what is not recoverable as a previously
proven command. This document defines the safe boundary for agent-executable
Phase 9a validation and does not authorize Phase 9b human-only validation or
testing on any non-disposable environment or device.

## Confirmed environment

- Kodi application: `/Applications/Kodi.app` (Kodi 21.1; executable
  `/Applications/Kodi.app/Contents/MacOS/Kodi`)
- Approved platform: this Mac only, using a disposable Kodi profile.
- Backup Pro source checkout: any local clone of this repository.
- Project tests are ordinary Python unit tests under `tests/`; run them from
  the active Backup Pro checkout with `python3 -m unittest discover -s tests -v`.

## Established harness

From the Backup Pro checkout:

```sh
./tools/kodi-test verify
./tools/kodi-test init
./tools/kodi-test launch
./tools/kodi-test status
./tools/kodi-test stop
```

Kodi 21.1's command-line help exposes `--portable`, but that resolves an
application-install-relative directory, not an arbitrary profile path; there
is no flag to point Kodi at an arbitrary profile directory. The harness
instead launches Kodi with `HOME` overridden to a project-local, disposable
directory (`<checkout>/.kodi-test/home`) and relies on Kodi's own path
resolution from `HOME`.

**Actual path layout, confirmed against a real isolated launch on this Mac
(2026-09-10, both from the Codex worktree and independently reproduced from
the Claude worktree)** — Kodi 21.1 on macOS does **not** use a `.kodi/`
layout for its profile or logs; it resolves macOS-style paths beneath
`HOME`:

| Kodi special path       | Resolves to (relative to disposable `HOME`)     |
|--------------------------|--------------------------------------------------|
| `special://home/`        | `Library/Application Support/Kodi` (the appdata root, containing `addons/`, `userdata/`, `system/`, `media/`) |
| `special://masterprofile/` / `special://profile/` | `Library/Application Support/Kodi/userdata` |
| `special://logpath/`     | `Library/Logs` (the actual log file is `Library/Logs/kodi.log`) |
| `special://temp/`        | `.kodi/temp` (scratch/cache only — not the profile and not the log) |

`tools/kodi_test.py` was corrected to this observed layout: `verify`/`status`
report `appdata`, `userdata`, `addons`, and `log` paths matching the table
above, and `install` places the add-on under
`<appdata>/addons/script.backup.pro/` (an earlier draft of this harness
assumed a `.kodi/`-based layout for all four of those paths, including the
add-on install target; that assumption was disproved by the real launch's
log and corrected before this harness was checkpointed).

`verify` refuses a missing/non-executable Kodi binary, refuses if any
disposable path would fall outside the disposable root, and separately
refuses if the disposable appdata directory or log file would overlap the
*real* normal Kodi profile (`~/Library/Application Support/Kodi`) or its log
(`~/Library/Logs/kodi.log`) — both checks are exercised by
`tests/test_kodi_test.py`. `init`/`reset` refuse a running instance and
delete only that exact project-local `.kodi-test/` root after resolving and
validating the path; they never touch anything outside it.

Install the development add-on without publishing or packaging the worktree:

```sh
./tools/kodi-test install <path-to-worktree>
```

The allowlist copies only `addon.xml`, `default.py`, `service.py`, artwork and
`resources/` into `addons/script.backup.pro/`; it rejects symlinks and excludes
tests, bytecode, docs, Git and agent files. Source paths outside the project
are refused.

Pre-seed a disposable add-on's per-profile settings before launch — a
scripted, reversible edit confined to the disposable profile, not a live
UI action:

```sh
./tools/kodi-test configure script.backup.pro remote_path=<local-dest> remote_selection=0
```

Only the given settings are written; anything else resolves to that
add-on's own declared default, exactly like a real, untouched install.

## What has actually been proven so far

- `./tools/kodi-test verify` succeeds on this Mac from both the Codex and
  Claude worktrees (each worktree gets its own independent `.kodi-test/`
  disposable root, since `PROJECT` is resolved from the script's own
  location).
- A real, unmodified `/Applications/Kodi.app/Contents/MacOS/Kodi` launched
  under `./tools/kodi-test launch` with `HOME` overridden this way: starts,
  logs its actual resolved `special://home/`, `special://logpath/`, and
  `special://profile/` paths (all confirmed under the disposable `HOME`,
  never under the real `~/Library/...`), initializes its GUI/renderer, and
  was stopped cleanly with `./tools/kodi-test stop` (`SIGTERM`, `running:
  false` afterward).
- The real, normal Kodi profile (`~/Library/Application Support/Kodi`) and
  its log (`~/Library/Logs/kodi.log`) were confirmed untouched by this
  activity: the real profile's `guisettings.xml` and log both show the
  user's own independent, unrelated Kodi session stopping *before* the
  disposable-launch test began, with no further writes afterward.
- This confirms the isolation mechanism (HOME override + disposable root)
  actually works end-to-end on this Mac, not just in unit tests.
- Since this was written, installing and running Backup Pro, capturing AF3
  state, creating a real backup, accepting a restore's confirmation prompts
  deterministically (Kodi's own JSON-RPC `Input.*` primitives, no human
  input needed), completing the AF3 rebuild, and restart persistence have
  all been proven live — see "Phase 9 validation procedure and current
  status" below. Phase 9a (all 11 steps) is now complete; only Phase 9b's
  visual/appearance confirmation remains.

## Phase 9 validation procedure and current status

The Phase 9 validation scenario:

1. **Complete/proven.** Run compile/package checks and the Python test suite.
2. **Complete/proven.** Create a disposable profile and verify its
   identity/path before Kodi starts.
3. **Complete/proven.** Launch Kodi against only that profile using the
   documented, human-verified command. Do not use the normal profile.
4. **Complete/proven.** Install/update the development add-on with the
   allowlisted local copy above.
5. **Complete/proven.** Capture known AF3 settings/appearance/helper state.
6. **Complete/proven.** Create a Backup Pro archive and inspect its manifest,
   sizes, hashes and exclusions.
7. **Complete/proven, corrected (2026-09-10).** Changing the disposable
   profile, triggering a restore, and **accepting both of its
   confirmation dialogs** are all proven — this step was previously
   marked "done up to the prompt" with the prompt itself wrongly
   classified as Phase 9b human-only; re-evaluated and found incorrect
   (see "Evidence and automation boundary" below for the exact
   mechanism). A scripted `Settings.SetSettingValue` changed
   `lookandfeel.skincolors`; a triggered `mode=restore` reached the
   expected `"Restore Arctic Fuse 3 configuration"` dialog
   (`Window.IsActive(yesnodialog)`, `Control.GetLabel(1)`); `Input.Up` +
   `Input.Select` accepted it, producing `pending-skin-restore.json`
   with `"phase":"prepared"`; the follow-up `"AF3 files are staged
   safely..."` OK dialog was accepted the same way, advancing to
   `"phase":"rebuild"` and invoking `finish_skin_restore()`.
8. **Complete/proven — root-caused and fixed (2026-09-10).**
   `finish_skin_restore()`'s `rebuild_skin()` call hung past its own 60s
   timeout, wedging the GUI/info-manager thread (an unrelated JSON-RPC
   info check also timed out during the hang). Root cause: Backup Pro
   keeps a modal `xbmcgui.DialogProgress` open for the whole restore, and
   `rebuild_skin()` needs `ActivateWindow` (via `script.skinvariables`'s
   `RunScript`) to succeed, which Kodi refuses while a modal dialog is
   showing. **Fix (commit `31450ab`)**: close the progress dialog right
   before calling `finish_skin_restore()`/`rollback_skin_restore()`; 2
   regression tests added, 275 tests pass. Live-reproven after the fix: a
   full backup→config-change→restore cycle completed with no wedge,
   `lookandfeel.skincolors` reverted to the backed-up value, and that
   value persisted across a full `tools/kodi-test restart`.
9. **Complete/proven (2026-09-10).** The pending-recovery dialog
   (`resolvePendingSkinRestore()`) is reachable non-interactively
   (invoke the add-on with no `mode`) and was accepted the same way
   (`Input.Up` + `Input.Select`) to discard a stale pending restore,
   confirmed via the pending-state file disappearing and the GUI staying
   responsive. `rollback_skin_restore()`'s specific choice wasn't
   separately live-exercised (shares the same now-fixed `rebuild_skin()`
   call already proven via the `finish` path).
10. **Cleanup/reset, as applicable.** Preserve machine-verifiable results,
    then reset or discard only the disposable profile. Done.

The planned checks can use Kodi's own UI, Kodi built-ins/JSON-RPC (now
established and tested — see "Evidence and automation boundary" below),
filesystem inspection, archive inspection, process status, and
Kodi/add-on logs. `resources/lib/skin_kodi_host.py` and the related
coordinator and recovery modules are add-on runtime code, not host-side
launch tooling.

## Evidence and automation boundary

Agents can safely automate once the profile and launch contract are recorded:
Python/unit/package checks; ZIP and manifest inspection; profile-local file
hashes; archive creation; controlled profile-local setup; process/log/status
inspection; restart commands; and objective before/after state comparisons.

Human judgment remains required for any *ambiguous* path or permission prompt
(one whose correct answer isn't already defined by the test scenario),
visual skin/layout appearance, unavailable credentials, and any failure that
threatens a non-disposable profile. Phase 9 cannot be marked complete from
unit tests alone — the harness launch/stop proof above is harness validation,
not a Phase 9 result, and Phase 9 is not marked complete by this work.

**Confirmation-dialog acceptance — proven automatable (2026-09-10)**: a
dialog whose intended answer is already fixed by the test scenario (e.g.
"Restore Arctic Fuse 3 configuration?" during a restore the scenario
itself triggered) is not automatically a human-only judgment call just
because it requires confirmation: restore, rollback, and pending-recovery
behavior can be driven by the harness itself. This was previously
under-investigated (a capability claim — "no proven mechanism yet" — not a
policy requirement) and has now been proven live using only existing,
official Kodi JSON-RPC primitives, no custom GUI-automation code:

```sh
./tools/kodi-test jsonrpc XBMC.GetInfoBooleans '{"booleans":["Control.HasFocus(9010)","Control.HasFocus(9011)"]}'
./tools/kodi-test jsonrpc Input.Up '{}'      # move focus from the default (No) to Yes
./tools/kodi-test jsonrpc Input.Select '{}'  # activate the focused button
```

`JSONRPC.Introspect` (unfiltered) lists the full `Input.*` namespace
(`Back`, `ButtonEvent`, `ContextMenu`, `Down`, `ExecuteAction`, `Home`,
`Info`, `Left`, `Right`, `Select`, `SendText`, `ShowCodec`, `ShowOSD`,
`ShowPlayerProcessInfo`, `Up`) — these are Kodi's own remote-input API, the
same commands a physical remote/keyboard would send. For AF3's skinned
`DialogConfirm`, the three buttons are real controls `9010` (No,
default-focused), `9011` (Yes), `9012` (extra) — found by reading the
skin's own `Dialog_DialogConfirm.xml` (`onclick>SendClick(11)` etc. map to
the underlying `10`/`11`/`12` ids the skin's grouplist wraps as
`9010`/`9011`/`9012`) and confirmed live via
`Control.HasFocus(<id>)`. Because the grouplist is vertical
(`onleft` exits the list entirely to a different control), `Input.Up`/
`Input.Down` move focus between buttons, not `Input.Left`/`Input.Right`.
Proven twice in one live restore: accepting the initial yes/no dialog
produced `pending-skin-restore.json` (`"phase":"prepared"`, matching
`stage_skin_restore()`); accepting the follow-up OK dialog (`Input.Select`
alone — a single-button dialog needs no navigation) advanced it to
`"phase":"rebuild"` and invoked `finish_skin_restore()`. No new harness
code was added or needed for this — the existing generic `jsonrpc` CLI
subcommand was sufficient.

**A separate blocker was found past both dialogs, then root-caused and
fixed (2026-09-10)**: `finish_skin_restore()`'s `rebuild_skin()` step —
which runs `RunScript(script.skinvariables,...)` and polls a Home-window
completion property — hung past its own 60-second internal timeout, and a
plain, unrelated `XBMC.GetInfoBooleans System.AddonIsEnabled(...)` check
issued independently over JSON-RPC also timed out during the hang —
evidence the whole GUI/info-manager thread was wedged. Root cause: Backup
Pro keeps a modal `xbmcgui.DialogProgress` open for the whole restore
(`self.progressBar`, default `progress_mode`), and Kodi refuses
`ActivateWindow` while a modal dialog is showing — so `rebuild_skin()`'s
window activation, and therefore its completion property, never
succeeded. **Fix (commit `31450ab`)**: close the progress dialog
immediately before calling `finish_skin_restore()`/`rollback_skin_restore()`
in both call sites (`_restoreSkinConfig()`, `resolvePendingSkinRestore()`);
progress callbacks during rebuild are already exception-safe
(`skin_coordinator._progress()` swallows exceptions). 2 regression tests
assert the dialog closes before the host's rebuild-only `activate_skin()`
call; 275 tests pass. Live-reproven: a full backup→config-change→restore
cycle completed with the GUI staying responsive throughout (no wedge),
`lookandfeel.skincolors` reverted correctly, and that value persisted
across a full Kodi restart.

**Non-interactive script triggering — proven via JSON-RPC (2026-09-10)**:
Phase 9a steps 5+ need a way to trigger a Backup Pro action (e.g.
"create a backup") without a human clicking the main menu.
`special://profile/autoexec.py`, the legacy XBMC/Kodi startup-script
hook, was tried first as a local-only, no-network alternative and does
**not** exist in this installed Kodi 21.1 macOS build: a
marker-file-writing `autoexec.py` never executed across several real,
fully-booted disposable launches (confirmed by polling the log and the
marker file directly, not assumed), and the string "autoexec" does not
appear anywhere in the Kodi binary or its bundled system resources. Do
not reintroduce an autoexec.py-based trigger for this Kodi build.

Kodi's JSON-RPC is proven instead, against a real launch on this Mac:

```sh
./tools/kodi-test init
./tools/kodi-test enable-webserver
./tools/kodi-test install <path-to-worktree>
./tools/kodi-test install-dependencies                       # addon.xml's declared deps + their own transitive deps
./tools/kodi-test configure script.backup.pro remote_path=<local-dest> remote_selection=0
./tools/kodi-test launch
./tools/kodi-test jsonrpc JSONRPC.Ping                       # -> "pong"
./tools/kodi-test enable-addon script.backup.pro              # a fresh install() is not enabled by default
./tools/kodi-test execute-addon script.backup.pro mode=backup
./tools/kodi-test stop
```

`enable-webserver` fails closed: it only writes `guisettings.xml` into a
*fresh* disposable profile (before the first launch), refusing if one
already exists, and always requires an authentication password (a
fixed test-only account, loopback-only intent — never bind this to a
non-disposable profile). The webserver coming up was confirmed via
`CWebserver[<port>]: Started` in the disposable instance's own log;
`JSONRPC.Ping` returned `"pong"` over HTTP Basic Auth; and
`Addons.ExecuteAddon` was confirmed to genuinely invoke the add-on's
`default.py`/`service.py` inside the running Kodi process — not merely
accepted by the API — via a real Python traceback in the log naming
those exact files and line numbers.

**A dependency gap surfaced by that same proof, now solved**: the
triggered run first failed with `ModuleNotFoundError: No module named
'dropbox'` (and `dateutil`). Backup Pro's `addon.xml` declares four
dependency add-ons (`script.module.dateutil`, `script.module.future`,
`script.module.dropbox`, `script.module.pyqrcode`) that
`./tools/kodi-test install` does not provide — it only copies Backup
Pro's own files, by design (see its allowlist above). `./tools/kodi-test
install-dependencies` fixes this: it copies each declared dependency
from the real, normal Kodi profile's already-installed add-ons
(read-only from the real profile), and resolves the full transitive
closure — `script.module.dropbox` itself needs `six`, `requests`,
`certifi`, `chardet`, `idna`, and `urllib3`, none of which Backup Pro's
own `addon.xml` mentions. Proven against a real launch: all ten
dependency add-ons copied and recognized.

**A real Backup Pro bug surfaced once dependencies were resolved, fixed
(commit `22c914c`)**: with the full dependency graph installed, a
triggered `mode=backup` run got all the way to
`XbmcBackup._createValidationFile()` — proving the trigger mechanism
works end-to-end, past every import, not just that Kodi's API accepted
the call — before failing with `Unable to create Backup Pro manifest:
'bytearray' object has no attribute 'encode'`. Root cause identified by
reading the code: `resources/lib/archive.py::sha256_reader()` assumed
`read_chunk()` returns `bytes` or `str` and called `.encode('utf-8')`
on anything else, but this Kodi 21.1 build's `xbmcvfs.File.read()`
returns `bytearray`, which has no `.encode()` method. Fixed with a
regression test reproducing the exact case first (TDD). **Phase 9a
step 5 is now done**: a triggered backup completes, and its manifest
was inspected directly — correct `addon_version`/`kodi_version`,
correct directory exclusions, 11 files each with a `sha256` hash and
`size`.

## Phase 9a step 4 (AF3 state capture) — done (2026-09-10)

Backup Pro's AF3 capture is gated on `xbmc.getSkinDir() == AF3_ID`
(`resources/lib/backup.py`), so AF3 must actually be the *active* skin
in the disposable profile, not merely present. `install_skin()`
(`./tools/kodi-test install-skin`) copies a skin add-on and its own
transitive dependencies from the real profile, the same pattern
`install_dependencies()` uses, and `configure_webserver(extra_settings=
{"lookandfeel.skin": AF3_ID})` (`enable-webserver --skin <id>`) sets it
active before the first launch.

Getting there took three rounds of live-testing and fixing real gaps,
not one lucky attempt:

1. `install-skin` first refused `xbmc.gui` — AF3's own `addon.xml`
   declares that virtual platform dependency directly (Backup Pro's
   `addon.xml` only ever declared `xbmc.python`), so
   `_declared_dependencies()`'s exclusion was generalized to the whole
   `xbmc.*` namespace.
2. Retried: `install-skin` then refused `script.module.pil`, required
   transitively by two of AF3's own dependencies
   (`plugin.video.themoviedb.helper`, `script.texturemaker`). Direct
   inspection first suggested it was genuinely absent from the real
   profile (`ls ~/Library/Application Support/Kodi/addons/script.module.pil`
   found nothing there) — **that check was incomplete, not the actual
   answer.** Once network-install was authorized (see below) and
   investigated properly before attempting anything, `Addons.GetAddonDetails`
   over JSON-RPC revealed the real picture: `script.module.pil` is
   installed — bundled inside `Kodi.app` itself
   (`/Applications/Kodi.app/Contents/Resources/Kodi/addons/script.module.pil/`),
   visible to every profile automatically, the same way
   `repository.xbmc.org` is. `_copy_addon_closure()` only ever checked
   the real *profile's* `addons/` directory, never Kodi's own bundled
   system add-ons, so it reported a false "missing" for anything
   satisfied that way. Fixed: check `KODI_SYSTEM_ADDONS_DIR` first and
   skip copying anything already there. **No network install was
   actually needed** for this specific package.
3. With that fixed, `install-skin` succeeded (17 add-ons total,
   `script.module.pil` correctly skipped), but AF3 *still* failed to
   load (`Failed to load skin 'skin.arctic.fuse.3'`, fallback to
   Estuary). `Addons.GetAddonDetails` on AF3 itself and each of its
   copied dependencies showed `"enabled": false` — every freshly copied
   add-on starts disabled, the same problem already known from Backup
   Pro, but this time affecting an entire skin's dependency chain at
   once, and Kodi's boot-time skin loader refuses to activate a skin
   with *any* disabled hard dependency. `enable_addons()` (batches
   `enable_addon()`) fixed this — but a live `Settings.SetSettingValue`
   skin switch afterward did *not* trigger a real reload; only
   `restart()` (stop + launch) did.

**Fully proven end to end** (`./tools/kodi-test init` → `install-skin`
→ `install .` → `install-dependencies` → `enable-webserver --skin
skin.arctic.fuse.3` → `launch` → `enable-addon script.backup.pro` +
`enable-addons <all 17 AF3-closure ids>` → `restart`): the log showed
`load skin from: .../addons/skin.arctic.fuse.3/ (version: 3.2.19)`
with no failure, and `Settings.GetSettingValue lookandfeel.skin`
confirmed `skin.arctic.fuse.3` active. With `backup_skin_config=true`
configured, a triggered backup produced a real `skin_config/` capture
in the archive — inspected directly: `skin_id` `skin.arctic.fuse.3`,
`skin_version` `3.2.19`, `helper_id` `script.skinvariables`,
`helper_version` `2.2.2`, 184 real appearance/setting values, 4 helper
files, a content fingerprint. This is Phase 9a step 4, objectively
complete. Real Kodi profile confirmed untouched throughout (unchanged
file mtimes across every launch in this investigation); stopped and
reset the disposable profile cleanly.

One harmless side effect observed and worth recording: once AF3 is
genuinely active, its home-screen widgets (via
`plugin.video.themoviedb.helper`) attempt outbound HTTP calls to
third-party content APIs (mdblist.com, trakt.tv) on their own,
independent of anything this harness does, and fail with `401` since
no accounts are configured in the disposable profile — expected, not
an error in this work, and unrelated to Backup Pro's AF3 capture
(which reads local skin settings/files, not widget content).

### Authorized network-install capability (recorded, not exercised)

The human explicitly authorized a narrowly scoped network-install
capability for this harness before the investigation above found it
wasn't actually needed. Recording the exact authorization here (per
explicit instruction) so either agent can apply the same rule to a
genuine future need without re-asking:

- **Scope**: the approved disposable Kodi test profile only. Never the
  normal Kodi profile. Never Apple TV or any other device.
- **Allowed source**: Kodi's own official repository/source as
  configured by the disposable Kodi instance only. No third-party
  repositories. No arbitrary ZIP/package downloads from the web. No
  external credentials.
- **Allowed package** (for this task): `script.module.pil` only, solely
  to satisfy AF3's proven transitive dependency. Not a general-purpose
  install capability — a later task must explicitly re-authorize
  installing anything else.
- **Required mechanism**: the smallest safe mechanism that requests
  installation *through Kodi itself* (its own repository/add-on
  installation path), not custom HTTP/package downloading. Must fail
  closed if the source can't be verified as the official Kodi
  repository.
- **Required verification** before trusting a result: the package is
  actually installed in the disposable profile; its source/repository
  identity as far as Kodi exposes it; that the originally-blocked
  activation (AF3) then succeeds; that the normal Kodi profile remains
  untouched; that no unrelated add-ons were installed unexpectedly;
  Kodi logs inspected for installation/activation errors.
- **Not implemented**: since `script.module.pil` turned out to already
  be present (Kodi-bundled, not network-installed), no install
  mechanism was built or exercised. If a genuine future need for a
  different package arises, build it fresh under these same
  constraints rather than assuming this authorization has already been
  spent on something else, and re-confirm the constraints still apply
  before using it.

## macOS and recovery assumptions

The operator must have permission to launch Kodi and read/write the chosen
disposable profile. Kodi may need to be fully stopped before profile reset or
replacement. On interruption, stop Kodi, collect logs and state first, and
reset only the verified disposable profile. Never use broad `rm`, `git clean`,
or profile-wide cleanup against an unknown path.

## Hard safety boundaries

- Never launch tests against the normal Kodi profile.
- Never install, configure, back up, restore, or otherwise write to an Apple TV.
- Never modify unrelated Kodi data or infer that a path is disposable.
- Never use destructive cleanup outside the verified disposable profile.
- Never claim live validation succeeded without objective evidence and the
  required human visual checks.
- Do not publish while validating. Distribution steps are documented in
  `docs/KODI_DISTRIBUTION_WORKFLOW.md` and require separate authorization.

The helper's `status` output is the machine-readable record of the active PID,
HOME, appdata, userdata, addons and log paths. It starts Kodi in a new session
and stops it with `SIGTERM`; if it does not stop, the helper fails closed for
further reset.
