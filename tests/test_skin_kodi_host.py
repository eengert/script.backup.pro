from __future__ import unicode_literals

import json
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
        self.live_settings = []
        self.appearance = {}

    def Player(self):
        return self.player

    def getSkinDir(self):
        return self.skin

    def executeJSONRPC(self, document):
        request = json.loads(document)
        method = request['method']
        params = request['params']
        if method == 'Settings.GetSkinSettings':
            result = {'skin': self.skin, 'settings': self.live_settings}
        elif method == 'Settings.GetSettingValue':
            result = {'value': self.appearance[params['setting']]}
        elif method == 'Settings.SetSettingValue':
            self.appearance[params['setting']] = params['value']
            result = True
        else:
            return json.dumps({'id': 1, 'error': {'code': -32601}})
        return json.dumps({'id': 1, 'result': result})


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
        self.xbmc.live_settings = list(self.values)

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

    def test_activate_skin_uses_switch_callback_and_checks_result(self):
        self.xbmc.skin = 'skin.estuary'
        self.host.activate_skin(AF3_ID)
        self.assertEqual(AF3_ID, self.host.active_skin())
        self.assertEqual([AF3_ID], self.switches)

        self.xbmc.skin = 'skin.estuary'
        self.host.switch_skin = lambda _skin: None
        with self.assertRaisesRegex(KodiSkinHostError, 'did not become'):
            self.host.activate_skin(AF3_ID)

    def test_verify_loaded_settings_requires_disk_and_live_equality(self):
        path = self.host._settings_path(AF3_ID)
        self.vfs.files[path] = self.document
        self.host.verify_loaded_settings(AF3_ID, self.values)

        self.xbmc.live_settings = []
        with self.assertRaisesRegex(KodiSkinHostError, 'did not load'):
            self.host.verify_loaded_settings(AF3_ID, self.values)

    def test_apply_appearance_sets_only_changed_and_verifies(self):
        self.xbmc.appearance = {
            'lookandfeel.skintheme': 'SKINDEFAULT',
            'lookandfeel.skincolors': 'Light',
        }
        self.host.apply_appearance({
            'lookandfeel.skintheme': 'SKINDEFAULT',
            'lookandfeel.skincolors': 'Dark',
        })
        self.assertEqual('Dark', self.xbmc.appearance[
            'lookandfeel.skincolors'])


if __name__ == '__main__':
    unittest.main()
