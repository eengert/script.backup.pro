"""Immutable Backup Pro operation-settings capture regressions."""
from __future__ import unicode_literals

import json
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

    def test_capture_carries_scheduler_timing_fields_for_recovery_validation(self):
        snapshot = self._capture({
            'cron_schedule': '0 3 * * *',
            'day_of_week': '2',
            'schedule_time': '03:00',
        })
        self.assertEqual('0 3 * * *', snapshot.cron_schedule)
        self.assertEqual('2', snapshot.day_of_week)
        self.assertEqual('03:00', snapshot.schedule_time)


class DestinationStateValidTests(unittest.TestCase):
    def _snapshot(self, values):
        addon = FakeAddon(values)
        with mock.patch.object(settings_module.xbmcaddon, 'Addon',
                               return_value=addon), \
                mock.patch.object(settings_module.xbmcvfs, 'exists',
                                  return_value=False, create=True):
            return BackupOperationSettings.capture()

    def test_primary_path_slot_requires_non_empty_path(self):
        self.assertFalse(self._snapshot(
            {'remote_selection': 0, 'remote_path': '  '})
            .destination_state_valid())
        self.assertTrue(self._snapshot(
            {'remote_selection': 0, 'remote_path': 'smb://host/share'})
            .destination_state_valid())

    def test_secondary_path_slot_requires_non_empty_path(self):
        self.assertFalse(self._snapshot(
            {'remote_selection': 1, 'remote_path_2': ''})
            .destination_state_valid())
        self.assertTrue(self._snapshot(
            {'remote_selection': 1, 'remote_path_2': '/local/path'})
            .destination_state_valid())

    def test_dropbox_slot_requires_both_key_and_secret(self):
        self.assertFalse(self._snapshot(
            {'remote_selection': 2, 'dropbox_key': 'key'})
            .destination_state_valid())
        self.assertFalse(self._snapshot(
            {'remote_selection': 2, 'dropbox_secret': 'secret'})
            .destination_state_valid())
        self.assertTrue(self._snapshot(
            {'remote_selection': 2, 'dropbox_key': 'key',
             'dropbox_secret': 'secret'})
            .destination_state_valid())

    def test_unrecognized_selection_is_invalid(self):
        self.assertFalse(self._snapshot(
            {'remote_selection': 9}).destination_state_valid())


class CriticalSchedulerBaselineTests(unittest.TestCase):
    def _snapshot(self, values):
        addon = FakeAddon(values)
        with mock.patch.object(settings_module.xbmcaddon, 'Addon',
                               return_value=addon), \
                mock.patch.object(settings_module.xbmcvfs, 'exists',
                                  return_value=False, create=True):
            return BackupOperationSettings.capture()

    def test_matching_settings_produce_matching_baselines(self):
        values = {
            'remote_selection': 0, 'remote_path': 'smb://host/share',
            'backup_addons': True, 'compress_backups': True,
        }
        first = self._snapshot(values).critical_scheduler_baseline()
        second = self._snapshot(values).critical_scheduler_baseline()
        self.assertEqual(first, second)

    def test_a_changed_selected_set_changes_the_baseline(self):
        with_addons = self._snapshot(
            {'backup_addons': True}).critical_scheduler_baseline()
        without_addons = self._snapshot(
            {'backup_addons': False}).critical_scheduler_baseline()
        self.assertNotEqual(
            with_addons['backup_addons'], without_addons['backup_addons'])

    def test_secrets_never_appear_in_the_baseline_digest(self):
        secret = 'smb://user:example-password@host/share'
        snapshot = self._snapshot({
            'remote_selection': 0, 'remote_path': secret,
            'dropbox_key': 'private-key', 'dropbox_secret': 'private-secret',
        })
        baseline = snapshot.critical_scheduler_baseline()
        rendered = json.dumps(baseline)
        for forbidden in (secret, 'user', 'example-password', 'private-key',
                          'private-secret'):
            self.assertNotIn(forbidden, rendered)

    def test_free_form_suffix_value_never_appears_in_the_baseline_digest(self):
        snapshot = self._snapshot({'backup_suffix': 'MyHomeAddressBackup'})
        baseline = snapshot.critical_scheduler_baseline()
        self.assertNotIn('MyHomeAddressBackup', json.dumps(baseline))


if __name__ == '__main__':
    unittest.main()
