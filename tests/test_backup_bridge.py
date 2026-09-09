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
)
from resources.lib.backup import FileManager, XbmcBackup  # noqa: E402
from resources.lib import backup as backup_module  # noqa: E402
from resources.lib.skin_coordinator import SkinCoordinatorError  # noqa: E402
from tests.test_planning import FakeVfs  # noqa: E402
from tests.test_skin_coordinator import FakeHost  # noqa: E402
from tests.test_skin_restore import fixture as skin_fixture  # noqa: E402


class FakeRecoveryDialog:
    """Records calls; ok()/notification() never block, select() is scripted."""

    def __init__(self, select_return=-1):
        self.select_return = select_return
        self.calls = []

    def select(self, title, options):
        self.calls.append(('select', title, list(options)))
        return self.select_return

    def ok(self, title, message):
        self.calls.append(('ok', title, message))
        return True

    def notification(self, title, message, *_args):
        self.calls.append(('notification', title, message))


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

        backup_module.inspect_pending_restore = fake_inspect
        backup_module.resume_skin_restore_staging = fake_resume
        backup_module.finish_skin_restore = fake_finish
        backup_module.rollback_skin_restore = fake_rollback
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

    def test_unsafe_action_shows_diagnostic_and_leaves_state_untouched(self):
        dialog = FakeRecoveryDialog()
        backup_module.xbmcgui.Dialog = lambda: dialog
        instance = self._instance()
        self.current_action = {'action': 'restart_preflight'}
        self.assertTrue(instance.resolvePendingSkinRestore())
        self.assertEqual([], self.calls)
        self.assertEqual(1, len(dialog.calls))
        self.assertEqual('ok', dialog.calls[0][0])
        self.assertIn('restart_preflight', dialog.calls[0][2])

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
            self.assertEqual(5, len(exclusions))
            self.assertEqual('/profile/skin', exclusions[0]['path'])
            self.assertEqual({
                'blur_v3', 'crop_v2', 'desaturate_v2', 'colors_v2',
            }, {item['path'].rsplit('/', 1)[-1] for item in exclusions[1:]})
        finally:
            backup_module.utils.getSettingBool = original_setting

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

    def test_kodi_file_manager_uses_planner_and_keeps_progress_nonzero(self):
        root = '/empty'
        manager = FileManager(FakeVfs({root: ([], [])}, {}))
        manager.addDir({'type': 'include', 'path': root, 'recurse': True})
        manager.walk()

        self.assertEqual(0, manager.summary()['included_kib'])
        self.assertEqual(1.0, manager.fileSize())
        self.assertEqual(root, manager.getFiles()[0]['file'])

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
        try:
            with tempfile.TemporaryDirectory() as directory:
                backup_module.utils.data_dir = lambda: directory + '/'
                backup_module.xbmcvfs.File = TextFile
                backup_module.xbmcvfs.exists = os.path.exists
                backup_module.xbmcvfs.delete = os.unlink

                instance = object.__new__(XbmcBackup)
                instance.remote_vfs = object()
                instance.xbmc_vfs = object()

                def copy_manifest(_source, _dest, _source_path, dest_path):
                    with open(dest_path, 'w', encoding='utf-8') as handle:
                        handle.write(json.dumps(document))
                    return True

                instance._copyFile = copy_manifest
                result = instance._checkValidationFile('/backup/')
                self.assertEqual('', result['kodi_version'])
        finally:
            backup_module.utils.data_dir = original_data_dir
            for name, value in (
                    ('File', original_file), ('exists', original_exists),
                    ('delete', original_delete)):
                if value is None:
                    delattr(backup_module.xbmcvfs, name)
                else:
                    setattr(backup_module.xbmcvfs, name, value)

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
        rotations = []
        instance._rotateBackups = lambda: rotations.append(True)
        original_notification = backup_module.utils.showNotification
        backup_module.utils.showNotification = lambda _message: None
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
            backup_module.utils.showNotification = original_notification

    def test_backup_wrapper_always_closes_after_failure(self):
        instance = object.__new__(XbmcBackup)
        instance._vfs_closed = False
        instance._active_artifact = '/fresh-backup/'
        instance._active_artifact_compressed = False
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
        original_notification = backup_module.utils.showNotification
        backup_module.utils.showNotification = lambda _message: None
        try:
            self.assertFalse(instance.backup())
            self.assertEqual([True], closed)
            self.assertEqual(['/fresh-backup/'], removed)
        finally:
            backup_module.utils.showNotification = original_notification

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

        class Progress:
            def checkCancel(self):
                return False

            def updateProgress(self, _percent, _message=None):
                pass

            def close(self):
                pass

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
                instance._makeSkinHost = FakeHost

                self.assertTrue(instance._restoreSkinConfig(manifest))
                self.assertFalse(os.path.exists(os.path.join(
                    data, 'pending-skin-restore.json')))
                self.assertTrue(any(item[0] == 'notification'
                                    for item in messages))
        finally:
            backup_module.utils.data_dir = original_data_dir
            backup_module.xbmcvfs.translatePath = original_translate
            if original_dialog is None:
                delattr(backup_module.xbmcgui, 'Dialog')
            else:
                backup_module.xbmcgui.Dialog = original_dialog


if __name__ == '__main__':
    unittest.main()
