# Backup Pro

Backup Pro is an independent Kodi backup add-on based on Rob Weber's
[Backup](https://github.com/robweber/xbmcbackup) 1.7.3. It keeps the original
on-demand and scheduled backup foundation while adding focused controls for
regenerable caches and reliable skin-configuration restore.

The add-on ID is `script.backup.pro`, so it can coexist with the original
`script.xbmcbackup`. Do not enable scheduled backups in both add-ons against
the same destination unless duplicate runs are intentional.

## Project status

Version 0.3.0 adds measured backup planning, opt-in TMDb Helper generated-image
exclusions, a checksummed Backup Pro manifest and bounded ZIP preflight.
Payload read-back verification, Arctic Fuse 3 restore integration and final
artwork remain planned. See
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
