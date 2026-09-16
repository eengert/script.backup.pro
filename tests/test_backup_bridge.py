from __future__ import unicode_literals

import json
import hashlib
import os
import sys
import tempfile
import types
import unittest


def install_kodi_stubs():
    xbmc = types.ModuleType('xbmc')
    xbmc.LOGDEBUG = 0
    xbmc.LOGWARNING = 1
    xbmc.getRegion = lambda _name: '%Y-%m-%d'
    xbmc.executeJSONRPC = lambda _request: '{}'
    xbmc.getInfoLabel = lambda _name: ''
    xbmc.getSkinDir = lambda: 'skin.arctic.fuse.3'
    xbmc.executebuiltin = lambda _command: None
    xbmc.log = lambda _message, level=0: None
    xbmc.sleep = lambda _milliseconds: None
    sys.modules.setdefault('xbmc', xbmc)

    xbmcgui = types.ModuleType('xbmcgui')
    xbmcgui.WindowXMLDialog = object
    xbmcgui.DialogProgress = object
    xbmcgui.DialogProgressBG = object
    xbmcgui.NOTIFICATION_INFO = 1
    sys.modules.setdefault('xbmcgui', xbmcgui)

    class Addon:
        def __init__(self, addon_id=None):
            self.addon_id = addon_id

        def getAddonInfo(self, name):
            return {'path': '.', 'profile': '/profile/',
                    'version': '0.9.8'}.get(name, '')

        def getLocalizedString(self, string_id):
            return str(string_id)

        def getSettingBool(self, _name):
            return False

        def getSettingInt(self, _name):
            return 0

        def getSetting(self, _name):
            return ''

        def getSettingString(self, _name):
            return ''

    xbmcaddon = types.ModuleType('xbmcaddon')
    xbmcaddon.Addon = Addon
    sys.modules.setdefault('xbmcaddon', xbmcaddon)

    xbmcvfs = types.ModuleType('xbmcvfs')
    xbmcvfs.translatePath = lambda path: path
    xbmcvfs.validatePath = lambda path: path
    sys.modules.setdefault('xbmcvfs', xbmcvfs)

    sys.modules.setdefault('pyqrcode', types.ModuleType('pyqrcode'))
    future_package = types.ModuleType('future')
    future_moves = types.ModuleType('future.moves')
    future_urllib = types.ModuleType('future.moves.urllib')
    future_request = types.ModuleType('future.moves.urllib.request')
    future_request.urlopen = lambda _url: None
    sys.modules.setdefault('future', future_package)
    sys.modules.setdefault('future.moves', future_moves)
    sys.modules.setdefault('future.moves.urllib', future_urllib)
    sys.modules.setdefault('future.moves.urllib.request', future_request)

    dropbox_package = types.ModuleType('dropbox')
    dropbox_module = types.ModuleType('dropbox.dropbox')
    oauth_module = types.ModuleType('dropbox.oauth')
    dropbox_package.dropbox = dropbox_module
    dropbox_package.oauth = oauth_module
    files_module = types.ModuleType('dropbox.files')
    files_module.WriteMode = type('WriteMode', (), {})
    files_module.CommitInfo = type('CommitInfo', (), {})
    files_module.UploadSessionCursor = type('UploadSessionCursor', (), {})
    sys.modules.setdefault('dropbox', dropbox_package)
    sys.modules.setdefault('dropbox.dropbox', dropbox_module)
    sys.modules.setdefault('dropbox.oauth', oauth_module)
    sys.modules.setdefault('dropbox.files', files_module)


install_kodi_stubs()

from resources.lib.archive import (  # noqa: E402
    ARCHIVE_ID,
    ARCHIVE_VERSION,
    ArchiveValidationError,
    build_manifest,
)
from resources.lib.backup import FileManager, XbmcBackup  # noqa: E402
from resources.lib import backup as backup_module  # noqa: E402
from resources.lib.skin_coordinator import SkinCoordinatorError  # noqa: E402
from tests.test_planning import FakeVfs  # noqa: E402
from tests.test_skin_coordinator import FakeHost  # noqa: E402
from tests.test_skin_restore import fixture as skin_fixture  # noqa: E402


class FakeRecoveryDialog:
    """Records calls; ok()/notification() never block, select()/yesno() are
    scripted."""

    def __init__(self, select_return=-1, yesno_return=True):
        self.select_return = select_return
        self.yesno_return = yesno_return
        self.calls = []

    def select(self, title, options):
        self.calls.append(('select', title, list(options)))
        return self.select_return

    def yesno(self, title, message, **_kwargs):
        self.calls.append(('yesno', title, message))
        return self.yesno_return

    def ok(self, title, message):
        self.calls.append(('ok', title, message))
        return True

    def notification(self, title, message, *_args):
        self.calls.append(('notification', title, message))


class FakeProgress:
    """A progress dialog stub that records close() calls, so a shared
    events list can prove ordering against another recorder (e.g. the
    success/failure Dialog().ok() calls) rather than just presence."""

    def __init__(self, events=None):
        self.events = events if events is not None else []
        self.closed = False

    def checkCancel(self):
        return False

    def updateProgress(self, _percent, _message=None):
        pass

    def close(self):
        self.closed = True
        self.events.append('progress_closed')


class RefusingDialog:
    """A Dialog that fails the test if background code ever shows UI."""

    def __getattr__(self, name):
        raise AssertionError(
            'background/scheduled recovery must never use xbmcgui.Dialog '
            '(attempted: %s)' % name)


class SkinRecoveryDispatchTests(unittest.TestCase):
    """Exercises XbmcBackup's recovery-runtime/UI dispatch in isolation."""

    def setUp(self):
        self._originals = {
            'inspect_pending_restore': backup_module.inspect_pending_restore,
            'resume_skin_restore_staging':
                backup_module.resume_skin_restore_staging,
            'finish_skin_restore': backup_module.finish_skin_restore,
            'rollback_skin_restore': backup_module.rollback_skin_restore,
            'discard_prepared_skin_restore':
                backup_module.discard_prepared_skin_restore,
            'BackupProgressBar': backup_module.BackupProgressBar,
            'Dialog': getattr(backup_module.xbmcgui, 'Dialog', None),
        }
        self.calls = []
        self.current_action = {'action': 'none'}

        def fake_inspect(_profile, _rollback_root, _pending_path):
            if self.current_action.get('action') == 'raise':
                raise SkinCoordinatorError('inconsistent AF3 recovery state')
            return dict(self.current_action)

        def fake_resume(_profile, _rollback_root, _pending_path, _host):
            self.calls.append('resume')

        def fake_finish(_profile, _rollback_root, _pending_path, _host):
            self.calls.append('finish')
            if self.current_action.get('fail_finish'):
                raise RuntimeError('Kodi refused to activate AF3')
            self.current_action = {'action': 'none'}

        def fake_rollback(_profile, _rollback_root, _pending_path, _host):
            self.calls.append('rollback')
            if self.current_action.get('fail_rollback'):
                raise RuntimeError('Kodi refused to roll back AF3')
            self.current_action = {'action': 'none'}

        def fake_discard(_profile, _rollback_root, _pending_path):
            self.calls.append('discard')
            if self.current_action.get('fail_discard'):
                raise RuntimeError('cannot discard AF3 restore')
            action = self.current_action.get('action')
            self.current_action = {'action': 'none'}
            return {
                'action': action,
                'transaction_recovered':
                    action == 'recover_unlinked_transaction',
            }

        backup_module.inspect_pending_restore = fake_inspect
        backup_module.resume_skin_restore_staging = fake_resume
        backup_module.finish_skin_restore = fake_finish
        backup_module.rollback_skin_restore = fake_rollback
        backup_module.discard_prepared_skin_restore = fake_discard
        backup_module.BackupProgressBar = lambda *_a, **_k: type(
            'Progress', (), {
                'create': lambda self, *_a, **_k: None,
                'close': lambda self: None,
            })()

    def tearDown(self):
        for name, value in self._originals.items():
            if name == 'Dialog':
                if value is None:
                    if hasattr(backup_module.xbmcgui, 'Dialog'):
                        delattr(backup_module.xbmcgui, 'Dialog')
                else:
                    backup_module.xbmcgui.Dialog = value
            else:
                setattr(backup_module, name, value)

    def _instance(self):
        instance = object.__new__(XbmcBackup)
        instance._skinRecoveryPaths = lambda: ('/profile', '/rollback',
                                                '/pending')
        instance._makeSkinHost = lambda: object()
        return instance

    def test_nothing_pending_does_not_touch_dialogs(self):
        backup_module.xbmcgui.Dialog = RefusingDialog
        instance = self._instance()
        self.current_action = {'action': 'none'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual([], self.calls)

    def test_restart_preflight_confirmed_discards_with_no_transaction(self):
        dialog = FakeRecoveryDialog(yesno_return=True)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'restart_preflight'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual(['discard'], self.calls)
        self.assertEqual('none', self.current_action['action'])
        self.assertTrue(any(call[0] == 'yesno' for call in dialog.calls))
        self.assertTrue(any(call[0] == 'notification'
                            for call in dialog.calls))

    def test_recover_unlinked_transaction_confirmed_rolls_back_and_discards(
            self):
        dialog = FakeRecoveryDialog(yesno_return=True)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'recover_unlinked_transaction'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual(['discard'], self.calls)
        self.assertEqual('none', self.current_action['action'])

    def test_discard_declined_preserves_pending_state(self):
        dialog = FakeRecoveryDialog(yesno_return=False)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'restart_preflight'}
        self.assertTrue(instance.resolvePendingSkinRestore())
        self.assertEqual([], self.calls)
        self.assertEqual('restart_preflight', self.current_action['action'])

    def test_discard_failure_preserves_pending_state_and_reports_it(self):
        dialog = FakeRecoveryDialog(yesno_return=True)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'restart_preflight',
                               'fail_discard': True}
        self.assertTrue(instance.resolvePendingSkinRestore())
        self.assertEqual(['discard'], self.calls)
        self.assertEqual('restart_preflight', self.current_action['action'])
        self.assertTrue(any(
            call[0] == 'ok' and 'Recovery data was preserved' in call[2]
            for call in dialog.calls))

    def test_inconsistent_state_shows_diagnostic_and_leaves_state_untouched(
            self):
        dialog = FakeRecoveryDialog()
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'raise'}
        self.assertTrue(instance.resolvePendingSkinRestore())
        self.assertEqual([], self.calls)
        self.assertEqual('ok', dialog.calls[0][0])
        self.assertIn('inconsistent AF3 recovery state', dialog.calls[0][2])

    def test_cancelling_preserves_pending_state_and_calls_nothing(self):
        dialog = FakeRecoveryDialog(select_return=-1)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'resume_staging'}
        self.assertTrue(instance.resolvePendingSkinRestore())
        self.assertEqual([], self.calls)
        self.assertEqual('resume_staging', self.current_action['action'])

    def test_explicit_not_now_choice_preserves_pending_state(self):
        # 'Not now' is always the last offered option.
        dialog = FakeRecoveryDialog(select_return=2)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'resume_staging'}
        self.assertTrue(instance.resolvePendingSkinRestore())
        self.assertEqual([], self.calls)

    def test_continue_resumes_staging_then_finishes(self):
        dialog = FakeRecoveryDialog(select_return=0)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'resume_staging'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual(['resume', 'finish'], self.calls)
        self.assertTrue(any(call[0] == 'notification'
                            for call in dialog.calls))

    def test_continue_skips_resume_when_already_staged_for_rebuild(self):
        dialog = FakeRecoveryDialog(select_return=0)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'finish_rebuild'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual(['finish'], self.calls)

    def test_rollback_choice_restores_previous_configuration(self):
        # resume_staging offers ['Continue...', 'Restore previous...', 'Not now']
        dialog = FakeRecoveryDialog(select_return=1)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'resume_staging'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual(['rollback'], self.calls)

    def test_rollback_only_action_offers_no_continue_choice(self):
        dialog = FakeRecoveryDialog(select_return=0)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'rollback_transaction'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual(['rollback'], self.calls)
        offered = dialog.calls[0][2]
        self.assertEqual(2, len(offered))
        self.assertFalse(any('Continue' in option for option in offered))

    def test_finish_failure_preserves_pending_state_and_reports_it(self):
        dialog = FakeRecoveryDialog(select_return=0)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'finish_rebuild',
                               'fail_finish': True}
        self.assertTrue(instance.resolvePendingSkinRestore())
        self.assertEqual(['finish'], self.calls)
        self.assertEqual('finish_rebuild', self.current_action['action'])
        self.assertTrue(any(
            call[0] == 'ok' and 'Recovery data was preserved' in call[2]
            for call in dialog.calls))

    def test_rollback_failure_preserves_pending_state_and_reports_it(self):
        dialog = FakeRecoveryDialog(select_return=1)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'resume_staging',
                               'fail_rollback': True}
        self.assertTrue(instance.resolvePendingSkinRestore())
        self.assertEqual(['rollback'], self.calls)
        self.assertEqual('resume_staging', self.current_action['action'])
        self.assertTrue(any(
            call[0] == 'ok' and 'Recovery data was preserved' in call[2]
            for call in dialog.calls))

    def test_finish_closes_progress_dialog_before_rebuilding(self):
        # AF3's rebuild mechanism activates Kodi windows; a modal progress
        # dialog left open during that call blocks the activation and
        # hangs the rebuild. The dialog must close before finish_skin_
        # restore() runs, not merely afterward.
        order = self.calls
        backup_module.BackupProgressBar = lambda *_a, **_k: type(
            'Progress', (), {
                'create': lambda self, *_a, **_k: None,
                'close': lambda self: order.append('progress_close'),
            })()
        dialog = FakeRecoveryDialog(select_return=0)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'finish_rebuild'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual(['progress_close', 'finish', 'progress_close'],
                          self.calls)

    def test_rollback_closes_progress_dialog_before_rebuilding(self):
        order = self.calls
        backup_module.BackupProgressBar = lambda *_a, **_k: type(
            'Progress', (), {
                'create': lambda self, *_a, **_k: None,
                'close': lambda self: order.append('progress_close'),
            })()
        dialog = FakeRecoveryDialog(select_return=1)
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'resume_staging'}
        self.assertFalse(instance.resolvePendingSkinRestore())
        self.assertEqual(['progress_close', 'rollback', 'progress_close'],
                          self.calls)

    def test_background_check_never_uses_dialogs_when_pending(self):
        backup_module.xbmcgui.Dialog = RefusingDialog
        instance = self._instance()
        self.current_action = {'action': 'resume_staging'}
        self.assertTrue(instance.checkPendingSkinRestoreBackground())
        self.assertEqual([], self.calls)
        self.assertEqual('resume_staging', self.current_action['action'])

    def test_background_check_is_false_and_silent_when_nothing_pending(self):
        backup_module.xbmcgui.Dialog = RefusingDialog
        instance = self._instance()
        self.current_action = {'action': 'none'}
        self.assertFalse(instance.checkPendingSkinRestoreBackground())
        self.assertEqual([], self.calls)


