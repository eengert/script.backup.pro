# Backup Pro

Backup Pro is an independent Kodi backup add-on based on Rob Weber's
[Backup](https://github.com/robweber/xbmcbackup) 1.7.3. It keeps the original
on-demand and scheduled backup foundation while adding focused controls for
regenerable caches and reliable skin-configuration restore.

The add-on ID is `script.backup.pro`, so it can coexist with the original
`script.xbmcbackup`. Do not enable scheduled backups in both add-ons against
the same destination unless duplicate runs are intentional.

## Project status

Version 0.5.0 adds a validated Arctic Fuse 3 capture adapter to the measured
planning, cache-exclusion and verified-archive foundation. It captures Kodi's
live typed skin settings and only the proven Script Skin Variables source JSON
needed for rebuilding AF3, excluding stale disk settings and generated build
fingerprints. Transactional restore, runtime UI integration and final artwork
remain planned. See
[`docs/DESIGN.md`](docs/DESIGN.md) for scope and acceptance criteria.

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
