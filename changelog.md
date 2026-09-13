# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

## Version 0.9.27

### Added

- Added diagnostic logging for Apple TV operation-boundary settings views and
  settings-safeguard decisions. This release does not change backup behavior.

## Version 0.9.26

### Added

- Added diagnostic logging for Apple TV backup-selection and stale-settings
  investigation. Logs identify selected backup sets and non-sensitive settings
  field names only; this release does not change backup behavior.

## Version 0.9.25

### Fixed

- Fixed an Apple TV issue where ordinary Backup Pro operation could mutate a
  destination setting, causing false stale-settings restart prompts and
  potentially making deselected sets such as Kodi Databases and
  Thumbnails/Fanart appear in a backup.

## Version 0.9.24

### Fixed

- Improved Apple TV post-add-on-change settings protection: Backup Pro now
  revalidates saved settings and only requires a Kodi restart when the
  settings view is actually inconsistent.

## Version 0.9.23

### Added

- Guarded tvOS settings against use after add-on changes: Backup Pro now
  detects an unsafe session (stale/default settings possible after add-on
  install/uninstall/update on tvOS) and blocks settings-dependent actions
  instead of acting on stale values, prompting a one-time restart when
  needed.

## Version 0.9.22

### Added

- Added diagnostic logging for tvOS case-collision verification, including
  Kodi VFS and native fallback details. This release does not claim to fix the
  underlying compatibility issue; collision handling remains fail-closed.

## Version 0.9.21

### Fixed

- Improved handling of byte-identical case-only source aliases when Kodi VFS
  reports inconsistent hashes, using an independent local-file comparison only
  as a safe fallback.
- Genuinely different or unverifiable collisions still fail closed.

## Version 0.9.20

### Fixed

- Compressed restores now stage archives through Kodi's VFS in bounded
  chunks, allowing the progress meter and size-remaining text to advance
  from bytes actually copied. Sources without safe incremental progress use
  explicit unknown-progress messaging instead of a misleading meter.
- The refreshed Backup Pro artwork now uses a new icon path, ensuring Kodi
  treats it as a new texture cache key after an add-on upgrade.

## Version 0.9.19

### Changed

- Refreshed the Backup Pro thumbnail and icon artwork.

## Version 0.9.18

### Changed

- Compressed restores now clearly distinguish preparation, copying,
  archive verification, and extraction before actual restore writes begin.
  Non-compressed restores use preparation wording without claiming
  archive-specific work.
- The restore-set chooser now presents human-readable labels using Backup
  Pro's established Settings terminology. Internal restore-set IDs and
  compatibility with existing Backup Pro archives are unchanged.

## Version 0.9.17

### Fixed

- `remoteConfigured()` failed to detect an unconfigured/empty backup
  destination because an empty `remote_path`/`remote_path_2` setting
  is normalized (by `Vfs.clean_path()`) to `"/"`, which the guard
  mistook for a valid destination. Backup Pro no longer treats a
  normalized `/` as configured; the raw, unnormalized setting is now
  checked instead.
- Backup and Restore now stop cleanly with the existing "destination
  not configured" message before any remote mkdir/copy/restore
  operation when no destination is set, instead of attempting to
  create a path at the filesystem root.

## Version 0.9.16

### Fixed

- Backup no longer fails when two source paths differ only by case
  (e.g. on case-insensitive filesystems) and resolve to the same
  underlying file. Case-folded source-path collisions are now detected
  before manifest construction; if all colliding files are
  byte-identical, exactly one deterministic canonical entry is kept
  (the lexicographically smallest normalized archive/source path), and
  the excluded aliases are logged and counted in exclusion accounting.
- Case-only collisions that differ in content still fail closed, with
  every conflicting path reported, exactly as before.
- Final manifest/archive validation is unchanged and still rejects
  ambiguous path sets.

## Version 0.9.15

### Fixed

- Backup Pro no longer fails to open immediately after a fresh installation
  when Kodi has not yet created the add-on's profile directory. Read-only
  pending-restore inspection treats that normal first-run condition as no
  pending restore; write-side directory validation and symlink protections
  are unchanged.

## Version 0.9.14

### Fixed

- The compressed-backup progress dialog showed a frozen "X remaining"
  byte countdown during the final copy of the finished archive to the
  backup destination, since that step copies one large file with no
  incremental progress available. It now shows a clear, honest message
  ("Compressing backup into ZIP archive...") instead of a misleading,
  non-advancing figure.
