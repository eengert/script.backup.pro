# Backup Pro work log

## Phase 1 - discovery

- Confirmed standard Backup 1.7.3 and upstream commit `cdd8faf`.
- Read the representative Mac configuration without exposing destination or
  cloud credentials.
- Confirmed the initial TMDb Helper generated-cache allowlist and Mac profile
  sizes.
- Confirmed `script.backup.pro` had no obvious Kodi repository or GitHub code
  collision.

## Phase 2 - foundation

- Forked `robweber/xbmcbackup` to `eengert/script.backup.pro`.
- Created branch `codex/backup-pro-foundation` in a separate checkout.
- Changed the runtime identity to `script.backup.pro`, display name to Backup
  Pro and development version to 0.1.0.
- Preserved both Program and Service extension points and the upstream MIT
  license and attribution.
- Recorded the accepted product scope, safety rules, test boundary, phases and
  release criteria in `docs/DESIGN.md`.
- Removed upstream's case-only duplicate `en_US` and `fa_AF` translation paths;
  they cannot coexist with `en_us` and `fa_af` on the case-insensitive macOS
  filesystem. The canonical lowercase paths and their content remain.
- Updated English dialog identity and internal RunScript targets.

Validation: XML parsing, Python compilation and `git diff --check` passed. No
package was installed and no Kodi profile or Apple TV was changed.

## Phase 3 - planning engine

- Added a Kodi-independent file planner with normalized, component-aware,
  exact-case path containment that fails safe by including unexpected names. It
  rejects parent traversal and does not
  confuse similarly named siblings such as `blur_v3` and `blur_v30`.
- Added included and excluded file/size counts, per-group totals and largest
  top-level directory reporting.
- Added the opt-in **Exclude TMDb Helper generated image cache** setting.
- Resolves TMDb Helper's configured image location and limits the adapter to
  `blur_v3`, `crop_v2`, `desaturate_v2` and `colors_v2`.
- Integrated the planner into both Simple and Advanced backup enumeration while
  preserving portable `special://` restore roots.
- Retained the old FileManager API through a thin Kodi wrapper so restore and
  compressed-backup call sites remain compatible pending their later phases.
- A read-only scan of the representative Mac profile planned 1,284 included
  files (42.40 MiB) and omitted 562 generated images (86.52 MiB). The remaining
  largest Add-on Data directory was TMDb Helper at 21.05 MiB, primarily its
  preserved database.

Validation: 11 planning and Kodi-bridge tests, Python compilation, XML parsing and Git
whitespace checks passed. The Mac profile scan was read-only. No package was
installed and no Kodi profile or Apple TV was changed.

## Phase 4A - archive contract and extraction gate

- Replaced the legacy validation marker with a versioned Backup Pro manifest.
- Records normalized relative file paths, exact byte sizes, SHA-256 hashes,
  Kodi/add-on versions, settings metadata and the measured backup plan.
- Added case-collision, duplicate, traversal, absolute-path, backslash,
  symlink, special-entry, encryption and unsupported-compression rejection.
- Added explicit file-count, per-member, aggregate-size and compression-ratio
  ceilings before extraction.
- Requires compressed archives to contain exactly one root-level Backup Pro
  manifest and rejects missing, undeclared or size-mismatched members.
- Streams ZIP writes in 1 MiB chunks instead of reading each source file fully
  into memory.

Validation: 35 pure, real-ZIP and Kodi-bridge tests, Python compilation, XML parsing and
Git whitespace checks passed. This phase did not install the add-on or write to
any Kodi profile or Apple TV. Payload read-back and restore hash verification
remain Phase 4B work.
