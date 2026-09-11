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
        setting = _find_setting(self.root, 'zip_temp_path')
        self.assertIsNotNone(setting, 'zip_temp_path setting was removed '
                              '(retained on purpose so an existing '
                              "persisted value keeps working)")
        dependencies = setting.find('dependencies')
        self.assertIsNotNone(dependencies)
        visible_dependencies = [
            dependency for dependency in dependencies.findall('dependency')
            if dependency.get('type') == 'visible'
        ]
        self.assertTrue(visible_dependencies)
        for dependency in visible_dependencies:
            # unconditional - not gated on compress_backups (or anything
            # else), so it can never become visible under any settings
            # combination.
            self.assertIsNone(dependency.get('setting'))
            self.assertEqual('false', (dependency.text or '').strip())

    def test_zip_temp_path_default_is_unchanged(self):
        # confirms this is still purely internal plumbing with a sane
        # default, not something that needs a user-facing migration.
        setting = _find_setting(self.root, 'zip_temp_path')
        self.assertEqual(
            'special://temp', setting.find('default').text)

    def test_compress_backups_has_no_dependent_destination_setting(self):
        # no remaining setting anywhere depends on compress_backups for
        # visibility - compression must not reveal any UI element at all.
        for setting in self.root.iter('setting'):
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
