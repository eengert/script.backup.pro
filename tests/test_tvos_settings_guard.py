from __future__ import unicode_literals

import json
import sys
import unittest
from unittest import mock

from tests.test_backup_bridge import install_kodi_stubs


install_kodi_stubs()

from resources.lib import tvos_settings_guard as guard_module  # noqa: E402
from resources.lib import utils  # noqa: E402


class FakeHost:
    def __init__(self, marker=None, version='0.9.23', pid=100,
                 tvos=True):
        self.marker = marker
        self.version_value = version
        self.pid_value = pid
        self.tvos = tvos
        self.properties = {}
        self.settings_value = 'settings-a'
        self.inventory_value = 'inventory-a'
        self.marker_writes = []
        self.logs = []
        self.now = 0.0
        self.fail_marker_write = False

    def is_tvos(self):
        return self.tvos

    def pid(self):
        return self.pid_value

    def version(self):
        return self.version_value

    def read_marker(self):
        return self.marker

    def write_marker(self, marker):
        if self.fail_marker_write:
            raise OSError('read-only profile')
        self.marker = dict(marker)
        self.marker_writes.append(dict(marker))

    def property_get(self, name):
        return self.properties.get(name, '')

    def property_set(self, name, value):
        self.properties[name] = value

    def property_clear(self, name):
        self.properties.pop(name, None)

    def settings_signature(self):
        return self.settings_value

    def inventory_signature(self):
        return self.inventory_value

    def log(self, message, level=None):
        self.logs.append((message, level))

    def monotonic(self):
        return self.now


def marker(version='0.9.23', pid=100):
    return {'schema': 1, 'version': version, 'pid': pid}


class SessionMarkerTests(unittest.TestCase):
    def test_first_marker_aware_run_requires_one_restart(self):
        host = FakeHost(marker=None)
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertFalse(guard.initialize())
        self.assertTrue(guard.is_unsafe())
        self.assertEqual([marker()], host.marker_writes)
        self.assertEqual('bootstrap', host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])

    def test_live_update_in_same_pid_is_unsafe(self):
        host = FakeHost(marker=marker(version='0.9.22'))
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertFalse(guard.initialize())
        self.assertEqual('live_update', host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])
        self.assertEqual('0.9.23', host.marker_writes[-1]['version'])

    def test_pid_change_clears_state_and_establishes_safe_baseline(self):
        host = FakeHost(marker=marker(pid=99))
        host.properties.update({
            guard_module.SETTINGS_SIGNATURE_PROPERTY: 'old-settings',
            guard_module.INVENTORY_SIGNATURE_PROPERTY: 'old-inventory',
        })
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertTrue(guard.initialize())
        self.assertFalse(guard.is_unsafe())
        self.assertEqual('settings-a', host.properties[
            guard_module.SETTINGS_SIGNATURE_PROPERTY])
        self.assertEqual('inventory-a', host.properties[
            guard_module.INVENTORY_SIGNATURE_PROPERTY])

    def test_same_version_same_pid_is_allowed(self):
        host = FakeHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertTrue(guard.allow_operation())
        self.assertFalse(guard.is_unsafe())

    def test_non_tvos_does_not_read_or_write_marker(self):
        host = FakeHost(marker=None, tvos=False)
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertTrue(guard.allow_operation())
        self.assertEqual([], host.marker_writes)
        self.assertEqual({}, host.properties)

    def test_marker_write_failure_fails_closed(self):
        host = FakeHost(marker=None)
        host.fail_marker_write = True
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertFalse(guard.initialize())
        self.assertEqual('initialization_failed', host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])

    def test_marker_contains_only_approved_non_sensitive_fields(self):
        host = FakeHost(marker=None)
        guard_module.TvOSSettingsGuard(host).initialize()

        written = host.marker_writes[-1]
        self.assertEqual({'schema', 'version', 'pid'}, set(written))
        document = json.dumps(written)
        for forbidden in ('remote_path', 'dropbox', 'password', 'smb://'):
            self.assertNotIn(forbidden, document)


