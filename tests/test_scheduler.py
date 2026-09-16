"""Regression tests for BackupScheduler.doScheduledBackup().

Phase 8 ("Scheduling and UX completion") requires proving the scheduled/
background path never opens a recovery dialog or switches skins when an
AF3 restore is pending -- it must only log and continue, exactly like
Phase 5b's checkPendingSkinRestoreBackground() promises. These tests
exercise the scheduler's wiring of that call, not the recovery logic
itself (already covered by SkinRecoveryDispatchTests).
"""

from __future__ import unicode_literals

import sys
import types
import unittest
from unittest import mock

from tests.test_backup_bridge import install_kodi_stubs

install_kodi_stubs()

# scheduler.py subclasses xbmc.Monitor, which install_kodi_stubs()'s xbmc
# stub doesn't define (nothing else in the suite needs it). Add a minimal
# stand-in; these tests never instantiate BackupScheduler through its real
# __init__, so its behavior is never exercised, only its presence for the
# class statement to succeed.
xbmc_stub = sys.modules['xbmc']
if not hasattr(xbmc_stub, 'Monitor'):
    xbmc_stub.Monitor = type('Monitor', (), {
        '__init__': lambda self, *args, **kwargs: None,
        'abortRequested': lambda self: True,
        'waitForAbort': lambda self, _seconds: True,
    })

# resources.lib.croniter imports dateutil.relativedelta, a third-party
# dependency not installed in this test environment (and unneeded here --
# these tests never exercise croniter's actual schedule math). Stub just
# enough for the import chain to succeed, matching install_kodi_stubs()'s
# existing pattern for pyqrcode/dropbox/future.
if 'dateutil' not in sys.modules:
    dateutil_package = types.ModuleType('dateutil')
    relativedelta_module = types.ModuleType('dateutil.relativedelta')
    relativedelta_module.relativedelta = type('relativedelta', (), {
        '__init__': lambda self, *args, **kwargs: None,
    })
    dateutil_package.relativedelta = relativedelta_module
    sys.modules.setdefault('dateutil', dateutil_package)
    sys.modules.setdefault('dateutil.relativedelta', relativedelta_module)

from resources.lib import scheduler as scheduler_module  # noqa: E402


class FakeBackup:
    """Records only the methods doScheduledBackup() is allowed to call.

    Any other call (a dialog, a skin switch, an interactive resolver)
    raises AttributeError and fails the test -- that absence is the
    regression guard.
    """

    def __init__(self, pending=False, remote_configured=True):
        self.events = []
        self._pending = pending
        self._remote_configured = remote_configured

    def checkPendingSkinRestoreBackground(self):
        self.events.append('checkPendingSkinRestoreBackground')
        return self._pending

    def remoteConfigured(self):
        self.events.append('remoteConfigured')
        return self._remote_configured

    def backup(self, progressOverride=False):
        self.events.append(('backup', progressOverride))
        return True


class FakeUtils:
    def __init__(self, setting_ints=None):
        self._setting_ints = setting_ints or {}
        self.notifications = []
        self.settings_written = {}

    def showNotification(self, message):
        self.notifications.append(message)

    def getString(self, string_id):
        return 'string:%s' % string_id

    def getSettingInt(self, name):
        return self._setting_ints.get(name, 0)

    def setSetting(self, name, value):
        self.settings_written[name] = value

    def log(self, _message, _level=0):
        pass


class FakeOperationSettings:
    def __init__(self, progress_mode=1, schedule_interval=1):
        self.progress_mode = progress_mode
        self.schedule_interval = schedule_interval


