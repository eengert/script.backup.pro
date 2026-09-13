"""Immutable Backup Pro operation-settings capture regressions."""
from __future__ import unicode_literals

import unittest
from unittest import mock

from tests.test_backup_bridge import install_kodi_stubs


install_kodi_stubs()

from resources.lib.operation_settings import BackupOperationSettings  # noqa: E402
from resources.lib import operation_settings as settings_module  # noqa: E402


class FakeAddon:
    def __init__(self, values):
        self.values = values

    def getSettingBool(self, name):
        return self.values.get(name, False)

    def getSettingInt(self, name):
        return self.values.get(name, 0)

    def getSetting(self, name):
        return self.values.get(name, '')

    def getAddonInfo(self, name):
        return '/profile/addon_data/script.backup.pro/' if name == 'profile' else ''


class OperationSettingsTests(unittest.TestCase):
    def _capture(self, values):
        addon = FakeAddon(values)
        with mock.patch.object(settings_module.xbmcaddon, 'Addon',
                               return_value=addon), \
                mock.patch.object(settings_module.xbmcvfs, 'exists',
                                  return_value=False, create=True):
            return BackupOperationSettings.capture()

    def test_capture_materializes_selection_and_private_operation_inputs(self):
        snapshot = self._capture({
            'backup_addons': True,
            'backup_database': False,
            'backup_thumbnails': False,
            'remote_selection': 1,
            'remote_path_2': 'smb://private/backup',
            'zip_temp_path': 'special://temp/private',
            'dropbox_key': 'key',
            'dropbox_secret': 'secret',
            'compress_backups': True,
            'backup_rotation': 4,
        })

        self.assertEqual((('addons', True), ('addon_data', False),
                          ('database', False), ('game_saves', False),
                          ('playlists', False), ('profiles', False),
                          ('thumbnails', False), ('config', False)),
                         snapshot.selected_sets)
        self.assertEqual('smb://private/backup', snapshot.remote_path_2)
        self.assertTrue(snapshot.compress_backups)
        self.assertEqual(4, snapshot.backup_rotation)
        # Dataclass repr must not turn a routine log/assertion into a secret leak.
        rendered = repr(snapshot)
        for private in ('smb://private/backup', 'special://temp/private',
                        'key', 'secret'):
            self.assertNotIn(private, rendered)

    def test_each_operation_gets_a_separate_immutable_snapshot(self):
        first = self._capture({'backup_database': False,
                               'remote_path': '/first'})
        second = self._capture({'backup_database': True,
                                'remote_path': '/second'})
        self.assertFalse(first.selected('database'))
        self.assertTrue(second.selected('database'))
        self.assertNotEqual(first, second)


if __name__ == '__main__':
    unittest.main()
