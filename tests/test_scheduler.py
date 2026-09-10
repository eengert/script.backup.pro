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


class SchedulerBackgroundDeferralTests(unittest.TestCase):
    def _run(self, pending, remote_configured, progress_mode=1,
              setting_ints=None):
        backup = FakeBackup(pending=pending, remote_configured=remote_configured)
        fake_utils = FakeUtils(setting_ints=setting_ints)
        scheduler = object.__new__(scheduler_module.BackupScheduler)
        scheduler.enabled = True

        with mock.patch.object(
                scheduler_module, 'XbmcBackup', return_value=backup), \
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


if __name__ == '__main__':
    unittest.main()