class SchedulerBackgroundDeferralTests(unittest.TestCase):
    def _run(self, pending, remote_configured, progress_mode=1,
              setting_ints=None):
        backup = FakeBackup(pending=pending, remote_configured=remote_configured)
        fake_utils = FakeUtils(setting_ints=setting_ints)
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup), \
                mock.patch.object(
                scheduler_module.BackupOperationSettings, 'capture',
                return_value=FakeOperationSettings(progress_mode)), \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            scheduler.doScheduledBackup(progress_mode)

        return backup, fake_utils

    def test_pending_recovery_defers_quietly_and_backup_still_runs(self):
        backup, _ = self._run(pending=True, remote_configured=True)

        self.assertIn('checkPendingSkinRestoreBackground', backup.events)
        self.assertIn('remoteConfigured', backup.events)
        self.assertTrue(any(
            event[0] == 'backup' for event in backup.events
            if isinstance(event, tuple)))

    def test_pending_check_happens_before_backup_runs(self):
        backup, _ = self._run(pending=True, remote_configured=True)

        check_index = backup.events.index('checkPendingSkinRestoreBackground')
        backup_index = next(
            index for index, event in enumerate(backup.events)
            if isinstance(event, tuple) and event[0] == 'backup')
        self.assertLess(check_index, backup_index)

    def test_pending_check_happens_even_when_remote_not_configured(self):
        backup, fake_utils = self._run(pending=True, remote_configured=False)

        self.assertIn('checkPendingSkinRestoreBackground', backup.events)
        self.assertFalse(any(
            isinstance(event, tuple) and event[0] == 'backup'
            for event in backup.events))
        self.assertIn('string:30045', fake_utils.notifications)

    def test_no_pending_recovery_runs_normally(self):
        backup, _ = self._run(pending=False, remote_configured=True)

        self.assertIn('checkPendingSkinRestoreBackground', backup.events)
        self.assertTrue(any(
            isinstance(event, tuple) and event[0] == 'backup'
            for event in backup.events))

    def test_silent_progress_mode_suppresses_start_notification(self):
        _, fake_utils = self._run(
            pending=False, remote_configured=True, progress_mode=2)

        self.assertNotIn('string:30053', fake_utils.notifications)

    def test_non_silent_progress_mode_shows_start_notification(self):
        _, fake_utils = self._run(
            pending=False, remote_configured=True, progress_mode=1)

        self.assertIn('string:30053', fake_utils.notifications)

    def test_unsafe_tvos_session_blocks_scheduled_backup(self):
        backup = FakeBackup(pending=False, remote_configured=True)
        fake_utils = FakeUtils(setting_ints={'progress_mode': 1})
        guard = mock.Mock()
        guard.allow_operation.return_value = False
        guard.admit_backup_snapshot.return_value = None
        guard.admit_scheduler_recovery_snapshot.return_value = None
        guard.last_block_reason.return_value = 'unsafe_reason_not_recoverable'
        guard.recovery_attempt_was_first.return_value = True
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True
        scheduler.settings_guard = guard

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup) as ctor, \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            result = scheduler.doScheduledBackup(1)

        self.assertFalse(result)
        ctor.assert_not_called()
        guard.allow_operation.assert_called_once_with('scheduler_backup')
        guard.admit_scheduler_recovery_snapshot.assert_called_once()
        self.assertIn('string:30237', fake_utils.notifications)

    def test_unsafe_preplan_gate_blocks_scheduled_backup_before_planning(self):
        backup = FakeBackup(pending=False, remote_configured=True)
        fake_utils = FakeUtils(setting_ints={'progress_mode': 1})
        guard = mock.Mock()
        guard.allow_operation.side_effect = (True, False)
        guard.admit_backup_snapshot.return_value = FakeOperationSettings()
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True
        scheduler.settings_guard = guard

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup), \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            result = scheduler.doScheduledBackup(1)

        self.assertFalse(result)
        self.assertEqual(
            [mock.call('scheduler_backup'),
             mock.call('scheduler_backup_preplan')],
            guard.allow_operation.call_args_list)
        self.assertFalse(any(
            isinstance(event, tuple) and event[0] == 'backup'
            for event in backup.events))
        self.assertIn('string:30237', fake_utils.notifications)

    def test_scheduler_passes_shared_guard_to_backup_selection_boundary(self):
        backup = FakeBackup(pending=False, remote_configured=True)
        fake_utils = FakeUtils(setting_ints={'progress_mode': 1})
        guard = mock.Mock()
        guard.allow_operation.side_effect = (True, True)
        snapshot = FakeOperationSettings()
        guard.admit_backup_snapshot.return_value = snapshot
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True
        scheduler.settings_guard = guard

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup) as ctor, \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            scheduler.doScheduledBackup(1)

        ctor.assert_called_once_with(
            settings_guard=guard, operation_settings=snapshot,
            recovered_from_live_update=False)


