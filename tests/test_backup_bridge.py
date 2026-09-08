from __future__ import unicode_literals

import json
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
                    'version': '0.2.0'}.get(name, '')

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

from resources.lib.archive import ARCHIVE_ID, ARCHIVE_VERSION  # noqa: E402
from resources.lib.backup import FileManager, XbmcBackup  # noqa: E402
from resources.lib import backup as backup_module  # noqa: E402
from tests.test_planning import FakeVfs  # noqa: E402


class BackupBridgeTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