class BackupBridgeTests(unittest.TestCase):
    def _selection_boundary_instance(self, guard):
        instance = object.__new__(XbmcBackup)
        instance.settings_guard = guard
        instance._copy_failures = []
        instance._failure_reason = None
        instance._setupVFS = lambda *_args: True
        instance.remote_vfs = type('Remote', (), {
            'root_path': '/backup/',
            'exists': lambda _self, _path: False,
            'mkdir': lambda _self, _path: True,
        })()
        instance._vfs_closed = False
        instance._closeVFS = lambda: setattr(instance, '_vfs_closed', True)
        return instance

    def test_unsafe_selection_boundary_aborts_before_any_selection_read(self):
        guard = type('Guard', (), {
            'allow_operation': lambda _self, action: action != (
                'backup_selection_boundary'),
            'log_operation_boundary': lambda *_args: None,
        })()
        instance = self._selection_boundary_instance(guard)
        selection_reads = []
        collected = []
        original_get_bool = backup_module.utils.getSettingBool
        original_notify = backup_module.utils.showNotification
        original_log = backup_module.utils.log
        original_string = backup_module.utils.getString
        try:
            backup_module.utils.getSettingBool = lambda name: (
                selection_reads.append(name) or False)
            backup_module.utils.showNotification = lambda _message: None
            backup_module.utils.log = lambda *_args: None
            backup_module.utils.getString = lambda value: 'string:%s' % value
            instance._collectBackupFiles = lambda: collected.append(True)

            self.assertFalse(instance._runBackup())
            self.assertEqual([], collected)
            self.assertEqual([], selection_reads)
            self.assertTrue(instance._vfs_closed)
        finally:
            backup_module.utils.getSettingBool = original_get_bool
            backup_module.utils.showNotification = original_notify
            backup_module.utils.log = original_log
            backup_module.utils.getString = original_string

    def test_safe_selection_boundary_reaches_collection(self):
        guard = type('Guard', (), {
            'allow_operation': lambda _self, _action: True,
            'log_operation_boundary': lambda *_args: None,
        })()
        instance = self._selection_boundary_instance(guard)
        collected = []
        original_get_setting = backup_module.utils.getSetting
        original_get_string = backup_module.utils.getString
        original_log = backup_module.utils.log
        try:
            backup_module.utils.getSetting = lambda _name: '0'
            backup_module.utils.getString = lambda value: 'string:%s' % value
            backup_module.utils.log = lambda *_args: None

            def collect():
                collected.append(True)
                raise RuntimeError('stop after selection boundary')

            instance._collectBackupFiles = collect
            with self.assertRaisesRegex(RuntimeError, 'stop after selection'):
                instance._runBackup()
            self.assertEqual([True], collected)
        finally:
            backup_module.utils.getSetting = original_get_setting
            backup_module.utils.getString = original_get_string
            backup_module.utils.log = original_log

    def test_no_guard_preserves_non_tvos_selection_path(self):
        instance = self._selection_boundary_instance(None)
        collected = []
        original_get_setting = backup_module.utils.getSetting
        original_get_string = backup_module.utils.getString
        original_log = backup_module.utils.log
        try:
            backup_module.utils.getSetting = lambda _name: '0'
            backup_module.utils.getString = lambda value: 'string:%s' % value
            backup_module.utils.log = lambda *_args: None
            instance._collectBackupFiles = lambda: collected.append(True) or []
            instance._createValidationFile = lambda _groups: False
            instance._finalizeBackup = lambda *_args, **_kwargs: False

            self.assertFalse(instance._runBackup())
            self.assertEqual([True], collected)
        finally:
            backup_module.utils.getSetting = original_get_setting
            backup_module.utils.getString = original_get_string
            backup_module.utils.log = original_log

    def test_snapshot_safety_revocation_discards_partial_zip_before_verification(self):
        """A revoked snapshot must never turn its partial ZIP into a backup."""
        events = []
        revoked = [False]
        testcase = self

        class LocalZip:
            root_path = '/backup/'

            def set_root(self, path):
                self.root_path = path

            def cleanup(self):
                events.append('zip_closed')

        class Staging:
            def set_root(self, _path):
                pass

            def rmfile(self, path):
                events.append(('staging_removed', path))
                return True

        class Remote:
            root_path = '/remote/'

            def __getattr__(self, name):
                testcase.fail('remote operation after safety revocation: %s' % name)

        instance = object.__new__(XbmcBackup)
        instance.operation_settings = object()  # admitted immutable snapshot
        instance.settings_guard = object()
        instance._copy_failures = []
        instance._failure_reason = None
        instance._active_artifact = None
        instance._setupVFS = lambda *_args: True
        instance.remote_vfs = LocalZip()
        instance.saved_remote_vfs = Remote()
        instance.xbmc_vfs = Staging()
        instance.ZIP_TEMP_PATH = '/staging'
        instance.transferSize = 0
        instance._allowBackupSelection = lambda _action: True
        instance._setting_int = lambda _name: 0
        instance._setting_bool = lambda name: name == 'compress_backups'
        instance._collectBackupFiles = lambda: [{
            'source': 'special://home/', 'dest': '', 'name': 'addons',
            'files': [{'file': '/profile/addons/a.py', 'size': 1,
                       'is_dir': False}],
        }]
        instance._createValidationFile = lambda _groups: True
        instance._operation_revoked = lambda: revoked[0]
        instance._copyFiles = lambda *_args, **_kwargs: (
            revoked.__setitem__(0, True) or False)
        instance._reportBackupFailure = lambda reason=None: events.append(
            ('restart_required', reason))

        original_zip = backup_module.ZipFileSystem
        original_verify = backup_module.verify_zip_archive
        original_log = backup_module.utils.log
        original_string = backup_module.utils.getString
        try:
            backup_module.ZipFileSystem = LocalZip
            backup_module.verify_zip_archive = lambda *_args, **_kwargs: self.fail(
                'partial ZIP must not be verified')
            backup_module.utils.log = lambda *_args: None
            backup_module.utils.getString = lambda value: 'string:%s' % value

            self.assertFalse(instance._runBackup())
        finally:
            backup_module.ZipFileSystem = original_zip
            backup_module.verify_zip_archive = original_verify
            backup_module.utils.log = original_log
            backup_module.utils.getString = original_string

        self.assertEqual(
            ['zip_closed', ('staging_removed',
                            '/staging/xbmc_backup_temp.zip'),
             ('restart_required', 'string:30237')], events)

    def test_admitted_snapshot_ignores_later_stale_settings_revocation(self):
        class Guard:
            def operation_revoked(self, admitted_snapshot=False,
                                  recovered_from_live_update=False):
                self.admitted_snapshot = admitted_snapshot
                self.recovered_from_live_update = recovered_from_live_update
                return False

        instance = object.__new__(XbmcBackup)
        instance.operation_settings = object()
        instance.settings_guard = Guard()

        self.assertFalse(instance._operation_revoked())
        self.assertTrue(instance.settings_guard.admitted_snapshot)
        self.assertFalse(instance.settings_guard.recovered_from_live_update)

    def test_admitted_snapshot_selection_boundary_allows_stale_settings_only(self):
        class Guard:
            def __init__(self, revoked):
                self.revoked = revoked
                self.admitted_snapshot = None

            def operation_revoked(self, admitted_snapshot=False,
                                  recovered_from_live_update=False):
                self.admitted_snapshot = admitted_snapshot
                return self.revoked

        safe_guard = Guard(False)
        safe = object.__new__(XbmcBackup)
        safe.operation_settings = object()
        safe.settings_guard = safe_guard
        self.assertTrue(safe._allowBackupSelection('selection'))
        self.assertTrue(safe_guard.admitted_snapshot)

        revoked_guard = Guard(True)
        revoked = object.__new__(XbmcBackup)
        revoked.operation_settings = object()
        revoked.settings_guard = revoked_guard
        original_log = backup_module.utils.log
        original_notify = backup_module.utils.showNotification
        original_string = backup_module.utils.getString
        try:
            backup_module.utils.log = lambda *_args: None
            backup_module.utils.showNotification = lambda _message: None
            backup_module.utils.getString = lambda value: 'string:%s' % value
            self.assertFalse(revoked._allowBackupSelection('selection'))
        finally:
            backup_module.utils.log = original_log
            backup_module.utils.showNotification = original_notify
            backup_module.utils.getString = original_string
        self.assertTrue(revoked_guard.admitted_snapshot)

    def test_safety_revocation_discards_partial_folder_without_rotation(self):
        class Remote:
            def __init__(self):
                self.removed = []

            def rmdir(self, path):
                self.removed.append(path)
                return True

        instance = object.__new__(XbmcBackup)
        instance.remote_vfs = Remote()
        instance._active_artifact = '/backup/'
        instance._active_artifact_compressed = False
        instance._reportBackupFailure = lambda reason=None: setattr(
            instance, 'reported_reason', reason)
        original_string = backup_module.utils.getString
        try:
            backup_module.utils.getString = lambda value: 'string:%s' % value
            self.assertFalse(instance._abortRevokedBackup(False))
        finally:
            backup_module.utils.getString = original_string

        self.assertEqual(['/backup/'], instance.remote_vfs.removed)
        self.assertIsNone(instance._active_artifact)
        self.assertEqual('string:30237', instance.reported_reason)

    def test_inner_selection_gate_blocks_race_after_af3_capture(self):
        """Example Room: service may become unsafe during AF3 capture."""
        class Guard:
            def __init__(self):
                self.unsafe = False
                self.actions = []

            def allow_operation(self, action):
                self.actions.append(action)
                return not self.unsafe

            def log_operation_boundary(self, _action):
                pass

        guard = Guard()
        instance = object.__new__(XbmcBackup)
        instance.settings_guard = guard
        instance._vfs_closed = False
        instance._closeVFS = lambda: setattr(instance, '_vfs_closed', True)
        selection_reads = []
        logs = []
        original_bool = backup_module.utils.getSettingBool
        original_int = backup_module.utils.getSettingInt
        original_log = backup_module.utils.log
        original_notify = backup_module.utils.showNotification
        original_string = backup_module.utils.getString
        try:
            backup_module.utils.getSettingBool = lambda name: (
                selection_reads.append(name) or name == 'backup_skin_config')
            backup_module.utils.getSettingInt = lambda name: self.fail(
                'selection read after unsafe state: %s' % name)
            backup_module.utils.log = lambda *args: logs.append(args)
            backup_module.utils.showNotification = lambda _message: None
            backup_module.utils.getString = lambda value: 'string:%s' % value

            def capture():
                guard.unsafe = True
                return {'name': 'skin_config'}

            instance._captureSkinConfigGroup = capture

            self.assertTrue(instance._allowBackupSelection(
                'backup_selection_boundary'))
            self.assertIsNone(instance._collectBackupFiles())
            self.assertEqual(['backup_skin_config'], selection_reads)
            self.assertEqual([
                'backup_selection_boundary',
                'backup_selection_consumption_boundary',
            ], guard.actions)
            self.assertTrue(instance._vfs_closed)
            self.assertFalse(hasattr(instance, 'backup_plan'))
            self.assertFalse(any(
                message.startswith('Backup simple selection:')
                for message, _level in logs))
            self.assertFalse(any(
                message.startswith('Backup planned set IDs:')
                for message, _level in logs))
        finally:
            backup_module.utils.getSettingBool = original_bool
            backup_module.utils.getSettingInt = original_int
            backup_module.utils.log = original_log
            backup_module.utils.showNotification = original_notify
            backup_module.utils.getString = original_string

    def test_inner_selection_gate_allows_safe_selection_after_af3_capture(self):
        guard = type('Guard', (), {
            'allow_operation': lambda _self, _action: True,
            'log_operation_boundary': lambda *_args: None,
        })()
        instance = object.__new__(XbmcBackup)
        instance.settings_guard = guard
        instance._automatic_exclusion_rules = None
        instance._skin_managed_exclusions = []
        instance._automaticExclusions = lambda: []
        instance._readBackupConfig = lambda _path: {
            name: {'root': '/profile/' + name, 'dirs': []}
            for name in XbmcBackup.simple_directory_list
        }
        instance._addBackupDir = lambda name, root, _dirs: {
            'name': name, 'source': root, 'dest': '/backup/', 'files': [],
            'summary': {'included_files': 0, 'included_kib': 0.0,
                        'excluded_files': 0, 'excluded_kib': 0.0,
                        'exclusions': []},
        }
        instance._hashFile = lambda *_args, **_kwargs: None
        instance._captureSkinConfigGroup = lambda: {
            'name': 'skin_config', 'source': '/profile/skin/',
            'dest': '/backup/', 'files': [],
            'summary': {'included_files': 0, 'included_kib': 0.0,
                        'excluded_files': 0, 'excluded_kib': 0.0,
                        'exclusions': []},
        }
        original_bool = backup_module.utils.getSettingBool
        original_int = backup_module.utils.getSettingInt
        original_log = backup_module.utils.log
        try:
            backup_module.utils.getSettingBool = lambda name: name in (
                'backup_addons', 'backup_skin_config')
            backup_module.utils.getSettingInt = lambda _name: 0
            backup_module.utils.log = lambda *_args: None

            groups = instance._collectBackupFiles()
            self.assertEqual(['addons', 'skin_config'],
                             [group['name'] for group in groups])
        finally:
            backup_module.utils.getSettingBool = original_bool
            backup_module.utils.getSettingInt = original_int
            backup_module.utils.log = original_log

    def test_inner_selection_gate_blocks_initializing_state(self):
        guard = type('Guard', (), {
            'allow_operation': lambda _self, _action: False,
            'log_operation_boundary': lambda *_args: None,
        })()
        instance = object.__new__(XbmcBackup)
        instance.settings_guard = guard
        instance._vfs_closed = False
        instance._closeVFS = lambda: setattr(instance, '_vfs_closed', True)
        original_bool = backup_module.utils.getSettingBool
        original_notify = backup_module.utils.showNotification
        original_string = backup_module.utils.getString
        try:
            backup_module.utils.getSettingBool = lambda name: (
                name == 'backup_skin_config')
            backup_module.utils.showNotification = lambda _message: None
            backup_module.utils.getString = lambda value: 'string:%s' % value
            instance._captureSkinConfigGroup = lambda: {'name': 'skin_config'}

            self.assertIsNone(instance._collectBackupFiles())
            self.assertTrue(instance._vfs_closed)
        finally:
            backup_module.utils.getSettingBool = original_bool
            backup_module.utils.showNotification = original_notify
            backup_module.utils.getString = original_string

    def test_simple_selection_honors_database_and_thumbnail_settings(self):
        """Regression for Example Room: unchecked sets must never be planned."""
        original_bool = backup_module.utils.getSettingBool
        original_int = backup_module.utils.getSettingInt
        original_log = backup_module.utils.log
        selected_dirs = {
            name: {'root': '/profile/' + name, 'dirs': []}
            for name in XbmcBackup.simple_directory_list
        }
        try:
            backup_module.utils.getSettingInt = lambda _name: 0
            backup_module.utils.log = lambda *_args: None
            instance = object.__new__(XbmcBackup)
            instance._automatic_exclusion_rules = None
            instance._skin_managed_exclusions = []
            instance._automaticExclusions = lambda: []
            instance._readBackupConfig = lambda _path: selected_dirs
            instance._addBackupDir = lambda name, root, _dirs: {
                'name': name, 'source': root, 'dest': '/backup/',
                'files': [], 'summary': {
                    'included_files': 0, 'included_kib': 0.0,
                    'excluded_files': 0, 'excluded_kib': 0.0,
                    'exclusions': [],
                },
            }
            instance._hashFile = lambda *_args, **_kwargs: None

            for enabled, expected in (
                    ({}, []),
                    ({'backup_addons': True}, ['addons']),
                    ({'backup_database': True}, ['database']),
                    ({'backup_thumbnails': True}, ['thumbnails']),
                    ({'backup_database': True, 'backup_thumbnails': True},
                     ['database', 'thumbnails'])):
                with self.subTest(enabled=enabled):
                    backup_module.utils.getSettingBool = lambda name: bool(
                        enabled.get(name, False))
                    groups = instance._collectBackupFiles()
                    self.assertEqual(expected, [group['name'] for group in groups])
                    if groups:
                        manifest = build_manifest(
                            groups, lambda _path: ('unused', 0))
                        self.assertEqual(expected, [directory['name'] for
                                                    directory in
                                                    manifest['directories']])
        finally:
            backup_module.utils.getSettingBool = original_bool
            backup_module.utils.getSettingInt = original_int
            backup_module.utils.log = original_log

    def test_simple_selection_logs_actual_selected_set_ids(self):
        original_bool = backup_module.utils.getSettingBool
        original_int = backup_module.utils.getSettingInt
        original_log = backup_module.utils.log
        selected_dirs = {
            name: {'root': '/profile/' + name, 'dirs': []}
            for name in XbmcBackup.simple_directory_list
        }
        logs = []
        try:
            backup_module.utils.getSettingInt = lambda _name: 0
            backup_module.utils.getSettingBool = lambda name: name in (
                'backup_addons', 'backup_skin_config')
            backup_module.utils.log = lambda *args: logs.append(args)
            instance = object.__new__(XbmcBackup)
            instance._automatic_exclusion_rules = None
            instance._skin_managed_exclusions = []
            instance._automaticExclusions = lambda: []
            instance._readBackupConfig = lambda _path: selected_dirs
            instance._captureSkinConfigGroup = lambda: {
                'name': 'skin_config', 'source': '/profile/skin/',
                'dest': '/backup/', 'files': [], 'summary': {
                    'included_files': 0, 'included_kib': 0.0,
                    'excluded_files': 0, 'excluded_kib': 0.0,
                    'exclusions': [],
                },
            }
            instance._addBackupDir = lambda name, root, _dirs: {
                'name': name, 'source': root, 'dest': '/backup/',
                'files': [], 'summary': {
                    'included_files': 0, 'included_kib': 0.0,
                    'excluded_files': 0, 'excluded_kib': 0.0,
                    'exclusions': [],
                },
            }
            instance._hashFile = lambda *_args, **_kwargs: None

            groups = instance._collectBackupFiles()
            self.assertEqual(['addons', 'skin_config'],
                             [group['name'] for group in groups])
            self.assertIn(('Backup simple selection: addons',
                           backup_module.xbmc.LOGWARNING), logs)
            self.assertIn(('Backup planned set IDs: addons,skin_config',
                           backup_module.xbmc.LOGWARNING), logs)
        finally:
            backup_module.utils.getSettingBool = original_bool
            backup_module.utils.getSettingInt = original_int
            backup_module.utils.log = original_log

    def test_backup_collection_collapses_and_logs_identical_case_alias(self):
        original_bool = backup_module.utils.getSettingBool
        original_int = backup_module.utils.getSettingInt
        original_log = backup_module.utils.log
        messages = []
        contents = {
            '/profile/addons/Example.txt': b'same',
            '/profile/addons/example.TXT': b'same',
        }
        summary = {
            'included_kib': 8 / 1024.0,
            'included_files': 2,
            'excluded_kib': 0.0,
            'excluded_files': 0,
            'exclusions': [],
        }
        try:
            backup_module.utils.getSettingBool = lambda name: (
                name == 'backup_addons')
            backup_module.utils.getSettingInt = lambda _name: 0
            backup_module.utils.log = lambda message, *_args: messages.append(message)
            instance = object.__new__(XbmcBackup)
            instance.simple_directory_list = ['addons']
            instance._readBackupConfig = lambda _path: {'addons': {
                'root': '/profile/addons', 'dirs': [],
            }}

            def add_group(_name, _root, _dirs):
                instance.transferSize += 8 / 1024.0
                return {
                    'name': 'addons',
                    'source': '/profile/addons',
                    'plan_root': '/profile/addons',
                    'dest': '/backup/',
                    'files': [
                        {'file': path, 'is_dir': False,
                         'size': len(data) / 1024.0}
                        for path, data in contents.items()
                    ],
                    'summary': summary,
                }

            instance._addBackupDir = add_group
            instance._hashFile = lambda path, **_kwargs: (
                hashlib.sha256(contents[path]).hexdigest(), len(contents[path]))

            groups = instance._collectBackupFiles()

            self.assertEqual(['/profile/addons/Example.txt'],
                             [item['file'] for item in groups[0]['files']])
            self.assertEqual(1, instance.backup_plan['file_count'])
            self.assertEqual(1, instance.backup_plan['excluded_files'])
            self.assertTrue(any(
                'Excluded byte-identical case-only source alias' in message
                and 'example.TXT' in message and 'Example.txt' in message
                for message in messages))
        finally:
            backup_module.utils.getSettingBool = original_bool
            backup_module.utils.getSettingInt = original_int
            backup_module.utils.log = original_log

    def test_skin_appearance_uses_parameterized_rpc_and_skips_unavailable(self):
        original_rpc = backup_module.xbmc.executeJSONRPC
        requests = []

        def execute(document):
            request = json.loads(document)
            requests.append(request)
            setting = request['params']['setting']
            if setting == 'lookandfeel.font':
                return json.dumps({'id': 1, 'error': {'code': -32602}})
            values = {
                'lookandfeel.skintheme': 'SKINDEFAULT',
                'lookandfeel.skincolors': 'Dark',
                'lookandfeel.skinzoom': 0,
            }
            return json.dumps({'id': 1, 'result': {
                'value': values[setting],
            }})

        try:
            backup_module.xbmc.executeJSONRPC = execute
            instance = object.__new__(XbmcBackup)
            self.assertEqual({
                'lookandfeel.skintheme': 'SKINDEFAULT',
                'lookandfeel.skincolors': 'Dark',
                'lookandfeel.skinzoom': 0,
            }, instance._skinAppearance())
            self.assertEqual(4, len(requests))
            self.assertTrue(all(request['method'] ==
                                'Settings.GetSettingValue'
                                for request in requests))
        finally:
            backup_module.xbmc.executeJSONRPC = original_rpc

    def test_skin_and_tmdb_exclusions_are_combined(self):
        original_setting = backup_module.utils.getSettingBool
        try:
            backup_module.utils.getSettingBool = lambda name: (
                name == 'exclude_tmdbh_image_cache')
            instance = object.__new__(XbmcBackup)
            instance._automatic_exclusion_rules = None
            instance._skin_managed_exclusions = [{
                'type': 'exclude', 'path': '/profile/skin',
                'adapter': 'skin.arctic.fuse.3', 'reason': 'managed',
            }]
            exclusions = instance._automaticExclusions()
            self.assertEqual(6, len(exclusions))
            self.assertEqual('/profile/skin', exclusions[0]['path'])
            self.assertEqual({
                'blur_v3', 'crop_v2', 'desaturate_v2', 'colors_v2',
            }, {item['path'].rsplit('/', 1)[-1]
                for item in exclusions[2:]})
            self.assertTrue(exclusions[1]['path'].endswith(
                'settings-safety-session.xml'))
        finally:
            backup_module.utils.getSettingBool = original_setting

    def test_settings_safety_marker_is_always_excluded(self):
        original_setting = backup_module.utils.getSettingBool
        original_data_dir = backup_module.utils.data_dir
        original_translate = backup_module.xbmcvfs.translatePath
        try:
            backup_module.utils.getSettingBool = lambda _name: False
            backup_module.utils.data_dir = lambda: (
                'special://profile/addon_data/script.backup.pro/')
            backup_module.xbmcvfs.translatePath = lambda path: path
            instance = object.__new__(XbmcBackup)
            instance._automatic_exclusion_rules = None
            instance._skin_managed_exclusions = []

            exclusions = instance._automaticExclusions()

            self.assertEqual(1, len(exclusions))
            self.assertEqual(
                'special://profile/addon_data/script.backup.pro/'
                'settings-safety-session.xml', exclusions[0]['path'])
        finally:
            backup_module.utils.getSettingBool = original_setting
            backup_module.utils.data_dir = original_data_dir
            backup_module.xbmcvfs.translatePath = original_translate

    def test_stages_af3_snapshot_and_cleans_it_without_touching_profile(self):
        original_data_dir = backup_module.utils.data_dir
        original_capture = backup_module.capture_af3_snapshot
        original_get_info = backup_module.xbmc.getInfoLabel
        original_translate = backup_module.xbmcvfs.translatePath
        try:
            with tempfile.TemporaryDirectory() as directory:
                addon_data = os.path.join(directory, 'addon')
                profile = os.path.join(directory, 'profile')
                os.makedirs(addon_data)
                os.makedirs(profile)
                backup_module.utils.data_dir = lambda: addon_data + '/'
                backup_module.xbmcvfs.translatePath = lambda path: (
                    profile + '/' if path == 'special://profile/' else path)
                backup_module.xbmc.getInfoLabel = lambda name: {
                    'System.FriendlyName': 'MacBook',
                    'System.ProfileName': 'Master',
                }.get(name, '')
                files = {
                    'addon_data/skin.arctic.fuse.3/settings.xml': b'<settings />',
                    'addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/main.json': b'{}',
                }
                backup_module.capture_af3_snapshot = lambda *_args, **_kwargs: {
                    'metadata': {'adapter_id': 'backup-pro.af3'},
                    'files': files,
                }
                instance = object.__new__(XbmcBackup)
                instance._skin_stage_path = None
                instance._skin_snapshot_metadata = None
                instance._skin_managed_exclusions = []
                instance._automatic_exclusion_rules = None
                instance.remote_vfs = type(
                    'Remote', (), {'root_path': '/backup/'})()
                instance._addBackupDir = lambda name, root, dirs: {
                    'name': name, 'source': root, 'dest': '/backup/',
                    'files': [], 'summary': {},
                }

                group = instance._captureSkinConfigGroup()
                stage = instance._skin_stage_path

                self.assertEqual('special://profile/', group['restore_path'])
                self.assertEqual(2, len(instance._skin_managed_exclusions))
                self.assertTrue(os.path.exists(os.path.join(
                    stage, 'addon_data/skin.arctic.fuse.3/settings.xml')))
                self.assertFalse(os.path.exists(os.path.join(
                    profile, 'addon_data/skin.arctic.fuse.3/settings.xml')))
                instance._cleanupSkinStage()
                self.assertFalse(os.path.exists(stage))
        finally:
            backup_module.utils.data_dir = original_data_dir
            backup_module.capture_af3_snapshot = original_capture
            backup_module.xbmc.getInfoLabel = original_get_info
            backup_module.xbmcvfs.translatePath = original_translate

    def test_skin_stage_path_is_unique_across_overlapping_captures(self):
        # regression guard: the staging directory used to be a single
        # fixed name (data_dir() + 'skin-staging'), rewritten via
        # shutil.rmtree()-and-recreate on every capture. Two Backup Pro
        # invocations overlapping in time (confirmed reproducible this
        # session: rapid repeated triggers, or one invocation still
        # finishing while another starts) could then have the second
        # invocation's capture delete and rewrite the exact files the
        # first invocation's own manifest-hash and archive-copy reads
        # were about to see - producing an archived file whose bytes
        # silently didn't match its own recorded manifest hash (the "AF3
        # snapshot payload failed verification" restore failure reported
        # 2026-09-10). A unique per-invocation path removes any
        # possibility of two invocations sharing one staging directory.
        original_data_dir = backup_module.utils.data_dir
        original_capture = backup_module.capture_af3_snapshot
        original_get_info = backup_module.xbmc.getInfoLabel
        original_translate = backup_module.xbmcvfs.translatePath
        try:
            with tempfile.TemporaryDirectory() as directory:
                addon_data = os.path.join(directory, 'addon')
                profile = os.path.join(directory, 'profile')
                os.makedirs(addon_data)
                os.makedirs(profile)
                backup_module.utils.data_dir = lambda: addon_data + '/'
                backup_module.xbmcvfs.translatePath = lambda path: (
                    profile + '/' if path == 'special://profile/' else path)
                backup_module.xbmc.getInfoLabel = lambda name: {
                    'System.FriendlyName': 'MacBook',
                    'System.ProfileName': 'Master',
                }.get(name, '')
                marker_path = 'addon_data/skin.arctic.fuse.3/settings.xml'
                backup_module.capture_af3_snapshot = lambda *_a, **_k: {
                    'metadata': {'adapter_id': 'backup-pro.af3'},
                    'files': {marker_path: b'<settings />'},
                }

                def make_instance():
                    instance = object.__new__(XbmcBackup)
                    instance._skin_stage_path = None
                    instance._skin_snapshot_metadata = None
                    instance._skin_managed_exclusions = []
                    instance._automatic_exclusion_rules = None
                    instance.remote_vfs = type(
                        'Remote', (), {'root_path': '/backup/'})()
                    instance._addBackupDir = lambda name, root, dirs: {
                        'name': name, 'source': root, 'dest': '/backup/',
                        'files': [], 'summary': {},
                    }
                    return instance

                first = make_instance()
                first._captureSkinConfigGroup()
                first_stage = first._skin_stage_path
                first_marker = os.path.join(first_stage, marker_path)
                self.assertTrue(os.path.exists(first_marker))

                # a second, "overlapping" invocation captures before the
                # first one has cleaned up its own staging directory
                second = make_instance()
                second._captureSkinConfigGroup()
                second_stage = second._skin_stage_path

                self.assertNotEqual(first_stage, second_stage)
                self.assertTrue(
                    os.path.exists(first_marker),
                    'the second capture must not disturb the first '
                    "invocation's still-in-use staging directory")
                with open(first_marker, 'rb') as handle:
                    self.assertEqual(b'<settings />', handle.read())

                first._cleanupSkinStage()
                self.assertFalse(os.path.exists(first_stage))
                self.assertTrue(os.path.exists(second_stage))
        finally:
            backup_module.utils.data_dir = original_data_dir
            backup_module.capture_af3_snapshot = original_capture
            backup_module.xbmc.getInfoLabel = original_get_info
            backup_module.xbmcvfs.translatePath = original_translate

    def test_kodi_file_manager_uses_planner_and_keeps_progress_nonzero(self):
        root = '/empty'
        manager = FileManager(FakeVfs({root: ([], [])}, {}))
        manager.addDir({'type': 'include', 'path': root, 'recurse': True})
        manager.walk()

        self.assertEqual(0, manager.summary()['included_kib'])
        self.assertEqual(1.0, manager.fileSize())
        self.assertEqual(root, manager.getFiles()[0]['file'])

    def test_backup_file_manager_omits_sqlite_sidecars_from_manifest_plan(self):
        root = '/profile/addon_data/example'
        manager = FileManager(FakeVfs({
            root: ([], ['settings.db', 'settings.db-shm', 'settings.db-wal']),
        }, {
            root + '/settings.db': 1,
            root + '/settings.db-shm': 1,
            root + '/settings.db-wal': 1,
        }))
        manager.addDir({'type': 'include', 'path': root, 'recurse': True})
        manager.walk()
        group = {
            'name': 'addon_data', 'source': root, 'plan_root': root,
            'files': manager.getFiles(),
        }

        manifest = build_manifest(
            [group], lambda _path: ('a' * 64, 1))
        self.assertEqual(
            ['settings.db'],
            [item['path'] for item in manifest['directories'][0]['files']])

    def _checkValidationFile_with_manifest(self, document, dialog=None):
        """Shared plumbing for _checkValidationFile(): stages `document`
        as the remote manifest and returns whatever _checkValidationFile()
        does with it. No manifest field ever names the device/profile
        that created it (see resources/lib/archive.py::build_manifest) -
        the only restore-time acceptance gate is the kodi_version check
        exercised by the tests below."""

        class TextFile:
            def __init__(self, path, mode):
                self.handle = open(path, mode, encoding='utf-8')

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.handle.close()

            def read(self):
                return self.handle.read()

        original_data_dir = backup_module.utils.data_dir
        original_file = getattr(backup_module.xbmcvfs, 'File', None)
        original_exists = getattr(backup_module.xbmcvfs, 'exists', None)
        original_delete = getattr(backup_module.xbmcvfs, 'delete', None)
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        try:
            with tempfile.TemporaryDirectory() as directory:
                backup_module.utils.data_dir = lambda: directory + '/'
                backup_module.xbmcvfs.File = TextFile
                backup_module.xbmcvfs.exists = os.path.exists
                backup_module.xbmcvfs.delete = os.unlink
                if dialog is not None:
                    backup_module.xbmcgui.Dialog = lambda: dialog

                instance = object.__new__(XbmcBackup)
                instance.remote_vfs = object()
                instance.xbmc_vfs = object()

                def copy_manifest(_source, _dest, _source_path, dest_path):
                    with open(dest_path, 'w', encoding='utf-8') as handle:
                        handle.write(json.dumps(document))
                    return True

                instance._copyFile = copy_manifest
                return instance._checkValidationFile('/backup/')
        finally:
            backup_module.utils.data_dir = original_data_dir
            for name, value in (
                    ('File', original_file), ('exists', original_exists),
                    ('delete', original_delete)):
                if value is None:
                    delattr(backup_module.xbmcvfs, name)
                else:
                    setattr(backup_module.xbmcvfs, name, value)
            if dialog is not None:
                if original_dialog is None:
                    delattr(backup_module.xbmcgui, 'Dialog')
                else:
                    backup_module.xbmcgui.Dialog = original_dialog

    def test_restore_accepts_manifest_kodi_version_field(self):
        document = {
            'archive_id': ARCHIVE_ID,
            'archive_version': ARCHIVE_VERSION,
            'kodi_version': '',
            'directories': [{
                'name': 'config',
                'path': 'special://home/userdata',
                'files': [],
            }],
        }
        result = self._checkValidationFile_with_manifest(document)
        self.assertEqual('', result['kodi_version'])

    def test_restore_of_archive_with_no_device_identity_field_is_accepted(self):
        # A manifest built entirely on a different device (e.g. an Apple TV)
        # carries no source-device/hostname/platform field at all - see
        # resources/lib/archive.py::build_manifest and
        # resources/lib/backup.py::_createValidationFile. Restoring it onto
        # a different device is not a special case; it is simply the
        # ordinary path, which this documents directly.
        document = {
            'archive_id': ARCHIVE_ID,
            'archive_version': ARCHIVE_VERSION,
            'kodi_version': '',
            'addon_id': 'script.backup.pro',
            'directories': [{
                'name': 'addons',
                'path': 'special://home/addons',
                'files': [],
            }],
        }
        result = self._checkValidationFile_with_manifest(document)
        self.assertIsNotNone(result)
        self.assertNotIn('source_device', result)
        self.assertNotIn('device_id', result)
        self.assertNotIn('hostname', result)

    def test_restore_warns_but_continues_past_kodi_version_mismatch(self):
        # The stubbed xbmc.getInfoLabel('System.BuildVersion') always
        # returns '' (install_kodi_stubs() above); any non-empty
        # kodi_version is therefore a mismatch and must only soft-warn,
        # matching a genuine cross-device restore where the target's Kodi
        # build differs from the archive's origin.
        document = {
            'archive_id': ARCHIVE_ID,
            'archive_version': ARCHIVE_VERSION,
            'kodi_version': '20.1.0 (a fictitious future build)',
            'directories': [{
                'name': 'config',
                'path': 'special://home/userdata',
                'files': [],
            }],
        }
        dialog = FakeRecoveryDialog(yesno_return=True)
        result = self._checkValidationFile_with_manifest(document, dialog)
        self.assertIsNotNone(result)
        self.assertTrue(any(call[0] == 'yesno' for call in dialog.calls))

    def test_restore_declines_when_user_rejects_kodi_version_mismatch(self):
        document = {
            'archive_id': ARCHIVE_ID,
            'archive_version': ARCHIVE_VERSION,
            'kodi_version': '20.1.0 (a fictitious future build)',
            'directories': [{
                'name': 'config',
                'path': 'special://home/userdata',
                'files': [],
            }],
        }
        dialog = FakeRecoveryDialog(yesno_return=False)
        result = self._checkValidationFile_with_manifest(document, dialog)
        self.assertIsNone(result)

    def test_folder_readback_verifies_manifest_and_payload(self):
        payload_hash = hashlib.sha256(b'abc').hexdigest()
        instance = object.__new__(XbmcBackup)
        instance.remote_vfs = type('Remote', (), {
            'clean_path': lambda _self, path: path.rstrip('/') + '/',
        })()
        instance.backup_manifest_file_hash = ('manifest-hash', 20)
        instance.backup_manifest = {
            'archive_id': ARCHIVE_ID,
            'archive_version': ARCHIVE_VERSION,
            'directories': [{
                'name': 'config',
                'path': 'special://home/userdata',
                'files': [{
                    'path': 'settings.xml',
                    'size': 3,
                    'sha256': payload_hash,
                }],
            }],
        }
        hashes = {
            '/backup/backup-pro.manifest.json': ('manifest-hash', 20),
            '/backup/config/settings.xml': (payload_hash, 3),
        }
        instance._hashVfsFile = lambda _vfs, path, **_kwargs: hashes[path]
        instance.progressBar = type('Progress', (), {
            'checkCancel': lambda _self: False,
        })()
        self.assertEqual(1, instance._verifyFolderBackup(
            '/backup')['file_count'])

        hashes['/backup/config/settings.xml'] = (
            hashlib.sha256(b'xyz').hexdigest(), 3)
        with self.assertRaises(ArchiveValidationError):
            instance._verifyFolderBackup('/backup')

    def test_copy_failures_are_all_recorded_not_just_the_first(self):
        # regression guard, 2026-09-11: a real-world backup (a large,
        # diverse addon_data/addons tree) hit a copy failure Backup Pro
        # reported only as a vague transient "not all files were
        # copied" notification -- with no indication of which file, or
        # how many. The prior code also only ever logged the *first*
        # failure in a group (`if not wroteFile and result`), so even
        # the log gave no visibility into a multi-file failure. Every
        # failed file must be recorded so the failure dialog (see
        # _backupFailureMessage()) can say exactly what went wrong.
        instance = object.__new__(XbmcBackup)
        instance.progressBar = type('Progress', (), {
            'checkCancel': lambda _self: False,
            'updateProgress': lambda _self, _percent, _message=None: None,
        })()
        instance.transferSize = 100
        instance.transferLeft = 100

        class Dest:
            root_path = '/backup/'

            def exists(self, _path):
                return True

            def mkdir(self, _path):
                return True

            def put(self, source_file, _dest_file):
                # simulate a source file that vanished or became
                # unreadable between listing and copy time -- the most
                # plausible real-world cause on a large, actively-used
                # profile (a background service touching its own cache).
                return 'bad' not in source_file

        class Source:
            root_path = '/profile/'

        files = [
            {'file': '/profile/good1.txt', 'size': 1, 'is_dir': False},
            {'file': '/profile/bad1.txt', 'size': 1, 'is_dir': False},
            {'file': '/profile/good2.txt', 'size': 1, 'is_dir': False},
            {'file': '/profile/bad2.txt', 'size': 1, 'is_dir': False},
        ]

        result = instance._copyFiles(files, Source(), Dest())

        self.assertFalse(result)
        self.assertEqual(
            ['/profile/bad1.txt', '/profile/bad2.txt'],
            instance._copy_failures)

    def test_copy_files_progress_message_override_pins_percent_not_bytes(self):
        # regression guard, 2026-09-12: a real 444MB compressed backup
        # showed a frozen "444 MB remaining" progress dialog for the
        # entire final copy-to-destination step, because that step
        # copies exactly one (already-compressed) file and the ordinary
        # per-file message is only computed once, before the single
        # blocking copy runs, then never updated again. When a caller
        # passes progress_message, _copyFiles must show that fixed,
        # honest message and pin the percent rather than deriving a
        # byte countdown from transferLeft that can never move.
        instance = object.__new__(XbmcBackup)
        updates = []
        instance.progressBar = type('Progress', (), {
            'checkCancel': lambda _self: False,
            'updateProgress': lambda _self, percent, message=None:
                updates.append((percent, message)),
        })()
        instance.transferSize = 444 * 1024 * 1024
        instance.transferLeft = instance.transferSize

        class Dest:
            root_path = '/backup/'

            def exists(self, _path):
                return True

            def mkdir(self, _path):
                return True

            def put(self, _source_file, _dest_file):
                return True

        class Source:
            root_path = '/tmp/'

        files = [{'file': '/tmp/20260912.zip', 'size': instance.transferSize,
                  'is_dir': False}]

        result = instance._copyFiles(
            files, Source(), Dest(),
            progress_message='Compressing backup into ZIP archive...')

        self.assertTrue(result)
        self.assertEqual(1, len(updates))
        percent, message = updates[0]
        self.assertEqual('Compressing backup into ZIP archive...', message)
        self.assertNotIn('remaining', message)
        # a fixed, non-zero percent (not derived from the untouched
        # transferLeft, which would show 0%) -- the bar stays put
        # instead of a fake countdown that never advances.
        self.assertEqual(instance._INDETERMINATE_PERCENT, percent)
        self.assertGreater(percent, 0)

    def test_copy_files_progress_prefix_keeps_phase_and_size_remaining(self):
        instance = object.__new__(XbmcBackup)
        updates = []
        instance.progressBar = type('Progress', (), {
            'checkCancel': lambda _self: False,
            'updateProgress': lambda _self, _percent, _message=None: None,
        })()
        instance._updateProgress = updates.append
        instance.transferSize = 376 * 1024 * 1024
        instance.transferLeft = instance.transferSize

        class Dest:
            root_path = '/staging/'

            def exists(self, _path):
                return True

            def mkdir(self, _path):
                return True

            def put(self, _source_file, _dest_file):
                return True

        class Source:
            root_path = '/remote/'

        result = instance._copyFiles([{
            'file': '/remote/backup.zip',
            'size': instance.transferSize,
            'is_dir': False,
        }], Source(), Dest(), progress_prefix='30231\n30232')

        self.assertTrue(result)
        self.assertEqual(1, len(updates))
        self.assertTrue(updates[0].startswith('30231\n30232\n'))
        self.assertIn('remaining\nwriting backup.zip', updates[0])

    def test_copy_files_streamed_progress_uses_actual_bytes_copied(self):
        instance = object.__new__(XbmcBackup)
        updates = []
        instance.progressBar = type('Progress', (), {
            'checkCancel': lambda _self: False,
            'updateProgress': lambda _self, percent, message=None:
                updates.append((percent, message)),
        })()
        instance.transferSize = 8 * 1024
        instance.transferLeft = instance.transferSize

        class Dest:
            root_path = '/staging/'

            def exists(self, _path):
                return True

            def mkdir(self, _path):
                return True

        class Source:
            root_path = '/remote/'

        def streamed_copy(_source, _dest, report_bytes):
            self.assertTrue(report_bytes(4 * 1024 * 1024))
            self.assertTrue(report_bytes(8 * 1024 * 1024))
            return True

        result = instance._copyFiles([{
            'file': '/remote/backup.zip',
            'size': instance.transferSize,
            'is_dir': False,
        }], Source(), Dest(), progress_prefix='30231\n30232',
            incremental_copy=streamed_copy)

        self.assertTrue(result)
        self.assertEqual([0, 50, 100], [percent for percent, _ in updates])
        self.assertIn('4.00MB remaining', updates[1][1])
        self.assertIn('0.00KB remaining', updates[2][1])
        self.assertTrue(updates[2][1].startswith('30231\n30232\n'))

    def test_copy_files_unknown_progress_can_use_empty_fixed_meter(self):
        instance = object.__new__(XbmcBackup)
        updates = []
        instance.progressBar = type('Progress', (), {
            'checkCancel': lambda _self: False,
            'updateProgress': lambda _self, percent, message=None:
                updates.append((percent, message)),
        })()
        instance.transferSize = 1
        instance.transferLeft = 1

        class Dest:
            root_path = '/staging/'

            def exists(self, _path):
                return True

            def mkdir(self, _path):
                return True

            def put(self, _source, _dest):
                return True

        class Source:
            root_path = '/remote/'

        self.assertTrue(instance._copyFiles([{
            'file': '/remote/backup.zip', 'size': 1, 'is_dir': False,
        }], Source(), Dest(), progress_message='progress unavailable',
            progress_percent=0))
        self.assertEqual([(0, 'progress unavailable')], updates)

    def test_backup_failure_message_reports_reason_and_failed_files(self):
        instance = object.__new__(XbmcBackup)
        instance._failure_reason = 'backup verification failed: checksum mismatch'
        instance._copy_failures = [
            '/profile/addon_data/one.db',
            '/profile/addon_data/two.db',
        ]
        message = instance._backupFailureMessage()
        self.assertIn('30192', message)  # "Backup failed" header
        self.assertIn('checksum mismatch', message)
        self.assertIn('/profile/addon_data/one.db', message)
        self.assertIn('/profile/addon_data/two.db', message)
        self.assertIn('30193', message)  # "file(s) failed to copy"

    def test_backup_failure_message_truncates_a_long_failure_list(self):
        instance = object.__new__(XbmcBackup)
        instance._failure_reason = None
        instance._copy_failures = ['/profile/file%d.txt' % i for i in range(12)]
        message = instance._backupFailureMessage()
        for path in instance._copy_failures[:5]:
            self.assertIn(path, message)
        for path in instance._copy_failures[5:]:
            self.assertNotIn(path, message)
        self.assertIn('7', message)  # 12 - 5 = 7 more, not shown
        self.assertIn('30194', message)  # "more not shown..."

    def test_failed_backup_leaves_no_artifact_and_shows_persistent_dialog(self):
        # a failed backup must never be discoverable as a restore point
        # (per Task A: "failed/incomplete backups are not added to
        # backup history and are not exposed as restore points"), and
        # must be reported with a dialog the user actively dismisses,
        # not a transient notification that could be missed or mistaken
        # for a partial-success warning.
        class Remote:
            def __init__(self):
                self.removed = []

            def rmdir(self, path):
                self.removed.append(path)
                return True

        instance = object.__new__(XbmcBackup)
        instance.remote_vfs = Remote()
        instance._copy_failures = ['/profile/addon_data/cache.db']
        instance._failure_reason = None
        events = []
        instance.progressBar = FakeProgress(events)
        dialogs = []
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        backup_module.xbmcgui.Dialog = lambda: type('D', (), {
            'ok': lambda self, title, message: events.append('dialog_shown') or dialogs.append(
                (title, message)) or True})()
        try:
            result = instance._finalizeBackup(
                False, '/backup/', compressed=False)
        finally:
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog

        self.assertFalse(result)
        self.assertEqual(['/backup/'], instance.remote_vfs.removed)
        self.assertEqual(1, len(dialogs))
        self.assertIn('/profile/addon_data/cache.db', dialogs[0][1])
        self.assertIn('30192', dialogs[0][1])
        # the progress dialog must be closed before the failure dialog is
        # shown, not left open underneath it.
        self.assertTrue(instance.progressBar.closed)
        self.assertEqual(['progress_closed', 'dialog_shown'], events)

    def test_compressed_readback_requires_exact_remote_copy(self):
        instance = object.__new__(XbmcBackup)
        instance.remote_vfs = object()
        instance.progressBar = type('Progress', (), {
            'checkCancel': lambda _self: False,
        })()
        instance._hashFile = lambda _path, **_kwargs: ('same', 10)
        instance._hashVfsFile = lambda _vfs, _path, **_kwargs: ('same', 10)
        self.assertEqual(10, instance._verifyCompressedUpload(
            '/local.zip', '/remote.zip')['total_bytes'])

        instance._hashVfsFile = lambda _vfs, _path, **_kwargs: ('changed', 10)
        with self.assertRaises(ArchiveValidationError):
            instance._verifyCompressedUpload('/local.zip', '/remote.zip')

    def test_retention_runs_only_after_successful_verification(self):
        class Remote:
            def __init__(self):
                self.removed = []

            def rmfile(self, path):
                self.removed.append(('file', path))
                return True

            def rmdir(self, path):
                self.removed.append(('directory', path))
                return True

        instance = object.__new__(XbmcBackup)
        instance.remote_vfs = Remote()
        instance.progressBar = FakeProgress()
        rotations = []
        instance._rotateBackups = lambda: rotations.append(True)
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        backup_module.xbmcgui.Dialog = lambda: type('D', (), {
            'ok': lambda self, _title, _message: True})()
        try:
            self.assertFalse(instance._finalizeBackup(
                False, '/backup/', compressed=False))
            self.assertEqual([], rotations)
            self.assertEqual(
                [('directory', '/backup/')],
                instance.remote_vfs.removed)

            self.assertTrue(instance._finalizeBackup(
                True, '/backup/', compressed=False))
            self.assertEqual([True], rotations)

            instance._rotateBackups = lambda: False
            self.assertFalse(instance._finalizeBackup(
                True, '/verified-backup/', compressed=False))
            self.assertNotIn(
                ('directory', '/verified-backup/'),
                instance.remote_vfs.removed)

            self.assertFalse(instance._finalizeBackup(
                False, '/backup.zip', compressed=True))
            self.assertEqual(
                [('directory', '/backup/'), ('file', '/backup.zip')],
                instance.remote_vfs.removed)
        finally:
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog

    def test_finalize_backup_notifies_a_clear_completion_summary(self):
        class Remote:
            def rmfile(self, _path):
                return True

            def rmdir(self, _path):
                return True

        instance = object.__new__(XbmcBackup)
        instance.remote_vfs = Remote()
        instance._rotateBackups = lambda: True
        instance.backup_plan = {
            'file_count': 42,
            'total_kib': 100,
            'excluded_files': 3,
            'excluded_kib': 50,
            'exclusions': [
                {'adapter': 'plugin.video.themoviedb.helper',
                 'size_kib': 50, 'file_count': 3},
            ],
        }
        instance._skin_snapshot_metadata = {'appearance': {}}
        events = []
        instance.progressBar = FakeProgress(events)
        dialogs = []
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        backup_module.xbmcgui.Dialog = lambda: type('D', (), {
            'ok': lambda self, title, message: events.append('dialog_shown') or dialogs.append(
                (title, message)) or True})()
        try:
            self.assertTrue(instance._finalizeBackup(
                True, '/backup/', compressed=False))
        finally:
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog

        # a persistent dialog the user must dismiss, not a transient
        # notification that can be missed -- consistent with the recent
        # restore-success treatment.
        self.assertEqual(1, len(dialogs))
        message = dialogs[0][1]
        self.assertIn('42', message)
        self.assertIn('3', message)
        # the progress dialog must be closed before success is shown, not
        # left open underneath it (regression guard, 2026-09-12: Eric saw
        # a stale compression progress dialog reappear after dismissing
        # the success dialog on a real 444MB compressed backup).
        self.assertTrue(instance.progressBar.closed)
        self.assertEqual(['progress_closed', 'dialog_shown'], events)
        # the FakeAddon stub's getLocalizedString() returns the numeric
        # string id rather than real English text (see install_kodi_stubs
        # in this file), so assert on the ids these getString() calls
        # resolve to rather than their real-world English copy.
        self.assertIn('30171', message)  # TMDb Helper cache excluded
        self.assertIn('30172', message)  # AF3 configuration included
        self.assertIn('30195', message)  # explicitly valid/usable

    def test_finalize_backup_summary_omits_optional_parts_when_absent(self):
        class Remote:
            def rmfile(self, _path):
                return True

            def rmdir(self, _path):
                return True

        instance = object.__new__(XbmcBackup)
        instance.remote_vfs = Remote()
        instance._rotateBackups = lambda: True
        instance.backup_plan = {'file_count': 5, 'total_kib': 10}
        instance._skin_snapshot_metadata = None
        instance.progressBar = FakeProgress()
        dialogs = []
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        backup_module.xbmcgui.Dialog = lambda: type('D', (), {
            'ok': lambda self, title, message: dialogs.append(
                (title, message)) or True})()
        try:
            self.assertTrue(instance._finalizeBackup(
                True, '/backup/', compressed=False))
        finally:
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog

        message = dialogs[0][1]
        self.assertNotIn('30171', message)
        self.assertNotIn('30172', message)
        self.assertNotIn('30170', message)

    def test_backup_wrapper_always_closes_after_failure(self):
        instance = object.__new__(XbmcBackup)
        instance._vfs_closed = False
        instance._active_artifact = '/fresh-backup/'
        instance._active_artifact_compressed = False
        instance.progressBar = FakeProgress()
        closed = []
        removed = []
        instance.remote_vfs = type('Remote', (), {
            'rmdir': lambda _self, path: removed.append(path),
        })()

        def fail(_progress_override):
            raise IOError('simulated failure')

        def close():
            instance._vfs_closed = True
            closed.append(True)

        instance._runBackup = fail
        instance._closeVFS = close
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        backup_module.xbmcgui.Dialog = lambda: type('D', (), {
            'ok': lambda self, _title, _message: True})()
        try:
            self.assertFalse(instance.backup())
            self.assertEqual([True], closed)
            self.assertEqual(['/fresh-backup/'], removed)
        finally:
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog

    def test_restore_wrapper_always_closes_after_failure(self):
        instance = object.__new__(XbmcBackup)
        closed = []
        instance._runRestore = lambda *_args: (_ for _ in ()).throw(
            IOError('simulated restore failure'))
        instance._closeVFS = lambda: closed.append(True)
        with self.assertRaises(IOError):
            instance.restore()
        self.assertEqual([True], closed)

    def test_runtime_skin_handler_stages_finishes_and_reports(self):
        manifest, files = skin_fixture()
        original_data_dir = backup_module.utils.data_dir
        original_translate = backup_module.xbmcvfs.translatePath
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        messages = []

        class Dialog:
            def yesno(self, title, message, **_kwargs):
                messages.append(('yesno', title, message))
                return True

            def ok(self, title, message):
                messages.append(('ok', title, message))
                return True

            def notification(self, title, message, *_args):
                messages.append(('notification', title, message))

        order = []

        class Progress:
            def checkCancel(self):
                return False

            def updateProgress(self, _percent, _message=None):
                pass

            def close(self):
                order.append('progress_close')

        class OrderedFakeHost(FakeHost):
            def _event(self, name, *values):
                order.append('host:' + name)
                return super(OrderedFakeHost, self)._event(name, *values)

        def make_host():
            return OrderedFakeHost()

        try:
            with tempfile.TemporaryDirectory() as directory:
                profile = os.path.join(directory, 'profile')
                data = os.path.join(directory, 'addon-data')
                os.makedirs(profile)
                backup_module.utils.data_dir = lambda: data + '/'
                backup_module.xbmcvfs.translatePath = lambda path: (
                    profile + '/' if path == 'special://profile/' else path)
                backup_module.xbmcgui.Dialog = Dialog

                instance = object.__new__(XbmcBackup)
                instance.restore_point = '20260908120000.zip'
                instance.progressBar = Progress()
                instance._readRestoreBytes = lambda path: files[
                    path.split('/', 1)[1]]
                instance._makeSkinHost = make_host

                self.assertTrue(instance._restoreSkinConfig(manifest))
                self.assertFalse(os.path.exists(os.path.join(
                    data, 'pending-skin-restore.json')))
                # The final result must be a persistent dialog the user
                # has to dismiss, not a transient toast that can be
                # missed -- confirm the toast path was actually
                # replaced, not just supplemented.
                self.assertTrue(any(
                    item[0] == 'ok' and 'Restored and verified' in item[2]
                    for item in messages))
                self.assertFalse(any(
                    item[0] == 'notification'
                    and 'Restored and verified' in item[2]
                    for item in messages))
                # The pre-restore confirmation must explain the
                # temporary default-skin switch, since this whole
                # method only runs for skin_config restores.
                self.assertTrue(any(
                    item[0] == 'yesno'
                    and 'briefly switch to its default skin' in item[2]
                    for item in messages))
                # _confirmSkinChange() already answers Kodi's native
                # skin-change dialog automatically -- the user is never
                # actually asked, so this staged-files dialog must not
                # tell them to answer it themselves.
                self.assertFalse(any(
                    item[0] == 'ok' and 'Choose Yes' in item[2]
                    for item in messages))
                # The progress dialog must close before finish_skin_
                # restore() activates AF3 (only it, not staging, calls
                # activate_skin) -- a modal dialog left open blocks the
                # window activation AF3's rebuild needs and hangs it.
                self.assertIn('progress_close', order)
                self.assertIn('host:activate_skin', order)
                self.assertLess(order.index('progress_close'),
                                 order.index('host:activate_skin'))
        finally:
            backup_module.utils.data_dir = original_data_dir
            backup_module.xbmcvfs.translatePath = original_translate
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog


