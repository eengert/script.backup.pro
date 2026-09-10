# Autonomous Kodi-on-Mac Validation

This is the boundary for safe local validation of Backup Pro. It records what
is established by the repository and what is not recoverable as a previously
proven command. It must not be read as authorization to run Phase 9.

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
- **Not yet proven**: installing Backup Pro into the disposable profile and
  running it (`./tools/kodi-test install` has unit-test coverage but has not
  yet been exercised against a live disposable Kodi launch), and none of the
  Phase 9 Backup Pro backup/restore/recovery scenario steps below.

## Safe validation procedure (planned, not yet completed)

The Phase 9 validation scenario:

1. Run compile/package checks and the Python test suite.
2. Create a disposable profile and verify its identity/path before Kodi starts.
3. Launch Kodi against only that profile using the documented, human-verified
   command. Do not use the normal profile.
4. Install/update the development add-on with the allowlisted local copy above.
5. Capture known AF3 settings/appearance/helper state.
6. Create a Backup Pro archive and inspect its manifest, sizes, hashes and
   exclusions.
7. Change only the disposable profile, restore the archive, accept any skin
   confirmation prompt, and verify live values, source hashes, rebuilt data and
   persisted state.
8. Restart Kodi and repeat the verification.
9. Exercise pending-recovery/rollback and compare installed/package files.
10. Preserve machine-verifiable results, then reset or discard only the
    disposable profile.

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

Human judgment remains required for any ambiguous path or permission prompt,
visual skin/layout appearance, whether a skin confirmation prompt is correct,
unavailable credentials, and any failure that threatens a non-disposable
profile. Phase 9 cannot be marked complete from unit tests alone — the harness
launch/stop proof above is harness validation, not a Phase 9 result, and Phase
9 is not marked complete or attempted by this work.

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

**A real Backup Pro bug surfaced once dependencies were resolved, not
yet fixed**: with the full dependency graph installed, a triggered
`mode=backup` run got all the way to
`XbmcBackup._createValidationFile()` — proving the trigger mechanism
works end-to-end, past every import, not just that Kodi's API accepted
the call — before failing with `Unable to create Backup Pro manifest:
'bytearray' object has no attribute 'encode'`. Root cause identified by
reading the code: `resources/lib/archive.py::sha256_reader()` assumes
`read_chunk()` returns `bytes` or `str` and calls `.encode('utf-8')` on
anything else, but this Kodi 21.1 build's `xbmcvfs.File.read()` returns
`bytearray`, which has no `.encode()` method. This is production code,
not the harness — not fixed here; it needs its own scoped, tested fix.
Phase 9a step 5 (create and inspect an actual backup's manifest/
hashes/exclusions) and later steps remain blocked on this specific,
now-precisely-understood bug — not on the trigger mechanism, which is
fully proven, and not on live-validation policy.

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