- The progress dialog could stay open underneath the final success or
  failure dialog and reappear, stale, right after it was dismissed. The
  progress dialog is now closed before either result dialog is shown.

## Version 0.9.13

### Fixed

- The "Set Zip File Location" setting could still appear under Compress
  Archives after 0.9.12's attempted fix. The internal zip staging path is
  now genuinely never exposed as a user setting, on real Kodi, not just
  according to the settings file - compressed backups continue to work
  exactly as before.

## Version 0.9.12

### Fixed

- Enabling Compress Archives no longer exposes a separate "Set Zip File
  Location" setting. That setting was only ever an internal staging path used
  while building the archive on local disk, never a second destination -
  compressed and uncompressed backups have always been written to the same
  configured backup destination, and now the settings screen reflects that.

### Changed

- Help's Compression section now explains plainly that compression only
  changes the archive format and always uses the same configured backup
  location.

## Version 0.9.11

### Fixed

- The final restore completion dialog could be dismissed a few seconds after
  appearing, before you had a chance to read it, if Arctic Fuse 3 was still
  quietly refreshing its menus and widgets in the background right after a
  restore. Completion is now confirmed to stay stable for several seconds
  before that dialog is shown, so it should no longer disappear on its own.

## Version 0.9.10

### Fixed

- Backup failures are now tracked per file, not just the first one encountered,
  and reported in a clear, persistent dialog stating what failed and why --
  instead of a vague, easily-missed notification that could be mistaken for a
  partial success. A failed backup was already correctly discarded and never
  offered as a restore point; this release only makes that failure clearly
  reported instead of silently confusing.
- Status now correctly reflects your real backup history via a small local
  completion marker, instead of always reporting no backup history regardless
  of past successful backups.

### Changed

- Successful backups now show a persistent confirmation dialog stating the
  backup is valid and can be restored, instead of a transient notification
  that could be missed.
- Backup setting names are clearer: Add-on Settings & Data, Exclude TMDb
  Helper Cached Images, Back Up Arctic Fuse 3 Configuration, Kodi
  Configuration Files, Kodi Databases, Installed User Add-ons (setting IDs
  and behavior are unchanged).
- Help has been rewritten as organized, friendly sections covering what gets
  backed up, compression, destination and storage, restore, scheduling,
  status, and the difference between an expected skip and a real failure.

## Version 0.9.9

### Added

- Full Arctic Fuse 3 (AF3) skin configuration backup and restore: crash-safe
  staging and rollback across interruption, previous-appearance restoration,
  Skin Variables rebuild, and verification of restored settings and helper
  files.
- Help and Status main-menu entries.
- A clear backup-completion summary notification.
- Updated add-on icon artwork.

### Fixed

- Restore no longer misses Kodi's own skin-change confirmation dialog;
  Backup Pro now answers it automatically and deterministically.
- Eliminated false-positive "restore incomplete" reports caused by Skin
  Variables' own build-fingerprint settings and a timing race in helper-file
  verification after the skin rebuild.
- A crash in archive checksum computation on certain files.

### Changed

- The pre-restore confirmation now explains that Kodi will briefly switch to
  its default skin, restore the backed-up configuration, then reactivate the
  backed-up skin.
- The final restore result is now shown as a dialog you must dismiss,
  instead of a notification that could be missed.

### Removed

- The obsolete v1.5.0 upgrade-notice dialog.

**Apple TV / tvOS**: not validated on physical hardware in this release;
treat as unsupported until device testing is separately authorized and
completed.

## Version 0.9.8

### Added

- Crash-safe forward staging resume after a committed file transaction or a
  failed Kodi VFS settings write.

### Security

- Revalidates every current managed AF3 file against the pending manifest
  before resuming staging and refuses changed or invalid data before host
  effects.

## Version 0.9.7

### Added

- Crash-resumable AF3 rollback coordination from every linked forward-restore
  checkpoint after the transaction identity is durable.
- Exact prior settings staging through Kodi VFS, including valid, absent and
  malformed previous settings documents.
- Previous appearance application, Skin Variables rebuild and complete helper
  source verification before rollback state is cleared.

### Security

- Revalidates the rollback snapshot against its independently persisted target
  before any rollback-side Kodi or profile effect.
- Preserves `rollback_rebuild` state after any failed activation, settings,
  appearance, rebuild or verification step so recovery can resume.

