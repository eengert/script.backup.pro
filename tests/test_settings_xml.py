import os
import unittest
from xml.etree import ElementTree

SETTINGS_XML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'resources', 'settings.xml')


def _find_setting(root, setting_id):
    for setting in root.iter('setting'):
        if setting.get('id') == setting_id:
            return setting
    return None


class SettingsXmlTests(unittest.TestCase):
    """Backup Pro must present exactly one canonical backup destination.
    zip_temp_path is a local staging scratch path used only while
    building a compressed archive before it is copied to that one
    destination - never a second destination - so it must never be
    shown in the UI, regardless of whether Compress Archives is on."""

    def setUp(self):
        self.root = ElementTree.parse(SETTINGS_XML).getroot()

    def test_zip_temp_path_is_never_visible(self):
        # regression guard: two prior attempts at hiding this were each
        # live-disproven on real Kodi. (1) 0.9.12's bare-literal
        # dependency (no `setting` attribute) was silently ignored,
        # leaving the field visible. (2) removing <control> entirely
        # stopped Kodi from registering the setting at all
        # ("CSettingsManager: requested setting (zip_temp_path) was not
        # found"), breaking real compressed backups outright. This
        # asserts the actual, live-confirmed-working mechanism: the
        # <control> is present (so the setting still registers/reads
        # back correctly), and its visibility depends on two
        # *contradictory* conditions on the same real setting
        # (compress_backups cannot be both true and false at once), so
        # it can never be satisfied.
        setting = _find_setting(self.root, 'zip_temp_path')
        self.assertIsNotNone(setting, 'zip_temp_path setting was removed '
                              '(retained on purpose so an existing '
                              "persisted value keeps working)")
        self.assertIsNotNone(
            setting.find('control'),
            'zip_temp_path has no <control> element - Kodi will fail to '
            'register the setting at all, breaking compressed backups')
        dependencies = setting.find('dependencies')
        self.assertIsNotNone(dependencies)
        visible_dependencies = [
            dependency for dependency in dependencies.findall('dependency')
            if dependency.get('type') == 'visible'
        ]
        # every dependency references a real setting (not a bare
        # literal, which Kodi silently ignores) ...
        for dependency in visible_dependencies:
            self.assertEqual('compress_backups', dependency.get('setting'))
        # ... and the set of required values is contradictory, so no
        # value of that setting can ever satisfy all of them at once.
        required_values = {
            (dependency.text or '').strip() for dependency in
            visible_dependencies
        }
        self.assertEqual({'true', 'false'}, required_values)

    def test_zip_temp_path_default_is_unchanged(self):
        # confirms this is still purely internal plumbing with a sane
        # default, not something that needs a user-facing migration.
        setting = _find_setting(self.root, 'zip_temp_path')
        self.assertEqual(
            'special://temp', setting.find('default').text)

    def test_compress_backups_reveals_no_visible_destination_setting(self):
        # compression must not reveal any UI element at all - no setting
        # OTHER than zip_temp_path may become visible based on
        # compress_backups. zip_temp_path itself is exempted here: it
        # does depend on compress_backups, but with contradictory
        # required values that can never both hold (see
        # test_zip_temp_path_is_never_visible), which is a deliberately
        # always-false condition, not a real reveal.
        for setting in self.root.iter('setting'):
            if setting.get('id') == 'zip_temp_path':
                continue
            dependencies = setting.find('dependencies')
            if dependencies is None:
                continue
            for dependency in dependencies.findall('dependency'):
                if dependency.get('setting') == 'compress_backups':
                    self.fail(
                        'setting %r still becomes visible based on '
                        'compress_backups' % setting.get('id'))


if __name__ == '__main__':
    unittest.main()
