# Backup Pro

Backup Pro is an independent Kodi backup add-on based on Rob Weber's
[Backup](https://github.com/robweber/xbmcbackup) 1.7.3. It keeps the original
on-demand and scheduled backup foundation while adding focused controls for
regenerable caches and reliable skin-configuration restore.

The add-on ID is `script.backup.pro`, so it can coexist with the original
`script.xbmcbackup`. Do not enable scheduled backups in both add-ons against
the same destination unless duplicate runs are intentional.

## Project status

Version 0.9.7 adds opt-in Arctic Fuse 3 snapshots to the measured planning,
cache-exclusion and verified-archive foundation. It captures Kodi's live typed
skin settings and only the proven Script Skin Variables source JSON needed for
rebuilding AF3. Managed source paths are omitted from the generic Add-on Data
group so stale settings and generated helper output cannot override the
authoritative snapshot. A pure restore core now preserves an exact rollback,
uses durable phase journals, automatically rolls back ordinary write failures,
and detects interrupted work for recovery after a crash. Restore preflight now
verifies every AF3 payload byte, produces source/version/count preview data and
creates a strictly validated pending-operation record with canonical bounded
serialization, enforced one-way recovery phases and atomic local persistence.
AF3 theme, colors, font and zoom choices are captured with the same stable
snapshot. The exact confined rollback transaction path and journal identity are
handed to durable pending state before profile mutation starts. A dependency-
injected coordinator now stages restores in crash-safe order through playback
stop, target-skin deactivation, transactional replacement, verified Kodi VFS
settings staging and a durable pre-rebuild checkpoint. Runtime dispatch, AF3
activation/rebuild and final verification remain to be connected. Its tested
Kodi boundary verifies installed dependencies, stopped playback, safe-skin
activation and read-back of settings staged through Kodi VFS. The completion
coordinator preserves pending state through activation, settings, appearance,
rebuild or helper-source failures and clears it only after every verification
passes. The Kodi boundary now implements target activation, staged-document
and live-setting equality, and set/read-back verification for every captured
appearance value. AF3 rebuilding now uses Skin Variables' public routes with a
verified action plan and unique completion token, validates generated include
XML before and after a synchronous skin reload, and refreshes restored node
caches. The restore selector now routes AF3 configuration by itself through the
verified preflight, staging, interactive skin-confirmation and completion path;
every restore closes its VFS/progress resources. Exact rollback completion now
restores prior files and appearance, rebuilds AF3 and verifies the independent
undo target before clearing state. Recovery-focused menus, final UX integration
and artwork remain planned. See
[`docs/DESIGN.md`](docs/DESIGN.md) for scope and acceptance criteria.

Pending restore schema v3 also binds the exact pre-restore settings, helper
hashes and appearance captured from the linked rollback transaction. The
rollback coordinator restores exact previous bytes through Kodi VFS,
reactivates AF3, reapplies its prior appearance, rebuilds generated data and
verifies the independent target before clearing recovery state. Absent or
malformed prior settings are restored byte-for-byte but are explicitly
reported as not live-verifiable.

## Attribution

Backup Pro is derived from `robweber/xbmcbackup` under the MIT License. The
original copyright and license are retained in [`LICENSE.txt`](LICENSE.txt).
Original icon components were credited to Open Iconic by the upstream project;
Backup Pro will use independently generated artwork before release.

## Device support

Development and restore testing target Kodi on macOS. No Backup Pro build may
write to an Apple TV during this project. Until physical-device testing is
separately authorized and completed, Apple TV and tvOS support must be labeled
unvalidated.