class FakeMonitor:
    """Runs the loop body exactly `iterations` times, then aborts."""
    def __init__(self, iterations):
        self._remaining = iterations

    def abortRequested(self):
        if self._remaining <= 0:
            return True
        self._remaining -= 1
        return False


class SchedulerLiveUpdateRecoveryDoScheduledBackupTests(unittest.TestCase):
    """doScheduledBackup()'s wiring of admit_scheduler_recovery_snapshot()."""

    def test_recovered_snapshot_runs_backup_and_returns_true(self):
        backup = FakeBackup(pending=False, remote_configured=True)
        fake_utils = FakeUtils(setting_ints={'progress_mode': 1})
        guard = mock.Mock()
        guard.allow_operation.return_value = False
        snapshot = FakeOperationSettings()
        guard.admit_scheduler_recovery_snapshot.return_value = snapshot
        guard.operation_revoked.return_value = False
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True
        scheduler.settings_guard = guard

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup) as ctor, \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            result = scheduler.doScheduledBackup(1)

        self.assertTrue(result)
        ctor.assert_called_once_with(
            settings_guard=guard, operation_settings=snapshot,
            recovered_from_live_update=True)
        guard.operation_revoked.assert_called_once_with(
            admitted_snapshot=True, recovered_from_live_update=True)
        guard.allow_operation.assert_called_once_with('scheduler_backup')
        self.assertTrue(any(
            isinstance(event, tuple) and event[0] == 'backup'
            for event in backup.events))

    def test_recovery_admission_blocked_notifies_once_and_returns_false(self):
        backup = FakeBackup(pending=False, remote_configured=True)
        fake_utils = FakeUtils(setting_ints={'progress_mode': 1})
        guard = mock.Mock()
        guard.allow_operation.return_value = False
        guard.admit_scheduler_recovery_snapshot.return_value = None
        guard.last_block_reason.return_value = 'recovery_baseline_mismatch'
        guard.recovery_attempt_was_first.return_value = True
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True
        scheduler.settings_guard = guard

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup) as ctor, \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            result = scheduler.doScheduledBackup(1)

        self.assertFalse(result)
        ctor.assert_not_called()
        self.assertEqual(1, fake_utils.notifications.count('string:30237'))

    def test_repeat_cooldown_skip_does_not_notify_again(self):
        backup = FakeBackup(pending=False, remote_configured=True)
        fake_utils = FakeUtils(setting_ints={'progress_mode': 1})
        guard = mock.Mock()
        guard.allow_operation.return_value = False
        guard.admit_scheduler_recovery_snapshot.return_value = None
        guard.last_block_reason.return_value = 'recovery_cooldown'
        guard.recovery_attempt_was_first.return_value = False
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True
        scheduler.settings_guard = guard

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup), \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            result = scheduler.doScheduledBackup(1)

        self.assertFalse(result)
        self.assertEqual(0, fake_utils.notifications.count('string:30237'))

    def test_recovered_operation_revoked_before_backup_stays_blocked(self):
        """A live_update reason is still current (or a new severe reason
        appeared) at the preplan recheck immediately before backup() -
        must block there too, not just at the initial gate."""
        backup = FakeBackup(pending=False, remote_configured=True)
        fake_utils = FakeUtils(setting_ints={'progress_mode': 1})
        guard = mock.Mock()
        guard.allow_operation.return_value = False
        guard.admit_scheduler_recovery_snapshot.return_value = (
            FakeOperationSettings())
        guard.operation_revoked.return_value = True
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True
        scheduler.settings_guard = guard

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup), \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            result = scheduler.doScheduledBackup(1)

        self.assertFalse(result)
        self.assertFalse(any(
            isinstance(event, tuple) and event[0] == 'backup'
            for event in backup.events))
        guard.operation_revoked.assert_called_once_with(
            admitted_snapshot=True, recovered_from_live_update=True)

    def test_ordinary_admission_failure_still_returns_false(self):
        """doScheduledBackup() must still return a falsy result (not True)
        when remoteConfigured() is False, so start() does not treat this
        as an attempted run."""
        backup = FakeBackup(pending=False, remote_configured=False)
        fake_utils = FakeUtils(setting_ints={'progress_mode': 1})
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup), \
                mock.patch.object(
                    scheduler_module.BackupOperationSettings, 'capture',
                    return_value=FakeOperationSettings()), \
                mock.patch.object(scheduler_module, 'utils', fake_utils):
            result = scheduler.doScheduledBackup(1)

        self.assertFalse(result)