## Version 0.9.6

### Added

- Pending-state schema v3 with an independently validated rollback target.
- Exact pre-restore settings, helper hashes and appearance are durably linked
  to the confined transaction path and UUID before profile mutation.

## Version 0.9.5

### Added

- Interactive Backup Pro runtime dispatch for verified AF3-only restores,
  including preview, skin-confirmation guidance and completion reporting.
- Local and Dropbox AF3 payload readers with preflight checksum verification.

### Fixed

- Restore VFS and progress resources now close on every exit path.
- The advanced-settings restart flow runs only when Config was selected, so it
  cannot unexpectedly modify an AF3-only restore.
- AF3 payload reads are bounded, rebuild plans use unique owned paths and a
  failed rebuild restores the previous generated profile selector.

## Version 0.9.4

### Added

- Token-confirmed AF3 rebuild using Skin Variables' supported public routes.
- Generated include validation before and after skin reload, node-cache refresh
  and verified selected-profile include generation.

## Version 0.9.3

### Added

- Kodi target-skin activation with an observed postcondition.
- Staged-document and live typed skin-setting verification.
- Per-setting appearance application and JSON-RPC read-back verification.

## Version 0.9.2

### Added

- Locked normal-restore completion coordinator for AF3 activation, settings
  verification, appearance, rebuild and helper-source verification.

### Security

- Pending state is cleared only after every completion check succeeds and is
  preserved with its exact rollback transaction after any injected failure.

## Version 0.9.1

### Added

- Verified Kodi host boundary for dependency checks, playback stop, safe-skin
  activation and AF3 settings staging through Kodi VFS.
- Semantic VFS read-back verification with restoration of the prior settings
  document when staging fails.

## Version 0.9.0

### Added

- Dependency-injected AF3 restore coordinator that orders dependency checks,
  durable intent, playback stop, target-skin deactivation, rollback handoff,
  transactional file replacement, Kodi VFS settings staging and the rebuild
  checkpoint.
- Read-only pending-state inspection that selects an exact recovery action from
  the pending phase and linked transaction status.

### Security

- Preserves pending state and rollback evidence at every tested crash boundary,
  skin rejection and VFS staging failure.
- Rejects orphaned, substituted and phase-inconsistent transactions instead of
  guessing which recovery data belongs to the restore.
- Locks concurrent restore starts, binds the immutable applied payload to the
  pending record and verifies playback stopped and AF3 became inactive before
  creating rollback data.

## Version 0.8.4

### Added

- Stable capture, manifest validation and restore preview for AF3's Kodi theme,
  colors, font and zoom appearance settings.

### Security

- Accepts only the four allowlisted appearance settings with bounded,
  setting-specific value types and rejects unstable snapshots.

## Version 0.8.3

### Added

- Durable `transaction_prepared` handoff that exposes the exact rollback
  transaction after snapshotting and before profile mutation.
- Pending schema 2 binds the rollback directory to its journal UUID.
- Profile-bound transaction status checks confined to the add-on's exact
  rollback root and transaction identity.

### Security

- Removes the crash window in which restored files could be applied before
  pending state knew which rollback transaction protected them.

## Version 0.8.2

### Added

- Atomic, synced local persistence for AF3 pending-restore state.
- Verified clear operation that refuses to remove absent, corrupt or changed
  recovery state.

### Security

- Rejects symlinked or non-file pending-state targets without touching their
  destination.

## Version 0.8.1

### Added

- Canonical, size-bounded serialization for AF3 pending-restore state.
- One-way restore phase transitions that require and then preserve the exact
  rollback transaction identity.

### Security

- Rejects malformed, oversized, backward or otherwise impossible persisted
  restore-state transitions before Kodi orchestration uses them.

## Version 0.8.0

### Added

- AF3 restore preview containing the source device/profile, component versions,
  settings/helper counts, payload size and rollback requirement.
- Verified loader for every `skin_config` payload member with cancellation.
- Strict pending-restore record containing typed live settings, helper hashes,
  manifest records and rollback phase requirements.

### Security

- Revalidates path scope, per-file hashes, sizes, aggregate fingerprint and
  live-setting count before restore mutation.
- Rejects damaged, incomplete or internally inconsistent pending state.

## Version 0.7.0

### Added

- Durable AF3 file-transaction journals and exact pre-mutation rollback
  snapshots.
