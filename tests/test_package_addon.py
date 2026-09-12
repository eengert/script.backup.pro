import importlib.util
import os
import tempfile
import unittest
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "package_addon", Path(__file__).parents[1] / "tools/package_addon.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _make_fake_addon_tree(root):
    """A minimal tree covering every whitelist entry plus the
    development-only clutter a wholesale copy would otherwise leak."""
    os.makedirs(os.path.join(root, 'resources', 'lib', '__pycache__'))
    os.makedirs(os.path.join(root, 'tests'))
    os.makedirs(os.path.join(root, '.git'))
    os.makedirs(os.path.join(root, '.agent'))
    os.makedirs(os.path.join(root, 'tools'))
    os.makedirs(os.path.join(root, 'docs'))
    os.makedirs(os.path.join(root, '.kodi-test'))

    with open(os.path.join(root, 'addon.xml'), 'w') as handle:
        handle.write(
            '<addon id="script.backup.pro" name="Backup Pro" '
            'version="9.9.9"></addon>')
    for name in ('default.py', 'service.py', 'LICENSE.txt'):
        Path(root, name).write_text('runtime file: ' + name)
    Path(root, 'resources', 'lib', 'backup.py').write_text('lib code')
    Path(root, 'resources', 'lib', '__pycache__',
         'backup.cpython-312.pyc').write_bytes(b'\x00compiled')
    Path(root, 'resources', 'lib', '.DS_Store').write_bytes(b'\x00')

    # development-only files a wholesale copy would otherwise leak
    Path(root, 'tests', 'test_backup_bridge.py').write_text('test code')
    Path(root, '.git', 'HEAD').write_text('ref: refs/heads/agent/claude')
    Path(root, '.agent', 'CURRENT_TASK.md').write_text('agent state')
    Path(root, 'tools', 'package_addon.py').write_text('packager itself')
    Path(root, 'docs', 'MAC_KODI_VALIDATION.md').write_text('docs')
    Path(root, '.kodi-test', 'marker').write_text('disposable test state')
    Path(root, 'README.md').write_text('repo-level doc, not runtime')
    Path(root, 'CLAUDE.md').write_text('agent instructions')


class PackageAddonTests(unittest.TestCase):
    def test_package_includes_only_whitelisted_runtime_files(self):
        with tempfile.TemporaryDirectory() as source, \
                tempfile.TemporaryDirectory() as output:
            _make_fake_addon_tree(source)

            addon_dir, zip_path = MODULE.build_package(
                root=source, output_dir=output)

            self.assertTrue(os.path.isdir(addon_dir))
            self.assertTrue(addon_dir.endswith(
                os.path.join('script.backup.pro')))
            self.assertTrue(os.path.isfile(
                os.path.join(addon_dir, 'addon.xml')))
            self.assertTrue(os.path.isfile(
                os.path.join(addon_dir, 'default.py')))
            self.assertTrue(os.path.isfile(
                os.path.join(addon_dir, 'resources', 'lib', 'backup.py')))

            staged_names = set()
            for dirpath, _dirnames, filenames in os.walk(addon_dir):
                for name in filenames:
                    staged_names.add(name)
            self.assertNotIn('backup.cpython-312.pyc', staged_names)
            self.assertNotIn('.DS_Store', staged_names)
            for excluded_dir in ('__pycache__', 'tests', '.git', '.agent',
                                 'tools', 'docs', '.kodi-test'):
                for dirpath, dirnames, _filenames in os.walk(addon_dir):
                    self.assertNotIn(excluded_dir, dirnames)
            self.assertNotIn('README.md', staged_names)
            self.assertNotIn('CLAUDE.md', staged_names)

            self.assertTrue(os.path.isfile(zip_path))
            self.assertIn('script.backup.pro-9.9.9.zip',
                          os.path.basename(zip_path))
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
            self.assertIn('script.backup.pro/addon.xml', names)
            self.assertIn('script.backup.pro/resources/lib/backup.py',
                          names)
            self.assertFalse(any('__pycache__' in n for n in names))
            self.assertFalse(any(n.endswith('.pyc') for n in names))

    def test_package_fails_closed_on_missing_whitelisted_path(self):
        with tempfile.TemporaryDirectory() as source, \
                tempfile.TemporaryDirectory() as output:
            _make_fake_addon_tree(source)
            os.remove(os.path.join(source, 'service.py'))

            with self.assertRaises(FileNotFoundError):
                MODULE.build_package(root=source, output_dir=output)

    def test_real_repository_packages_cleanly(self):
        # exercises the actual whitelist against the real repo tree,
        # not a synthetic fixture - proves every whitelisted path
        # genuinely exists and the real resources/ tree (including its
        # __pycache__ dirs) is filtered correctly.
        with tempfile.TemporaryDirectory() as output:
            addon_dir, zip_path = MODULE.build_package(output_dir=output)
            self.assertTrue(os.path.isfile(
                os.path.join(addon_dir, 'addon.xml')))
            self.assertTrue(os.path.isfile(
                os.path.join(addon_dir, 'resources', 'images',
                             'icon-v2.png')))
            self.assertTrue(os.path.isfile(
                os.path.join(addon_dir, 'resources', 'lib', 'restore_ui.py')))
            with zipfile.ZipFile(zip_path) as archive:
                names = archive.namelist()
                addon = ET.fromstring(
                    archive.read('script.backup.pro/addon.xml').lstrip(
                        b'\xef\xbb\xbf'))
            self.assertIn('script.backup.pro/resources/lib/restore_ui.py',
                          names)
            self.assertIn(
                'script.backup.pro/resources/images/icon-v2.png', names)
            self.assertNotIn(
                'script.backup.pro/resources/images/icon.png', names)
            self.assertEqual(
                'resources/images/icon-v2.png',
                addon.find(
                    './extension[@point="xbmc.addon.metadata"]/'
                    'assets/icon').text)
            self.assertFalse(any('__pycache__' in n for n in names))
            self.assertFalse(any(n.endswith(('.pyc', '.pyo'))
                                  for n in names))
            self.assertFalse(any('.DS_Store' in n for n in names))


if __name__ == '__main__':
    unittest.main()
