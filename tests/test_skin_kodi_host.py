from __future__ import unicode_literals

import unittest

from resources.lib.skin_adapter import AF3_ID, skin_settings_document
from resources.lib.skin_kodi_host import KodiSkinHost, KodiSkinHostError


class FakePlayer:
    def __init__(self):
        self.playing = True
        self.stop_works = True

    def isPlaying(self):
        return self.playing

    def stop(self):
        if self.stop_works:
            self.playing = False


class FakeXbmc:
    def __init__(self):
        self.player = FakePlayer()
        self.skin = AF3_ID

    def Player(self):
        return self.player

    def getSkinDir(self):
        return self.skin


class FakeAddon:
    def __init__(self, addon_id, installed):
        if addon_id not in installed:
            raise RuntimeError('not installed')
        self.version = installed[addon_id]

    def getAddonInfo(self, name):
        return self.version if name == 'version' else ''


class FakeAddonModule:
    def __init__(self, installed):
        self.installed = installed

    def Addon(self, addon_id):
        return FakeAddon(addon_id, self.installed)


class FakeFile:
    def __init__(self, vfs, path, mode=None):
        self.vfs = vfs
        self.path = path
        self.mode = mode

    def size(self):
        return len(self.vfs.files[self.path])

    def readBytes(self, size):
        return self.vfs.files[self.path][:size]

    def write(self, data):
        saved = bytes(data)
        if self.vfs.corrupt_next_write:
            self.vfs.corrupt_next_write = False
            saved += b'corrupt'
        self.vfs.files[self.path] = saved
        return True

    def close(self):
        pass


class FakeVfs:
    def __init__(self):
        self.files = {}
        self.directories = set()
        self.corrupt_next_write = False

    def File(self, path, mode=None):
        return FakeFile(self, path, mode)

    def mkdirs(self, path):
        self.directories.add(path)
        return True

    def exists(self, path):
        return path in self.files or path in self.directories

    def delete(self, path):
        self.files.pop(path, None)
        return True


class KodiSkinHostTests(unittest.TestCase):
    def setUp(self):
        self.xbmc = FakeXbmc()
        self.vfs = FakeVfs()
        self.installed = {
            AF3_ID: '4.2.0',
            'script.skinvariables': '2.0.0',
            'skin.estuary': '1.0.0',
        }
        self.switches = []

        def switch(skin):
            self.switches.append(skin)
            self.xbmc.skin = skin

        self.host = KodiSkinHost(
            self.xbmc, self.vfs, FakeAddonModule(self.installed),
            switch, lambda _seconds: None)
        self.values = [
            {'id': 'demo.bool', 'type': 'boolean', 'value': True},
            {'id': 'demo.text', 'type': 'string', 'value': 'value'},
        ]
        self.document = skin_settings_document(self.values)

    def test_dependency_check_requires_skin_and_helper_versions(self):
        self.host.ensure_dependencies(AF3_ID, 'script.skinvariables')
        self.installed.pop('script.skinvariables')
        with self.assertRaisesRegex(KodiSkinHostError, 'install'):
            self.host.ensure_dependencies(AF3_ID, 'script.skinvariables')

    def test_stop_playback_enforces_observed_postcondition(self):
        self.host.stop_playback()
        self.assertFalse(self.host.is_playing())
        self.xbmc.player.playing = True
        self.xbmc.player.stop_works = False
        with self.assertRaisesRegex(KodiSkinHostError, 'did not stop'):
            self.host.stop_playback()

    def test_ensure_inactive_uses_installed_safe_skin(self):
        self.host.ensure_inactive(AF3_ID)
        self.assertEqual(['skin.estuary'], self.switches)
        self.assertEqual('skin.estuary', self.host.active_skin())

    def test_inactive_target_does_not_switch_skin(self):
        self.xbmc.skin = 'skin.other'
        self.host.ensure_inactive(AF3_ID)
        self.assertEqual([], self.switches)

    def test_stage_settings_writes_and_verifies_through_vfs(self):
        self.xbmc.skin = 'skin.estuary'
        self.host.stage_settings(AF3_ID, self.document, self.values)
        self.assertEqual(
            self.document,
            self.vfs.files[self.host._settings_path(AF3_ID)])

    def test_stage_settings_restores_previous_document_on_corruption(self):
        self.xbmc.skin = 'skin.estuary'
        path = self.host._settings_path(AF3_ID)
        previous = skin_settings_document([
            {'id': 'old', 'type': 'string', 'value': 'kept'},
        ])
        self.vfs.files[path] = previous
        self.vfs.corrupt_next_write = True
        with self.assertRaises(KodiSkinHostError):
            self.host.stage_settings(AF3_ID, self.document, self.values)
        self.assertEqual(previous, self.vfs.files[path])

    def test_active_target_blocks_vfs_write(self):
        with self.assertRaisesRegex(KodiSkinHostError, 'inactive'):
            self.host.stage_settings(AF3_ID, self.document, self.values)
        self.assertEqual({}, self.vfs.files)


if __name__ == '__main__':
    unittest.main()
