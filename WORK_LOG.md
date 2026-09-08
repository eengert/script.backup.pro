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