class SkinSwitchConfirmationTests(unittest.TestCase):
    """_switchSkin() must answer Kodi's own 'keep this skin?' safety
    dialog itself (see resources/lib/backup.py::_confirmSkinChange())
    rather than depend on a human reacting to its countdown in time --
    reported 2026-09-10: the dialog appeared and Kodi reverted before
    the user got a usable chance to click Yes, leaving Backup Pro's own
    restore transaction genuinely incomplete."""

    def _instance(self):
        instance = object.__new__(XbmcBackup)
        instance.progressBar = type('Progress', (), {
            'close': lambda self: None,
            'create': lambda self, *_a, **_k: None,
        })()
        instance._skin_monitor = type('Monitor', (), {
            'waitForAbort': lambda self, _seconds: False,
        })()
        return instance

    def test_confirm_skin_change_sends_deterministic_yes_click(self):
        # regression guard: an earlier version navigated focus with
        # Action(Up) before selecting, assuming a button layout proven
        # only for AF3's own custom yesno dialog. Live testing found
        # this dialog renders using whichever skin is *currently*
        # active when it appears - Estuary during the temporary
        # ensure_inactive() switch, a completely different layout where
        # Action(Up) did nothing and the dialog was left on "No".
        # SendClick(11) is Kodi's own standard "Yes" control id for
        # this dialog, which every skin's yesno template (including
        # AF3's own) binds its visual Yes button to - no navigation or
        # skin-topology assumption needed at all.
        calls = []
        original_builtin = backup_module.xbmc.executebuiltin
        backup_module.xbmc.executebuiltin = lambda cmd: calls.append(cmd)
        try:
            XbmcBackup._confirmSkinChange()
        finally:
            backup_module.xbmc.executebuiltin = original_builtin
        self.assertEqual(calls, ['SendClick(11)'])

    def test_switch_skin_confirms_the_dialog_exactly_once(self):
        instance = self._instance()
        original_skindir = backup_module.xbmc.getSkinDir
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        target = 'skin.arctic.fuse.3'
        seen = {'n': 0}

        def fake_getskindir():
            seen['n'] += 1
            return 'skin.estuary' if seen['n'] == 1 else target

        backup_module.xbmc.getSkinDir = fake_getskindir
        backup_module.xbmcgui.Dialog = lambda: type('D', (), {
            'notification': lambda self, *_a, **_k: None})()
        instance._skinRpc = lambda *_a, **_k: True
        confirm_calls = []
        instance._confirmSkinChange = lambda: confirm_calls.append('confirm')
        # "active" for 3 confirmation-eligible reads, then gone for 4
        # consecutive reads to satisfy the "kept" exit condition
        active_sequence = iter([True, True, True, False, False, False, False])
        instance._skinConfirmationActive = lambda: next(active_sequence, False)
        original_progress_mode = backup_module.utils.getSettingInt
        backup_module.utils.getSettingInt = lambda _name: 2  # NONE mode
        try:
            instance._switchSkin(target)
        finally:
            backup_module.xbmc.getSkinDir = original_skindir
            backup_module.utils.getSettingInt = original_progress_mode
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog
        # answered exactly once despite the dialog being seen 3 times -
        # repeated presses could navigate past Yes on a second attempt
        self.assertEqual(confirm_calls, ['confirm'])

    def test_switch_skin_notification_uses_a_friendly_skin_name(self):
        instance = self._instance()
        original_skindir = backup_module.xbmc.getSkinDir
        original_addon = backup_module.xbmcaddon.Addon
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        target = 'skin.arctic.fuse.3'
        messages = []
        seen = {'n': 0}

        def fake_getskindir():
            seen['n'] += 1
            return 'skin.estuary' if seen['n'] == 1 else target

        backup_module.xbmc.getSkinDir = fake_getskindir
        backup_module.xbmcaddon.Addon = lambda _addon_id: type('A', (), {
            'getAddonInfo': lambda self, name: (
                'Arctic Fuse 3' if name == 'name' else ''),
            'getLocalizedString': lambda self, string_id: str(string_id)})()
        backup_module.xbmcgui.Dialog = lambda: type('D', (), {
            'notification': lambda self, _title, message, *_a, **_k:
                messages.append(message)})()
        instance._skinRpc = lambda *_a, **_k: True
        instance._skinConfirmationActive = lambda: False
        original_progress_mode = backup_module.utils.getSettingInt
        backup_module.utils.getSettingInt = lambda _name: 2  # NONE mode
        try:
            instance._switchSkin(target)
        finally:
            backup_module.xbmc.getSkinDir = original_skindir
            backup_module.xbmcaddon.Addon = original_addon
            backup_module.utils.getSettingInt = original_progress_mode
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog
        self.assertTrue(any('Arctic Fuse 3' in m for m in messages))
        self.assertFalse(any('Choose Yes' in m for m in messages))


