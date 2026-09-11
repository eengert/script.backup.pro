from __future__ import unicode_literals

import tempfile
import unittest
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree

from resources.lib import skin_adapter


class SkinAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.profile = Path(self.temporary.name) / 'profile'
        self.profile.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, data):
        path = self.profile / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def result(self, settings=None, skin=skin_adapter.AF3_ID):
        return {'skin': skin, 'settings': settings or []}

    def test_live_settings_are_typed_sorted_and_portable(self):
        values = skin_adapter.live_skin_setting_values(self.result([
            {'id': 'zeta', 'type': 'string', 'value': '<value>'},
            {'id': 'Enabled', 'type': 'boolean', 'value': True},
            {'id': 'disabled', 'type': 'boolean', 'value': False},
        ]))
        document = skin_adapter.skin_settings_document(values)
        root = ElementTree.fromstring(document)

        self.assertEqual(['disabled', 'Enabled', 'zeta'],
                         [item['id'] for item in values])
        self.assertEqual(['false', 'true', '<value>'],
                         [item.text for item in root.findall('setting')])
        self.assertEqual(['bool', 'bool', 'string'],
                         [item.get('type') for item in root.findall('setting')])

    def test_live_settings_reject_wrong_skin_invalid_type_and_duplicates(self):
        invalid = (
            self.result(skin='skin.estuary'),
            self.result([{'id': 'bad/id', 'type': 'string', 'value': 'x'}]),
            self.result([{'id': 'x', 'type': 'boolean', 'value': 'true'}]),
            self.result([
                {'id': 'x', 'type': 'string', 'value': 'one'},
                {'id': 'x', 'type': 'string', 'value': 'two'},
            ]),
        )
        for result in invalid:
            with self.subTest(result=result):
                with self.assertRaises(skin_adapter.SkinAdapterError):
                    skin_adapter.live_skin_setting_values(result)

    def test_generated_skinvariables_fingerprints_are_not_captured(self):
        values = skin_adapter.live_skin_setting_values(self.result([
            {'id': 'script-skinvariables-menu-hash', 'type': 'string', 'value': 'generated'},
            {'id': 'script-skinvariables-user-choice', 'type': 'string', 'value': 'keep'},
        ]))
        self.assertEqual(['script-skinvariables-user-choice'],
                         [item['id'] for item in values])

    def test_sibling_build_fingerprint_without_variables_segment_is_excluded(self):
        # regression guard: script-skinviewtypes-hash (Skin Variables'
        # view-type build fingerprint) reappears in settings.xml
        # immediately after AF3 reactivates during a restore, just like
        # script-skinvariables-images-hash already handled above - but
        # it lacks the "-variables-" segment the original pattern
        # required, so it slipped through and made
        # verify_loaded_settings() see a live/staged mismatch that
        # wasn't a real settings change (2026-09-10).
        self.assertTrue(
            skin_adapter.volatile_skin_setting('script-skinviewtypes-hash'))
        self.assertTrue(skin_adapter.volatile_skin_setting(
            'script-skinvariables-images-hash'))
        self.assertFalse(skin_adapter.volatile_skin_setting(
            'script-skinvariables-user-choice'))
        self.assertFalse(skin_adapter.volatile_skin_setting('home.firstrun'))

    def test_collects_only_af3_managed_helper_json(self):
        skin = skin_adapter.AF3_ID
        managed = {
            'addon_data/script.skinvariables/{}-viewtypes.json'.format(skin): b'{"view":50}',
            'addon_data/script.skinvariables/nodes/{}/main.json'.format(skin): b'{"label":"Home"}',
            'addon_data/script.skinvariables/logins/{}/skinusers.json'.format(skin):
                b'[{"slug":"user-Family"}]',
            'addon_data/script.skinvariables/nodes/{}-user-Family/widgets/main.json'.format(skin):
                b'[{"label":"Family"}]',
        }
        for path, data in managed.items():
            self.write(path, data)
        self.write('addon_data/{}/settings.xml'.format(skin), b'<settings><setting id="stale" /></settings>')
        self.write('addon_data/script.skinvariables/nodes/{}/generated.xml'.format(skin), b'<generated />')
        self.write('addon_data/script.skinvariables/nodes/skin.other/main.json', b'{}')
        self.write('addon_data/unrelated.addon/cache/image.jpg', b'image')

        self.assertEqual(managed, skin_adapter.collect_helper_files(self.profile))

    def test_infers_safe_skin_user_directory_without_declaration(self):
        relative = ('addon_data/script.skinvariables/nodes/'
                    'skin.arctic.fuse.3-user-Guest/main.json')
        self.write(relative, b'{"label":"Guest"}')
        self.assertEqual({relative: b'{"label":"Guest"}'},
                         skin_adapter.collect_helper_files(self.profile))

    def test_managed_source_paths_collapse_helper_roots_and_reject_outputs(self):
        files = {
            'addon_data/skin.arctic.fuse.3/settings.xml': b'<settings />',
            'addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/a.json': b'{}',
            'addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/b.json': b'{}',
            'addon_data/script.skinvariables/logins/skin.arctic.fuse.3/user.json': b'{}',
        }
        self.assertEqual((
            'addon_data/script.skinvariables/logins/skin.arctic.fuse.3',
            'addon_data/script.skinvariables/nodes/skin.arctic.fuse.3',
            'addon_data/skin.arctic.fuse.3/settings.xml',
        ), skin_adapter.managed_source_paths(files))

        with self.assertRaises(skin_adapter.SkinAdapterError):
            skin_adapter.managed_source_paths({
                'addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/generated.xml': b'',
            })

    def test_rejects_invalid_json_and_symlinks(self):
        invalid = self.write(
            'addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/main.json',
            b'not json')
        with self.assertRaises(skin_adapter.SkinAdapterError):
            skin_adapter.collect_helper_files(self.profile)
        invalid.unlink()

        outside = self.write('outside.json', b'{}')
        link = self.profile / ('addon_data/script.skinvariables/nodes/'
                               'skin.arctic.fuse.3/link.json')
        link.parent.mkdir(parents=True, exist_ok=True)
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are unavailable')
        with self.assertRaises(skin_adapter.SkinAdapterError):
            skin_adapter.collect_helper_files(self.profile)

    def test_rejects_file_over_size_limit(self):
        path = self.write(
            'addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/main.json',
            b'{}')
        original_limit = skin_adapter.MAX_FILE_BYTES
        try:
            skin_adapter.MAX_FILE_BYTES = 1
            with self.assertRaises(skin_adapter.SkinAdapterError):
                skin_adapter.collect_helper_files(self.profile)
        finally:
            skin_adapter.MAX_FILE_BYTES = original_limit
        self.assertTrue(path.exists())

    def test_restore_payload_rejects_nested_settings_and_invalid_json(self):
        with self.assertRaises(skin_adapter.SkinAdapterError):
            skin_adapter.validate_snapshot_files({
                'addon_data/skin.arctic.fuse.3/settings.xml':
                    b'<settings><setting id="x"><nested /></setting></settings>',
            })
        with self.assertRaises(skin_adapter.SkinAdapterError):
            skin_adapter.validate_snapshot_files({
                'addon_data/skin.arctic.fuse.3/settings.xml': b'<settings />',
                'addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/main.json': b'NaN',
            })

    def test_capture_uses_live_settings_and_returns_preview_metadata(self):
        helper = ('addon_data/script.skinvariables/nodes/'
                  'skin.arctic.fuse.3/main.json')
        self.write(helper, b'{"label":"Home"}')
        self.write('addon_data/skin.arctic.fuse.3/settings.xml',
                   b'<settings><setting id="stale">disk</setting></settings>')
        result = self.result([
            {'id': 'fresh', 'type': 'string', 'value': 'live'},
            {'id': 'enabled', 'type': 'boolean', 'value': True},
        ])
        calls = []

        def rpc(method):
            calls.append(method)
            return result

        appearance = {
            'lookandfeel.skintheme': 'SKINDEFAULT',
            'lookandfeel.skincolors': 'Dark',
            'lookandfeel.font': 'Default',
            'lookandfeel.skinzoom': 0,
        }
        snapshot = skin_adapter.capture_af3_snapshot(
            self.profile, rpc, source_device='MacBook', source_profile='Master',
            skin_version='3.9.0', helper_version='2.2.1',
            appearance_call=lambda: appearance)
        settings = ElementTree.fromstring(
            snapshot['files']['addon_data/skin.arctic.fuse.3/settings.xml'])

        self.assertEqual(['Settings.GetSkinSettings'] * 2, calls)
        self.assertEqual(['enabled', 'fresh'],
                         [item.get('id') for item in settings.findall('setting')])
        self.assertNotIn(b'stale', ElementTree.tostring(settings))
        self.assertEqual(2, snapshot['metadata']['setting_count'])
        self.assertEqual(1, snapshot['metadata']['helper_file_count'])
        self.assertEqual('MacBook', snapshot['metadata']['source_device'])
        self.assertEqual('Master', snapshot['metadata']['source_profile'])
        self.assertEqual(appearance, snapshot['metadata']['appearance'])
        self.assertEqual(64, len(snapshot['metadata']['fingerprint']))

    def test_capture_rejects_settings_that_change_during_collection(self):
        results = iter((
            self.result([{'id': 'value', 'type': 'string', 'value': 'before'}]),
            self.result([{'id': 'value', 'type': 'string', 'value': 'after'}]),
        ))
        with self.assertRaises(skin_adapter.SkinAdapterError):
            skin_adapter.capture_af3_snapshot(
                self.profile, lambda _method: next(results))

    def test_capture_rejects_helpers_that_change_across_quiet_period(self):
        snapshots = ({'one.json': b'one'}, {'one.json': b'two'})
        settled = []
        with mock.patch.object(
                skin_adapter, 'collect_helper_files', side_effect=snapshots):
            with self.assertRaises(skin_adapter.SkinAdapterError):
                skin_adapter.capture_af3_snapshot(
                    self.profile, lambda _method: self.result(),
                    settle=lambda: settled.append(True))
        self.assertEqual([True], settled)

    def test_capture_rejects_changed_or_invalid_appearance(self):
        appearances = iter((
            {'lookandfeel.font': 'Default'},
            {'lookandfeel.font': 'Large'},
        ))
        with self.assertRaises(skin_adapter.SkinAdapterError):
            skin_adapter.capture_af3_snapshot(
                self.profile, lambda _method: self.result(),
                appearance_call=lambda: next(appearances))

        for invalid in (
                {'unknown.setting': 'value'},
                {'lookandfeel.font': None},
                {'lookandfeel.skinzoom': True},
                {'lookandfeel.skinzoom': float('nan')}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(skin_adapter.SkinAdapterError):
                    skin_adapter.checked_appearance(invalid)


if __name__ == '__main__':
    unittest.main()
