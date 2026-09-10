# Autonomous Kodi-on-Mac Validation

This is the boundary for safe local validation of Backup Pro. It records what
is established by the repository and what is not recoverable as a previously
proven command. It must not be read as authorization to run Phase 9.

## Confirmed environment

- Kodi application: `/Applications/Kodi.app`
- Approved platform: this Mac only, using a disposable Kodi profile.
- Backup Pro source checkout: `/Users/example/Documents/Kodi/script.backup.pro`
  (the Codex worker checkout may differ; use the active Git worktree recorded
  by the agent state files).
- Project tests are ordinary Python unit tests under `tests/`; run them from
  the active Backup Pro checkout with `python3 -m unittest discover -s tests -v`.

The repository evidence does **not** contain a recorded disposable-profile
directory, a previously successful Kodi launch command/flags, JSON-RPC driver,
Kodi automation script, or verified Kodi log path for this project. Agents must
not invent those details. Before Phase 9, the human must establish and record a
profile path and launch procedure, verify that the profile is disposable, and
confirm the command against this Mac.

## Safe validation procedure (planned, not yet completed)

The Phase 9 validation scenario:

1. Run compile/package checks and the Python test suite.
2. Create a disposable profile and verify its identity/path before Kodi starts.
3. Launch Kodi against only that profile using the documented, human-verified
   command. Do not use the normal profile.
4. Install/update the development add-on by an explicitly documented method
   (an audited ZIP or a controlled filesystem copy); the exact method is not
   yet evidenced in this repository.
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

The planned checks can use Kodi's own UI and, if later established and tested,
Kodi built-ins/JSON-RPC, filesystem inspection, archive inspection, process
status, and Kodi/add-on logs. No current project helper proves an autonomous
Kodi driver. `resources/lib/skin_kodi_host.py` and the related coordinator and
recovery modules are add-on runtime code, not host-side launch tooling.

## Evidence and automation boundary

Agents can safely automate once the profile and launch contract are recorded:
Python/unit/package checks; ZIP and manifest inspection; profile-local file
hashes; archive creation; controlled profile-local setup; process/log/status
inspection; restart commands; and objective before/after state comparisons.

Human judgment remains required for the first profile identity check, any
ambiguous path or permission prompt, visual skin/layout appearance, whether a
skin confirmation prompt is correct, unavailable credentials, and any failure
that threatens a non-disposable profile. Phase 9 cannot be marked complete from
unit tests alone.

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

## Missing evidence to resolve before Phase 9

Record the exact disposable profile path, launch command/flags/environment,
installation/update mechanism, Kodi and add-on log paths, restart procedure,
and reset procedure after a supervised dry run. Until then, these details are
human-owned setup decisions, not reusable agent commands.