class ArchiveDiscoveryTests(unittest.TestCase):
    """Exercises XbmcBackup.listBackups(), the sole archive-discovery
    mechanism the Restore menu uses (resources/lib/backup.py:172-200).
    It never opens a ZIP or reads a manifest to find compressed archives -
    only filename shape - so these tests also document exactly what
    renaming a backup ZIP is and is not safe to do."""

    class FakeRemoteVfs:
        def __init__(self, dirs, files, manifest_dirs=frozenset(),
                     sizes=None):
            self._dirs = dirs
            self._files = files
            self._manifest_dirs = manifest_dirs
            self._sizes = sizes or {}

        def listdir(self, _directory):
            return list(self._dirs), list(self._files)

        def exists(self, path):
            name = backup_module.MANIFEST_NAME
            return path.rstrip('/').rsplit('/', 1)[-1] == name and (
                path[:-len(name)].rstrip('/').rsplit('/', 1)[-1]
                in self._manifest_dirs)

        def fileSize(self, path):
            return self._sizes.get(path.rsplit('/', 1)[-1], 0)

    def _instance(self, dirs=(), files=(), manifest_dirs=frozenset(),
                  sizes=None):
        instance = object.__new__(XbmcBackup)
        instance.remote_base_path = '/backup/'
        instance.remote_vfs = self.FakeRemoteVfs(
            dirs, files, manifest_dirs, sizes)
        return instance

    def test_zip_with_valid_twelve_digit_prefix_is_discovered(self):
        instance = self._instance(files=['202609160835-exampleroom.zip'])
        result = instance.listBackups()
        self.assertEqual(1, len(result))
        self.assertEqual('202609160835-exampleroom.zip', result[0][0])

    def test_renaming_a_zip_to_drop_the_timestamp_prefix_hides_it(self):
        # Answers "is renaming the ZIP safe?" directly: a copy named for
        # its room/device instead of kept timestamp-first is silently
        # invisible to Restore, not merely mislabeled.
        instance = self._instance(files=['ExampleRoomBackup.zip'])
        self.assertEqual([], instance.listBackups())

    def test_zip_with_prefix_shorter_than_twelve_digits_is_ignored(self):
        instance = self._instance(files=['2026091608.zip'])
        self.assertEqual([], instance.listBackups())

    def test_non_zip_file_is_ignored(self):
        instance = self._instance(files=['202609160835-exampleroom.zip.txt'])
        self.assertEqual([], instance.listBackups())

    def test_folder_backup_requires_adjacent_manifest(self):
        instance = self._instance(
            dirs=['202609160835backup', '202609170900nomanifest'],
            manifest_dirs={'202609160835backup'})
        result = instance.listBackups()
        self.assertEqual(['202609160835backup'], [entry[0] for entry in result])

    def test_renaming_a_zip_to_a_still_valid_prefix_is_discovered(self):
        # A rename that keeps a real, differently-formed YYYYMMDDHHMM
        # prefix ahead of the first '.' still works - the constraint is
        # the leading 12 digits, not the original filename overall.
        instance = self._instance(files=['202609160835.zip'])
        result = instance.listBackups()
        self.assertEqual(['202609160835.zip'], [entry[0] for entry in result])