class SchedulerStartLoopTests(unittest.TestCase):
    """start()'s live_update recovery gating - due status, cooldown, and
    never advancing the schedule on a blocked/failed recovery attempt."""

    def _scheduler(self, guard, iterations, next_run=0.0, enabled=True):
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = enabled
        scheduler.settings_guard = guard
        scheduler.next_run = next_run
        scheduler.monitor = FakeMonitor(iterations)
        return scheduler

    def test_failed_recovery_does_not_advance_next_run(self):
        guard = mock.Mock()
        guard.poll.return_value = False
        guard.unsafe_reason.return_value = 'live_update'
        guard.scheduler_recovery_ready.return_value = True
        scheduler = self._scheduler(guard, iterations=2)
        scheduler.doScheduledBackup = mock.Mock(return_value=False)
        scheduler.findNextRun = mock.Mock()

        with mock.patch.object(
                scheduler_module.time, 'time', return_value=100.0), \
                mock.patch.object(scheduler_module.xbmc, 'sleep'), \
                mock.patch.object(
                    scheduler_module.utils, 'getSettingBool',
                    return_value=False), \
                mock.patch.object(
                    scheduler_module.utils, 'getSettingInt',
                    return_value=1):
            scheduler.start()

        self.assertTrue(scheduler.doScheduledBackup.called)
        scheduler.findNextRun.assert_not_called()
        self.assertEqual(0.0, scheduler.next_run)

    def test_successful_recovery_advances_next_run_exactly_once(self):
        guard = mock.Mock()
        guard.poll.return_value = False
        guard.unsafe_reason.return_value = 'live_update'
        guard.scheduler_recovery_ready.return_value = True
        scheduler = self._scheduler(guard, iterations=1)
        scheduler.doScheduledBackup = mock.Mock(return_value=True)
        scheduler.findNextRun = mock.Mock()

        with mock.patch.object(
                scheduler_module.time, 'time', return_value=100.0), \
                mock.patch.object(scheduler_module.xbmc, 'sleep'), \
                mock.patch.object(
                    scheduler_module.utils, 'getSettingBool',
                    return_value=False), \
                mock.patch.object(
                    scheduler_module.utils, 'getSettingInt',
                    return_value=1):
            scheduler.start()

        scheduler.doScheduledBackup.assert_called_once()
        scheduler.findNextRun.assert_called_once_with(100.0)

    def test_cooldown_skips_doScheduledBackup_entirely(self):
        guard = mock.Mock()
        guard.poll.return_value = False
        guard.unsafe_reason.return_value = 'live_update'
        guard.scheduler_recovery_ready.return_value = False
        scheduler = self._scheduler(guard, iterations=3)
        scheduler.doScheduledBackup = mock.Mock()
        scheduler.findNextRun = mock.Mock()

        with mock.patch.object(scheduler_module.xbmc, 'sleep'):
            scheduler.start()

        scheduler.doScheduledBackup.assert_not_called()
        scheduler.findNextRun.assert_not_called()

    def test_other_unsafe_reason_disables_scheduler_as_before(self):
        guard = mock.Mock()
        guard.poll.return_value = False
        guard.unsafe_reason.return_value = 'settings_view_changed'
        scheduler = self._scheduler(guard, iterations=1, enabled=True)
        scheduler.doScheduledBackup = mock.Mock()

        with mock.patch.object(scheduler_module.xbmc, 'sleep'):
            scheduler.start()

        self.assertFalse(scheduler.enabled)
        scheduler.doScheduledBackup.assert_not_called()

    def test_non_tvos_guard_behavior_is_unchanged(self):
        guard = mock.Mock()
        guard.poll.return_value = True  # inactive guard always allows
        scheduler = self._scheduler(guard, iterations=1)
        scheduler.doScheduledBackup = mock.Mock(return_value=True)
        scheduler.findNextRun = mock.Mock()

        with mock.patch.object(
                scheduler_module.time, 'time', return_value=100.0), \
                mock.patch.object(scheduler_module.xbmc, 'sleep'), \
                mock.patch.object(
                    scheduler_module.utils, 'getSettingBool',
                    return_value=False), \
                mock.patch.object(
                    scheduler_module.utils, 'getSettingInt',
                    return_value=1):
            scheduler.start()

        guard.unsafe_reason.assert_not_called()
        scheduler.doScheduledBackup.assert_called_once()
        scheduler.findNextRun.assert_called_once_with(100.0)

    def test_repeated_ticks_do_not_duplicate_a_successful_run(self):
        guard = mock.Mock()
        guard.poll.return_value = True
        scheduler = self._scheduler(guard, iterations=3)
        calls = []

        def fake_do(progress_mode):
            calls.append(progress_mode)
            return True

        def fake_find_next_run(now):
            # Simulate the real advance-past-now behavior so a later tick
            # correctly sees the schedule as no longer due.
            scheduler.next_run = now + 3600

        scheduler.doScheduledBackup = fake_do
        scheduler.findNextRun = fake_find_next_run

        with mock.patch.object(
                scheduler_module.time, 'time', return_value=100.0), \
                mock.patch.object(scheduler_module.xbmc, 'sleep'), \
                mock.patch.object(
                    scheduler_module.utils, 'getSettingBool',
                    return_value=False), \
                mock.patch.object(
                    scheduler_module.utils, 'getSettingInt',
                    return_value=1):
            scheduler.start()

        self.assertEqual(1, len(calls))