- Detection and recovery of interrupted snapshot, apply and rollback phases.
- Manual rollback for completed file transactions, bound to the exact source
  profile.

### Security

- Validates settings XML and helper JSON before creating transaction state.
- Rejects symlinked profile, journal, destination and rollback-snapshot paths.
- Blocks new skin transactions while unresolved recovery state exists.
- Bounds rollback journal, file and aggregate reads before restoring data.

## Version 0.6.0

### Added

- Opt-in **Back up Arctic Fuse 3 configuration** setting.
- Staged `skin_config` archive group with validated source/profile and component
  metadata.

### Changed

- Paths owned by the AF3 adapter are measured and excluded from overlapping
  generic Add-on Data copies.
- Temporary skin snapshots are removed after successful, failed or cancelled
  backups.

### Security

- Backup Pro manifests require AF3 metadata and `skin_config` payloads to
  appear together with matching path, count and size constraints.
- Generic live-file restore of `skin_config` is blocked until the transactional
  restore handler is available.

## Version 0.5.0

### Added

- Arctic Fuse 3 adapter for Kodi's authoritative live boolean and string skin
  settings.
- Validated capture of AF3 Script Skin Variables node, login and viewtype JSON,
  including declared and safely inferred skin-user profiles.
- Restore-preview metadata for the source device/profile, component versions,
  setting and helper-file counts, size and snapshot fingerprint.

### Security

- Rejects unstable settings, invalid JSON, unsafe user identifiers, symbolic
  links and excessive helper file counts or sizes during skin capture.
- Excludes Skin Variables build fingerprints and unrelated or generated helper
  output from the managed snapshot.

## Version 0.4.0

### Added

- Exact read-back verification for published manifests and folder payloads.
- Full local ZIP validation plus byte-for-byte verification after upload.

### Changed

- Backup retention runs only after copying and read-back verification succeed.
- Cancelled or failed backup artifacts are removed so they are not presented as
  valid restore points.
- New backups refuse pre-existing targets and clean up their complete fresh
  artifact after failure.
- Dropbox uploads close source handles reliably and handle the exact chunk-size
  boundary as a normal upload.

## Version 0.3.0

### Added

- Versioned Backup Pro manifests with normalized file paths, exact byte sizes
  and SHA-256 checksums.
- ZIP preflight for duplicate paths, traversal, absolute paths, symlinks,
  unsupported entries and explicit size, count and compression-ratio limits.

### Changed

- ZIP members are written in bounded chunks instead of loading each source
  file wholly into memory.
- Backup Pro ZIP restores require a matching Backup Pro manifest and do not
  accept undeclared payload members.

## Version 0.2.0

### Added

- Backup Pro identity and coexistence with standard Backup.
- Measured backup plans with group totals and largest included directories.
- Opt-in, measured exclusions for confirmed TMDb Helper generated-image caches.

### Changed

- Replaced raw prefix exclusions with normalized component-aware path matching.
- Removed case-only duplicate translation paths that cannot coexist on macOS.

## [Version 1.7.3](https://github.com/robweber/xbmcbackup/compare/matrix-1.7.1...robweber:matrix-1.7.3)

### Fixed