SMB_WITH_CREDENTIALS = (
    'smb://exampleuser:example-password@192.0.2.10:445/AppleTv/backups/'
    'ExampleRoom - Main/')


class RemoteDestinationDiagnosticsTests(unittest.TestCase):
    """Exercises the SMB diagnostic hardening added for the intermittent
    Apple TV destination failure: sanitized destination logging at the
    admitted-snapshot boundary and at the actual remote-directory-prepare
    failure boundary, and that neither ever leaks a credential."""

    def _fakeOperationSettings(self, **overrides):
        values = {
            'remote_selection': 0,
            'remote_path': SMB_WITH_CREDENTIALS,
            'remote_path_2': '',
            'dropbox_key': '',
            'dropbox_secret': '',
        }
        values.update(overrides)
        return type('FakeOperationSettings', (), values)()

    def test_snapshot_diagnostics_use_the_admitted_snapshot_not_live_settings(self):
        # The admitted snapshot says one destination; live settings (as
        # they would read if re-fetched mid-operation) say another. The
        # diagnostic must reflect only the snapshot, proving no live
        # settings read leaked into destination diagnostics after
        # admission.
        instance = object.__new__(XbmcBackup)
        instance.operation_settings = self._fakeOperationSettings(
            remote_path='smb://192.0.2.10/AppleTv/backups/ExampleRoom - Main/')
        original_get_setting = backup_module.utils.getSetting
        try:
            backup_module.utils.getSetting = lambda _name: (
                'smb://live-settings-should-not-be-used@10.0.0.9/other/')
            result = instance._destinationDiagnostics()
        finally:
            backup_module.utils.getSetting = original_get_setting

        self.assertEqual('192.0.2.10', result['host'])
        self.assertEqual('primary_path', result['slot'])
        self.assertFalse(result['credentials_present'])

    def test_no_operation_settings_falls_back_to_live_settings(self):
        # Restore/status construct XbmcBackup without an admitted
        # snapshot; _setting() (and therefore diagnostics) legitimately
        # fall back to live settings in that case.
        instance = object.__new__(XbmcBackup)
        instance.operation_settings = None
        original_get_setting = backup_module.utils.getSetting
        try:
            backup_module.utils.getSetting = lambda name: {
                'remote_selection': '0',
                'remote_path': 'smb://192.0.2.10/live-path/',
            }.get(name, '')
            result = instance._destinationDiagnostics()
        finally:
            backup_module.utils.getSetting = original_get_setting

        self.assertEqual('192.0.2.10', result['host'])

    def test_current_destination_diagnostics_reflects_the_live_remote_vfs(self):
        instance = object.__new__(XbmcBackup)
        instance.operation_settings = None
        instance.remote_vfs = type('Remote', (), {
            'root_path': SMB_WITH_CREDENTIALS,
        })()
        result = instance._currentDestinationDiagnostics()

        self.assertEqual('192.0.2.10', result['host'])
        self.assertTrue(result['credentials_present'])

    def test_current_destination_diagnostics_explicit_path_overrides_root(self):
        instance = object.__new__(XbmcBackup)
        instance.operation_settings = None
        instance.remote_vfs = type('Remote', (), {
            'root_path': 'smb://192.0.2.10/AppleTv/backups/ExampleRoom - Main/',
        })()
        result = instance._currentDestinationDiagnostics(
            SMB_WITH_CREDENTIALS + '20260910174920.zip')

        self.assertTrue(result['credentials_present'])
        self.assertEqual('/AppleTv/backups/ExampleRoom - Main/'
                          '20260910174920.zip', result['path'])

    def _mkdirFailureInstance(self, remote_root):
        instance = object.__new__(XbmcBackup)
        instance.operation_settings = None
        instance._setupVFS = lambda *_a, **_k: True
        instance.remote_vfs = type('Remote', (), {
            'root_path': remote_root,
            'exists': lambda _self, _path: False,
            'mkdir': lambda _self, _path: False,
        })()
        instance._copy_failures = []
        instance._failure_reason = None
        instance._vfs_closed = False
        instance._closeVFS = lambda: setattr(instance, '_vfs_closed', True)
        instance.progressBar = type('Progress', (), {
            'close': lambda self: None})()
        return instance

    def test_directory_prepare_failure_reports_the_correct_stage(self):
        instance = self._mkdirFailureInstance(SMB_WITH_CREDENTIALS)
        logged = []
        dialog = FakeRecoveryDialog()
        original_log = backup_module.utils.log
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        try:
            backup_module.utils.log = lambda message, *_a: logged.append(
                message)
            backup_module.xbmcgui.Dialog = lambda: dialog
            result = instance._runBackup()
        finally:
            backup_module.utils.log = original_log
            if original_dialog is None:
                if hasattr(backup_module.xbmcgui, 'Dialog'):
                    delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog

        self.assertFalse(result)

        stage_records = [m for m in logged if '"stage":' in m]
        self.assertTrue(any(
            '"directory_prepare_failed"' in m for m in stage_records))
        # The stage that only announces intent (not yet failed) must also
        # have fired, so a hang between the two is distinguishable from
        # an immediate failure.
        self.assertTrue(any(
            '"directory_prepare"' in m and 'failed' not in m
            for m in stage_records))

    def test_no_credentials_appear_anywhere_in_captured_log_output(self):
        instance = self._mkdirFailureInstance(SMB_WITH_CREDENTIALS)
        logged = []
        dialog = FakeRecoveryDialog()
        original_log = backup_module.utils.log
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        try:
            backup_module.utils.log = lambda message, *_a: logged.append(
                message)
            backup_module.xbmcgui.Dialog = lambda: dialog
            instance._runBackup()
        finally:
            backup_module.utils.log = original_log
            if original_dialog is None:
                if hasattr(backup_module.xbmcgui, 'Dialog'):
                    delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog

        combined = '\n'.join(logged)
        self.assertNotIn('exampleuser', combined)
        self.assertNotIn('example-password', combined)
        # Also never shown to the user via the failure dialog's reason text.
        dialog_text = '\n'.join(
            str(call[2]) for call in dialog.calls if call[0] == 'ok')
        self.assertNotIn('exampleuser', dialog_text)
        self.assertNotIn('example-password', dialog_text)

    def test_mkdir_still_receives_the_real_unredacted_destination(self):
        # Sanitization must be log-only: production SMB behavior (the
        # actual path Kodi's VFS is asked to create) is unchanged.
        instance = self._mkdirFailureInstance(SMB_WITH_CREDENTIALS)
        received_paths = []
        instance.remote_vfs.mkdir = lambda path: received_paths.append(
            path) or False
        original_log = backup_module.utils.log
        original_dialog = getattr(backup_module.xbmcgui, 'Dialog', None)
        try:
            backup_module.utils.log = lambda *_a: None
            backup_module.xbmcgui.Dialog = lambda: FakeRecoveryDialog()
            instance._runBackup()
        finally:
            backup_module.utils.log = original_log
            if original_dialog is None:
                if hasattr(backup_module.xbmcgui, 'Dialog'):
                    delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog

        self.assertEqual([SMB_WITH_CREDENTIALS], received_paths)


