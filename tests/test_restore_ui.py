from __future__ import unicode_literals

import unittest

from resources.lib.restore_ui import (
    restore_preparation_message,
    restore_set_labels,
    selected_restore_set_ids,
)


LABELS = {
    30030: 'Installed User Add-ons',
    30031: 'Add-on Settings & Data',
    30032: 'Kodi Databases',
    30033: 'Playlist',
    30034: 'Thumbnails/Fanart',
    30035: 'Kodi Configuration Files',
    30080: 'Profiles',
    30100: 'Extracting Archive',
    30133: 'Game Saves',
    30231: 'Preparing backup for restore...',
    30232: 'Copying compressed archive...',
    30233: 'Reading and verifying compressed archive...',
    30234: 'Arctic Fuse 3 Configuration',
}


class RestoreUiTests(unittest.TestCase):
    def test_known_restore_sets_use_settings_terminology(self):
        internal = [
            'addons', 'addon_data', 'database', 'game_saves', 'playlists',
            'profiles', 'thumbnails', 'config', 'skin_config',
        ]
        self.assertEqual([
            'Installed User Add-ons', 'Add-on Settings & Data',
            'Kodi Databases', 'Game Saves', 'Playlist', 'Profiles',
            'Thumbnails/Fanart', 'Kodi Configuration Files',
            'Arctic Fuse 3 Configuration',
        ], restore_set_labels(internal, LABELS.__getitem__))

    def test_unknown_custom_set_keeps_its_user_supplied_name(self):
        self.assertEqual(
            ['Movies and extras'],
            restore_set_labels(['Movies and extras'], LABELS.__getitem__))

    def test_selected_labels_map_back_to_original_internal_ids(self):
        internal = ['addons', 'addon_data', 'skin_config']
        labels = restore_set_labels(internal, LABELS.__getitem__)
        selected = [labels.index('Add-on Settings & Data'),
                    labels.index('Arctic Fuse 3 Configuration')]
        self.assertEqual(
            ['addon_data', 'skin_config'],
            selected_restore_set_ids(internal, selected))

    def test_folder_restore_starts_with_preparation_message_only(self):
        self.assertEqual(
            'Preparing backup for restore...',
            restore_preparation_message(LABELS.__getitem__))

    def test_compressed_restore_reports_each_preparation_phase(self):
        self.assertEqual(
            'Preparing backup for restore...\nCopying compressed archive...',
            restore_preparation_message(LABELS.__getitem__, 'copy_archive'))
        self.assertEqual(
            'Preparing backup for restore...\n'
            'Reading and verifying compressed archive...',
            restore_preparation_message(LABELS.__getitem__, 'verify_archive'))
        self.assertEqual(
            'Preparing backup for restore...\nExtracting Archive',
            restore_preparation_message(LABELS.__getitem__, 'extract_archive'))


if __name__ == '__main__':
    unittest.main()