class RefusingDialog:
    """A Dialog that fails the test if scheduler init ever shows UI."""

    def __getattr__(self, name):
        raise AssertionError(
            'BackupScheduler.__init__() must not show any dialog on a '
            'fresh profile (attempted: %s)' % name)


class SchedulerInitTests(unittest.TestCase):
    def test_init_shows_no_dialog_on_a_fresh_profile(self):
        # regression guard: __init__() used to unconditionally show a
        # one-time "Version 1.5.0 requires you to setup your file
        # selections again - this is a breaking change" OK dialog (gated
        # on a hidden upgrade_notes setting whose default made it fire
        # on every fresh install), inherited unchanged from the original
        # Backup add-on this was forked from. No current migration or
        # compatibility behavior depended on it -- upgrade_notes was
        # read/written nowhere else in the codebase -- so it was pure
        # dead legacy messaging referencing a version (1.5.0) predating
        # this add-on's own 0.9.8 versioning. Removed 2026-09-10.
        class FakeUtilsForInit:
            def getSettingBool(self, _name):
                return False

            def data_dir(self):
                return '/profile/addon_data/script.backup.pro/'

            def getString(self, string_id):
                return 'string:%s' % string_id

        class FakeVfs:
            def exists(self, _path):
                return False

            def translatePath(self, path):
                return path

        with mock.patch.object(
                scheduler_module, 'utils', FakeUtilsForInit()), \
                mock.patch.object(scheduler_module, 'xbmcvfs', FakeVfs()), \
                mock.patch.object(
                    scheduler_module, 'xbmcgui',
                    type('xbmcgui', (), {'Dialog': RefusingDialog})()):
            scheduler = scheduler_module.BackupScheduler()

        self.assertFalse(scheduler.enabled)


if __name__ == '__main__':
    unittest.main()