class RemoteConfiguredTests(unittest.TestCase):
    """remoteConfigured() gates both manual Backup and Restore in
    default.py; it must detect an empty destination setting even though
    Vfs.clean_path() normalizes "" to "/" on the constructed remote_vfs."""

    def setUp(self):
        self._original_get = backup_module.utils.getSetting
        self._original_set = backup_module.utils.setSetting
        self._original_exists = getattr(backup_module.xbmcvfs, 'exists', None)
        self._zip_temp_exists = True
        backup_module.utils.getSetting = lambda name: self._settings.get(name, '')
        backup_module.utils.setSetting = lambda *_a, **_k: None
        backup_module.xbmcvfs.exists = lambda _path: self._zip_temp_exists

    def tearDown(self):
        backup_module.utils.getSetting = self._original_get
        backup_module.utils.setSetting = self._original_set
        if self._original_exists is None:
            delattr(backup_module.xbmcvfs, 'exists')
        else:
            backup_module.xbmcvfs.exists = self._original_exists

    def _configure(self, settings, zip_temp_exists=True):
        self._settings = settings
        self._zip_temp_exists = zip_temp_exists
        instance = object.__new__(XbmcBackup)
        instance.ZIP_TEMP_PATH = '/zip_temp'
        instance.configureRemote()
        return instance

    def test_empty_primary_destination_is_not_configured(self):
        instance = self._configure(
            {'remote_selection': '0', 'remote_path': ''})
        self.assertEqual('/', instance.remote_base_path)
        self.assertFalse(instance.remoteConfigured())

    def test_empty_secondary_destination_is_not_configured(self):
        instance = self._configure(
            {'remote_selection': '1', 'remote_path_2': ''})
        self.assertEqual('/', instance.remote_base_path)
        self.assertFalse(instance.remoteConfigured())

    def test_secondary_destination_never_rewrites_primary_setting(self):
        writes = []
        backup_module.utils.setSetting = lambda *args: writes.append(args)

        instance = self._configure({
            'remote_selection': '1',
            'remote_path': 'smb://old-primary/path',
            'remote_path_2': 'smb://selected-secondary/path',
        })

        self.assertEqual('smb://selected-secondary/path',
                         instance._remote_raw_path)
        self.assertTrue(instance.remoteConfigured())
        self.assertEqual([], writes)

    def test_normalized_root_only_path_never_looks_configured(self):
        # a blank setting always normalizes to root_path == "/" - assert
        # remoteConfigured() does not mistake that for a real destination.
        instance = self._configure(
            {'remote_selection': '0', 'remote_path': '   '})
        self.assertFalse(instance.remoteConfigured())

    def test_valid_smb_path_remains_configured(self):
        instance = self._configure({
            'remote_selection': '0',
            'remote_path': 'smb://user:pass@host/share/backups',
        })
        self.assertTrue(instance.remoteConfigured())

    def test_valid_local_path_remains_configured(self):
        instance = self._configure({
            'remote_selection': '0',
            'remote_path': '/local/backups',
        })
        self.assertTrue(instance.remoteConfigured())

    def test_missing_zip_temp_path_still_blocks_when_path_is_valid(self):
        instance = self._configure(
            {'remote_selection': '0', 'remote_path': '/local/backups'},
            zip_temp_exists=False)
        self.assertFalse(instance.remoteConfigured())

    def test_backup_declines_mkdir_when_destination_not_configured(self):
        instance = self._configure(
            {'remote_selection': '0', 'remote_path': ''})
        self.assertFalse(instance.remoteConfigured())

        mkdir_calls = []
        instance.remote_vfs.mkdir = (
            lambda directory: mkdir_calls.append(directory) or True)

        # mirrors default.py's BACKUP dispatch gate: backup() only runs
        # when remoteConfigured() is true.
        if instance.remoteConfigured():
            instance.backup()

        self.assertEqual([], mkdir_calls)

    def test_restore_declines_remote_listing_when_destination_not_configured(self):
        instance = self._configure(
            {'remote_selection': '0', 'remote_path': ''})
        self.assertFalse(instance.remoteConfigured())

        listdir_calls = []
        instance.remote_vfs.listdir = (
            lambda directory: listdir_calls.append(directory) or ([], []))

        # mirrors default.py's RESTORE dispatch gate: listBackups()/
        # restore() only run when remoteConfigured() is true.
        if instance.remoteConfigured():
            instance.listBackups()

        self.assertEqual([], listdir_calls)