class RuntimeDetectionTests(unittest.TestCase):
    def setUp(self):
        self.host = FakeHost(marker=marker())
        self.guard = guard_module.TvOSSettingsGuard(
            self.host, poll_interval=1.0)
        self.assertTrue(self.guard.initialize())

    def test_unrelated_addon_inventory_change_is_unsafe(self):
        self.host.inventory_value = 'inventory-after-install'

        self.assertFalse(self.guard.poll(force=True))
        self.assertEqual('addon_inventory_changed', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])

    def test_same_final_inventory_stale_settings_view_is_unsafe(self):
        # Models uninstall/reinstall completing with the same final inventory:
        # the known-good settings signature remains an independent detector.
        self.host.settings_value = 'stale-default-view'

        self.assertFalse(self.guard.poll(force=True))
        self.assertEqual('settings_view_changed', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])

    def test_legitimate_settings_change_rebaselines(self):
        self.host.settings_value = 'settings-b'

        self.assertTrue(self.guard.rebaseline_settings())
        self.assertTrue(self.guard.poll(force=True))
        self.assertFalse(self.guard.is_unsafe())

    def test_authorized_settings_dialog_change_is_not_false_alarm(self):
        self.guard.begin_settings_edit()
        self.host.settings_value = 'settings-b'
        self.assertTrue(self.guard.poll(force=True))
        self.guard.finish_settings_edit()

        self.assertTrue(self.guard.poll(force=True))
        self.assertEqual('settings-b', self.host.properties[
            guard_module.SETTINGS_SIGNATURE_PROPERTY])

    def test_poll_is_throttled(self):
        self.assertTrue(self.guard.poll(force=True))
        self.host.inventory_value = 'changed'
        self.host.now = 0.5
        self.assertTrue(self.guard.poll())
        self.host.now = 1.0
        self.assertFalse(self.guard.poll())

    def test_safety_check_failure_fails_closed(self):
        self.host.inventory_signature = mock.Mock(
            side_effect=RuntimeError('rpc unavailable'))

        self.assertFalse(self.guard.poll(force=True))
        self.assertEqual('safety_check_failed', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])


class SignaturePrivacyTests(unittest.TestCase):
    class Addon:
        def __init__(self, values):
            self.values = values

        def getSettingBool(self, name):
            return self.values.get(name, False)

        def getSettingInt(self, name):
            return self.values.get(name, 0)

        def getSetting(self, name):
            return self.values.get(name, '')

    def test_sensitive_values_are_not_present_in_signature(self):
        secret = 'smb://user:password@server/private'
        addon = self.Addon({
            'remote_path': secret,
            'remote_path_2': secret,
            'dropbox_key': 'private-key',
            'dropbox_secret': 'private-secret',
        })

        signature = guard_module.normalized_settings_signature(addon)

        self.assertEqual(64, len(signature))
        for forbidden in (secret, 'private-key', 'private-secret'):
            self.assertNotIn(forbidden, signature)

    def test_path_contents_do_not_change_signature_when_presence_matches(self):
        first = self.Addon({'remote_path': '/one/private/path'})
        second = self.Addon({'remote_path': 'smb://different/private/path'})

        self.assertEqual(
            guard_module.normalized_settings_signature(first),
            guard_module.normalized_settings_signature(second))

    def test_inventory_is_stable_across_enumeration_order(self):
        first = json.dumps({'result': {'addons': [
            {'addonid': 'b', 'version': '1', 'enabled': True,
             'installed': True},
            {'addonid': 'a', 'version': '2', 'enabled': False,
             'installed': True},
        ]}})
        second = json.dumps({'result': {'addons': list(reversed(
            json.loads(first)['result']['addons']))}})

        self.assertEqual(
            guard_module.normalized_inventory_signature(first),
            guard_module.normalized_inventory_signature(second))

    def test_inventory_detects_version_enabled_and_install_changes(self):
        base = {'addonid': 'example', 'version': '1', 'enabled': True,
                'installed': True}
        baseline = guard_module.normalized_inventory_signature(json.dumps({
            'result': {'addons': [base]}}))
        for field, value in (
                ('version', '2'), ('enabled', False), ('installed', False)):
            changed = dict(base)
            changed[field] = value
            with self.subTest(field=field):
                self.assertNotEqual(
                    baseline,
                    guard_module.normalized_inventory_signature(json.dumps({
                        'result': {'addons': [changed]}})))


class FreshAddonWrapperTests(unittest.TestCase):
    def test_setting_reads_do_not_reuse_one_addon_wrapper(self):
        created = []

        class Addon:
            def __init__(self, addon_id):
                created.append(addon_id)

            def getSetting(self, _name):
                return str(len(created))

        with mock.patch.object(utils.xbmcaddon, 'Addon', Addon):
            first = utils.getSetting('remote_selection')
            second = utils.getSetting('remote_selection')

        self.assertEqual(['script.backup.pro'] * 2, created)
        self.assertEqual(('1', '2'), (first, second))

    def test_guard_never_calls_set_setting(self):
        host = FakeHost(marker=marker())
        host.set_setting = mock.Mock(side_effect=AssertionError(
            'settings must never be rewritten'))

        self.assertTrue(
            guard_module.TvOSSettingsGuard(host).allow_operation())
        host.set_setting.assert_not_called()


if __name__ == '__main__':
    unittest.main()