- fixed issue with Scheduler running in a loop (#251). Issue stemmed from previous strptime fix affecting correct calculation of cron values

### Changed

- changed previous strptime bug fix by applying specific patch to authorizers file where this bug was initially seen

## [Version 1.7.2](https://github.com/robweber/xbmcbackup/compare/matrix-1.7.1...robweber:matrix-1.7.2)

### Fixed

- fixed a bug with the GUI settings restore, the settings key was not identified properly. Thanks @alexhass
- implemented [suggested fix](https://kodi.wiki/view/Python_Problems#datetime.strptime) for strptime Python bug

## [Version 1.7.1](https://github.com/robweber/xbmcbackup/compare/matrix-1.7.0...robweber:matrix-1.7.1)

### Added

- `utils.getSettingStripped` to trim whitespace around setting strings

### Changed

- Dropbox Secret and App Key are trimmed on loading - thanks @rjclark99
- added additional dialog information when gathering files for a Restore. This doesn't fix the speed which these happen since that is related to the platform but does provide more info that it's working.

### Fixed

- issue where Zip file restores were failing due to a missing `is_dir` attribute

## [Version 1.7.0](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.8...robweber:matrix-1.7.0)

### Added

- You can now append a suffix to the end of each backup name (folder or zip file). This is only available in the Advanced or Expert settings.
- validation file now saves a list of all installed addons and versions
- prompt to close Kodi at the end of successful restore

### Changed

- added new line between file size and file name, was unreadable on some systems due to string resizing
- modified GitHub issue template slightly
- translations sync
- token files are stored in a `.json` instead of a `.txt` file
- file discovery process now flags directories with an `is_dir` metadata property instead of prefixing with a dash (-). This was done for legacy reasons and there is no reason for it.

### Fixed

- fixed minor UI issues
- division error when transferSize = 0
- fixed Dropbox tokens expiring by using [refresh tokens](https://dropbox-sdk-python.readthedocs.io/en/latest/api/oauth.html)

## [Version 1.6.8](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.7...robweber:matrix-1.6.8)

### Changed

- use the `<source>files</source>` tag on the remote path browser to bring in saved file paths from the File Manager
- multiple language files updated through integration with [Weblate](https://kodi.weblate.cloud/projects/kodi-add-ons-scripts/script-xbmcbackup/), thanks to @gade01 for helping to get it working.

### Fixed

- default en_gb file must use empty `msgstr` value
- minor UI fixes for dialog prompts
- fixed catch22 situation where Dropbox remote tries to load prior to authorization flow
- travis CI links in README

## [Version 1.6.7](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.5...robweber:matrix-1.6.7) - 2021-04-16

### Added

- added QRcode when setting up Dropbox, uses pyqrcode

### Fixed

- fixed issue when using ```RunScript()``` within settings to launch Advanced Editor
- error on advanced settings restore prior to reboot
- minor gui dialog fixes

## [Version 1.6.6](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.5...robweber:matrix-1.6.6) - 2021-03-15

### Fixed

- error when typing the remote path, ```listBackups()``` function was not working if final slash not included in typed directory path name.
- added ```force=True``` flag to the ```rmdir()``` function. Fixes issue with directories being removed when not empty

## [Version 1.6.5](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.4...robweber:matrix-1.6.5) - 2021-03-06

### Added

- added Expert setting to change location of zip file temp location as it's being built or extracted

### Changed

- updated ```settings.xml``` to match new [Kodi settings syntax](https://kodi.wiki/view/Add-on_settings_conversion), including visibility levels

### Fixed

- when restoring from a zip file the command to delete the extracted directory was incorrect
- ```Dialog().yesno()``` no longer takes line1 arg, changed to message

## [Version 1.6.4](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.3...robweber:matrix-1.6.4) - 2020-12-23

### Added

- merged duplicate copy code into ```_copyFile``` method
- added method to backup/restore Kodi settings via the GetSettings/SetSettingValue JSON methods in the validation file
- added setting to always restore settings or prompt at the time of backup

### Changed

- updated script.module.future version to current
- swapped xbmc.translatePath for xbmcvfs.translatePath, deprecated

### Fixed

- fixed calls to ```xbmcgui.Dialog().ok()```, method definition changed to only allow one message arg with Kodi 19
- fixed import of dropbox Oauth package in authorizer flow

### Removed

- removed old xml GuiSettings parsing for settings restore

## [Version 1.6.3](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.2...robweber:matrix-1.6.3) - 2020-06-15

### Changed

  - fixed validatePath error (issue #166) thanks (thanks @AnonTester)

## [Version 1.6.2](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.1...robweber:matrix-1.6.2) - 2019-04-09

### Changed

  - changed PNG screenshots to JPG (per [#165](https://github.com/robweber/xbmcbackup/issues/165))

## [Version 1.6.1](https://github.com/robweber/xbmcbackup/compare/matrix-1.6.0...robweber:matrix-1.6.1) - 2019-12-30

### Added

  - added method to get size of a file from the VFS
  - added total transfer size information to progress bar with appropriate precision (KB, MB, etc)
  - show file size of zip files in the restore selection dialog
  - added getSettingInt and getSettingBool to utils.py class
  - added verbose logging setting and tied it to logging related to file paths added/written, this will significantly reduce the debug log size (thanks CastagnaIT)
  - localize advanced editor strings instead of hard coding English

### Changed

  - display every file transfered in progress bar, not just directory
  - base progress bar percent on transfer size, not total files
  - changed getSettings where needed to getSettingBool and getSettingInt
  - use service.py to start scheduler, moving scheduler to resources/lib/scheduler.py Kodi doesn't cache files in the root directory
  - fixed issues with rotating backups where trailing slash was missing (thanks @AnonTester)
  - read/write files using contextlib

## [Version 1.6.0](https://github.com/robweber/xbmcbackup/compare/krypton-1.5.2...robweber:matrix-1.6.0) - 2019-11-26

### Added

 - added new badges for Kodi Version, TravisCI and license information from shields.io
 - dependency on script.module.dateutil for relativedelta.py class

### Changed

 - addon.xml updated to use Leia specific syntax and library imports
 - removed specific encode() calls per Python2/3 compatibility
 - call isdigit() method on the string directly instead of str.isdigit() (results in unicode error)
 - added flake8 testing to travis-ci
 - updated code to make python3 compatible
 - updated code for pep9 styling
 - use setArt() to set ListItem icons as the icon= constructor is deprecated
 - Dropbox dependency is now 9.4.0

### Removed

 - removed need for urlparse library
 - Removed GoogleDrive support - issues with python 3 compatibility
 - removed relativedelta.py, use the dateutil module for this

## [Version 1.5.2](https://github.com/robweber/xbmcbackup/compare/krypton-1.5.1...robweber:krypton-1.5.2) - 2019-09-30

### Added

 - Updated Changelog format to the one suggested by [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
 - Added script.module.dropbox import as a dependency for Dropbox filesystem

### Changed

 - Fixed issue getting xbmcbackup.val file from non-zipped remote directories. Was being copied as though it was a local file so it was failing.
 - Use linux path separator (/) all the time, Kodi will interpret this correctly on windows. Was causing issues with remote file systems since os.path.sep
 - Fixed minor python code style changes based on kodi-addon-checker output

### Removed

 - files releated to dropbox library, using script.module.dropbox import now

## Version 1.5.1 - 2019-09-10

### Changed
 - Fixed guisettings restores not working - thanks Bluerayx

## Version 1.5.0 - 2019-08-26

### Added
- Added new Advanced file editor and file selection based on a .json

### Removed
- File backups and restores will not work with old version - breaking change with previous versions PR117

## Version 1.1.3 - 2017-12-29

### Added
 - added file chunk support for Dropbox uploads
 - added scheduler delay to assist with time sync (rpi mostly), will delay startup by 2 min

### Changed
 - fixed settings duplicate ids, thanks aster-anto

## Version 1.1.2

### Added
 - Fixes to the Dropbox lib for python 2.6

## Version 1.1.1

### Added
 - added ability to "catchup" on missed scheduled backup

### Changed
 - fixed error on authorizers (missing secret/key)
 - updated google oauth and client versions
 - merged in dropbox v2 library code

## Version 1.1.0

### Added
 - added tinyurl generation for oauth urls

### Changed
 - moved authorize to settings area for cloud storage

## Version 1.0.9

### Changed
 - fixed dropbox rest.py for Python 2.6 - thanks koying!

## Version 1.0.8

### Changed
 - updated dropbox api

## Version 1.0.7

### Changed
 - updated google client api version

## Version 1.0.6

### Added

 - added progress for zip extraction - hopefully helps with extract errors

### Changed
 - fix for custom directories not working recursively

## Version 1.0.5

### Added
 - added google drive support
 - added settings dialog option - thanks ed_davidson

### Changed
  - make compression setting compatible with python 2.6 and above
  - fix for growing backups - thanks brokeh

## Version 1.0.4

### Added
 - exit if we can't delete the old archive, non recoverable

## Version 1.0.3

### Added
 - added "delete auth" dialog to delete oauth files in settings

## Version 1.0.2

### Changed
 - updated xbmc.python version to 2.19.0 - should be helix only

## Version 1.0.0

### Changed
 - rebranded as "Backup"
 - removed XBMC references and replaced with Kodi
 - tweaked file walking for Helix

## Version 0.5.9

### Added

 - create restored version of guisettings for easy local restoration

### Changed
 - fixed dropbox unicode error

## Version 0.5.8.7

### Added
 - allow limited updating of guisettings file through json

## Version 0.5.8.6

### Added
 - show notification if some files failed
 - check if destination is writeable - thanks war59312

## Version 0.5.8.5

### Added
 - added custom library nodes to config backup options - thanks Ned Scott

## Version 0.5.8.4

### Changed
 - backup compression should use zip64 as sizes may be over 2GB
 - need to expand out path -bugfix

## Version 0.5.8

 - fixes path substitution errors

## Version 0.5.7

 - added option to compress backups, uses local source for staging the
   zip before sending to remote

## Version 0.5.6

 - fix dropbox delete recursion error - thanks durd updated language
   files

## Version 0.5.5

 - fix for dropbox errors during repeated file upload attempts

## Version 0.5.4

 - check xbmc version when doing a restore

## Version 0.5.3

 - updated python version

## Version 0.5.2

 - added additional script and window parameters, thanks Samu-rai
 - critical error in backup rotation
 - updated progress bar display

## Version 0.5.1

 - updated for new Gotham xbmc python updates

## Version 0.5.0

 - New Version for Gotham

## Version 0.4.6

 - modified backup folder names to include time, also modified display
   listing

## Version 0.4.5

 - added version info to logs
- added try/catch for unicode errors

## Version 0.4.4

 - modified the check for invalid file types

## Version 0.4.3

 - added error message if remote directory is blank
 - added license tag

## Version 0.4.2

 - Added support for userdata/profiles folder - thanks TUSSFC

## Version 0.4.1

 - added encode() around notifications

## Version 0.4.0

 - fixed settings display error - thanks zer04c

## Version 0.3.9

 - added "just once" scheduler for one-off type backups
 - show  notification on scheduler  
 - update updated language files from  Transifex

## Version 0.3.8

 - added advancedsettings check on restore. prompts user to restore only this file and restart xbmc to continue. This fixes issues where path substitution was not working during restores - thanks ctrlbru

## [Version 0.3.7]

 - added optional addon.xml tags
 - update language files from Transifex

## Version 0.3.6

 - added up to 2 custom directories, can be toggled on/off
 - added a check for backup verification before rotation - no more
   deleting non backup related files
 - use monitor class for onSettingsChanged method

## Version 0.3.5

 - test of custom directories - only 1 at the moment

## Version 0.3.4

 - added ability to take parameters via RunScript() or
   JSONRPC.Addons.ExecuteAddon()

## Version 0.3.3

 - updated xbmc python version (2.1.0)

## Version 0.3.2

 - added settings for user provided Dropbox key and secret

## Version 0.3.1

 - added try/except for multiple character encodings
 - remove token.txt file if Dropbox Authorization is revoked
 - can shutdown xbmc after scheduled backup

## Version 0.3.0

 - major vfs rewrite
 - Added Dropbox as storage target
 - updated gui/removed settings - thanks SFX Group for idea!

## Version 0.2.3

 - first official frodo build

## Version 0.2.2

 - fix for backup rotation sort

## Version 0.2.1

 - added ability to rotate backups, keeping a set number of days

## Version 0.2.0

 - removed the vfs.py helper library
 - default.py file now uses xbmcvfs python library exclusively for
   listing directories and copy operations

## Version 0.1.7

 - minor bug fixes and translations updates

## Version 0.1.6

 - merged scheduler branch with master, can now schedule backups on an
   interval

## Version 0.1.5

 - pulled xbmcbackup class into separate library

## Version 0.1.4

 - added more verbose error message for incorrect paths

## Version 0.1.3

 - backup folder format - thanks zeroram
 - added German translations - thanks dersphere
 - removed need for separate verbose logging setting
 - updated utf-8 encoding for all logging
 - backup now uses date as folder name, restore allows user to type date
   of last backup

## Version 0.1.2

 - added French language translation - thanks mikebzh44
 - added some utf-8 encoding tags to filenames

## Version 0.1.1

 - added check for key in vfs.py - Thanks Martijn!

## Version 0.1.0

 - removed transparency from icon.png

## Version 0.0.9

 - modified vfs.py again to filter out xsp files (smart playlists).
   Created running list for these types of compressed files
 - added enable/disable logging toggle in settings

## Version 0.0.8

 - modified vfs.py script to exclude handling zip files as directories,
   added keymap and peripheral data folders in the "config" section

## Version 0.0.7

 - removed "restore.txt" file and now write file listing to memory list
   instead

## Version 0.0.6

 - Added the vfs module created by paddycarey
 - File Selection is now followed for both backup and restore options

## Version 0.0.5

 - Added option to manually type a path rather than browse for one (only
   one used)
 - Show progress bar right away so you know this is doing something

## Version 0.0.4

 - Finished code for restore mode.

## Version 0.0.3

 - Added progress bar and "silent" option for running on startup or as a
   script

## Version 0.0.2

 - First version, should backup directories as needed