class StatusReportTests(unittest.TestCase):
    """Exercises XbmcBackup.buildStatusReport()'s Kodi-side wiring: it
    must stay read-only, local-only (no remote listing, no network probe)
    and delegate the actual formatting to status.describe_status()."""

    def _instance(self, remote_configured=False, recovery_kind='none',
                  remote_selection='0', remote_base_path=''):
        instance = object.__new__(XbmcBackup)
        instance.remoteConfigured = lambda: remote_configured
        instance.inspectSkinRecovery = lambda: {'kind': recovery_kind}
        instance.remote_base_path = remote_base_path
        self._remote_selection = remote_selection
        return instance

    def setUp(self):
        self._original_get_setting = backup_module.utils.getSetting
        self._original_get_setting_bool = backup_module.utils.getSettingBool
        self._remote_selection = '0'
        backup_module.utils.getSetting = lambda name: (
            self._remote_selection if name == 'remote_selection' else '')
        backup_module.utils.getSettingBool = lambda _name: False

    def tearDown(self):
        backup_module.utils.getSetting = self._original_get_setting
        backup_module.utils.getSettingBool = self._original_get_setting_bool

    def test_no_destination_no_recovery_no_scheduler(self):
        instance = self._instance()
        report = instance.buildStatusReport()
        self.assertEqual(
            [key for key, _detail in report],
            ['last_backup_unknown', 'remote_not_configured',
             'recovery_clear', 'scheduler_disabled'])

    def test_dropbox_destination_uses_its_localized_name(self):
        instance = self._instance(
            remote_configured=True, remote_selection='2')
        report = instance.buildStatusReport()
        self.assertIn(('remote_configured', '30027'), report)

    def test_path_destination_shows_the_configured_path(self):
        instance = self._instance(
            remote_configured=True, remote_selection='0',
            remote_base_path='/mnt/backups/')
        report = instance.buildStatusReport()
        self.assertIn(('remote_configured', '/mnt/backups/'), report)

    def test_a_successful_backup_is_reflected_in_status(self):
        # regression guard, 2026-09-11: buildStatusReport() hardcoded
        # last_backup = {'known': False} unconditionally (Phase 8,
        # commit cd64127) -- Status said "No backup history" even
        # immediately after a genuinely successful, verified backup,
        # which is exactly what a real user reported and could not
        # distinguish from an actual failure. Status must stay
        # local-only (no remote listing -- a Dropbox listing in
        # particular would risk a slow network call just to open the
        # screen), so a successful backup now records a small local
        # marker at completion time for Status to read back cheaply.
        with tempfile.TemporaryDirectory() as directory:
            original_data_dir = backup_module.utils.data_dir
            original_translate = backup_module.xbmcvfs.translatePath
            backup_module.utils.data_dir = lambda: directory + '/'
            backup_module.xbmcvfs.translatePath = lambda path: (
                directory + '/' if path == 'special://profile/addon_data/'
                or path == directory + '/' else path)
            try:
                instance = self._instance()
                # nothing recorded yet
                self.assertEqual(
                    [key for key, _detail in instance.buildStatusReport()],
                    ['last_backup_unknown', 'remote_not_configured',
                     'recovery_clear', 'scheduler_disabled'])

                instance._recordLastBackup('/backups/20260911162220/')
                report = instance.buildStatusReport()
            finally:
                backup_module.utils.data_dir = original_data_dir
                backup_module.xbmcvfs.translatePath = original_translate

        keys = [key for key, _detail in report]
        self.assertEqual(keys[0], 'last_backup_known')
        # the FakeAddon stub's getRegionalTimestamp-driven _dateFormat()
        # is exercised for real here (not stubbed), so just confirm a
        # non-empty, genuinely-parsed label came back rather than a
        # blank or raw placeholder.
        self.assertTrue(report[0][1])

    def test_pending_recovery_is_surfaced_and_leads(self):
        instance = self._instance(recovery_kind='discard')
        report = instance.buildStatusReport()
        self.assertEqual(report[0], ('recovery_pending', None))

    def test_scheduler_disabled_never_touches_next_run_file(self):
        original_exists = getattr(backup_module.xbmcvfs, 'exists', None)

        def refuse_exists(_path):
            raise AssertionError(
                'scheduler is disabled; next_run.txt must not be read')

        backup_module.xbmcvfs.exists = refuse_exists
        try:
            instance = self._instance()
            report = instance.buildStatusReport()
            self.assertIn(('scheduler_disabled', None), report)
        finally:
            if original_exists is None:
                delattr(backup_module.xbmcvfs, 'exists')
            else:
                backup_module.xbmcvfs.exists = original_exists

    def test_scheduler_enabled_reads_next_run_from_local_file(self):
        class TextFile:
            def __init__(self, path, mode='r'):
                self.handle = open(path, mode, encoding='utf-8')

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                self.handle.close()

            def read(self):
                return self.handle.read()

        original_data_dir = backup_module.utils.data_dir
        original_file = getattr(backup_module.xbmcvfs, 'File', None)
        original_exists = getattr(backup_module.xbmcvfs, 'exists', None)
        backup_module.utils.getSettingBool = lambda name: (
            name == 'enable_scheduler')
        try:
            with tempfile.TemporaryDirectory() as directory:
                backup_module.utils.data_dir = lambda: directory + '/'
                backup_module.xbmcvfs.File = TextFile
                backup_module.xbmcvfs.exists = os.path.exists
                with open(os.path.join(directory, 'next_run.txt'), 'w',
                          encoding='utf-8') as handle:
                    handle.write('4102444800')  # 2100-01-01, well into the future

                instance = self._instance()
                report = instance.buildStatusReport()
                keys = [key for key, _detail in report]
                self.assertIn('scheduler_enabled_next', keys)
        finally:
            backup_module.utils.data_dir = original_data_dir
            for name, value in (
                    ('File', original_file), ('exists', original_exists)):
                if value is None:
                    delattr(backup_module.xbmcvfs, name)
                else:
                    setattr(backup_module.xbmcvfs, name, value)


if __name__ == '__main__':
    unittest.main()
