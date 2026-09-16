from __future__ import unicode_literals

import json
import sys
import unittest
from dataclasses import dataclass
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


class FakeObservationHost(FakeHost):
    """Host double exposing the safe, field-name-only diagnostic state."""
    def __init__(self, *args, **kwargs):
        super(FakeObservationHost, self).__init__(*args, **kwargs)
        self.diagnostic_value = (
            ('backup_database', False),
            ('backup_thumbnails', False),
            ('remote_path_configured', True),
        )

    def settings_observation(self):
        return self.settings_value, self.diagnostic_value


def marker(version='0.9.23', pid=100):
    return {'schema': 1, 'version': version, 'pid': pid}


@dataclass(frozen=True)
class FakeSnapshot:
    value: str
    safety_signature: str


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
        self.assertEqual('ready', host.properties[
            guard_module.SAFETY_READY_PROPERTY])

    def test_same_version_same_pid_is_allowed(self):
        host = FakeHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertTrue(guard.allow_operation())
        self.assertFalse(guard.is_unsafe())

    def test_late_service_bootstrap_adopts_program_baseline(self):
        # Program can establish a valid same-process baseline before Kodi
        # starts the long-running service. A late service marker read must
        # not turn that already safe session into a false bootstrap block.
        host = FakeHost(marker=None)
        host.properties.update({
            guard_module.SETTINGS_SIGNATURE_PROPERTY: 'program-settings',
            guard_module.INVENTORY_SIGNATURE_PROPERTY: 'program-inventory',
        })
        host.settings_value = 'program-settings'
        host.inventory_value = 'program-inventory'
        guard = guard_module.TvOSSettingsGuard(
            host, service_initialization=True)

        self.assertTrue(guard.initialize())
        self.assertFalse(guard.is_unsafe())
        self.assertEqual('ready', host.properties[
            guard_module.SAFETY_READY_PROPERTY])
        self.assertEqual([marker()], host.marker_writes)
        self.assertTrue(guard.allow_operation('manual_backup_preplan'))
        self.assertIn(
            ('late service initialization adopted existing process baseline',
             None), host.logs)

    def test_operation_waits_while_service_advertises_initialization(self):
        host = FakeHost(marker=marker())
        host.properties[guard_module.SAFETY_READY_PROPERTY] = 'initializing'
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertFalse(guard.allow_operation('manual_backup_preplan'))
        self.assertFalse(guard.is_unsafe())
        self.assertIn(
            ('operation guard: action=manual_backup_preplan '
             'result=restart_required reason=service_initializing '
             'unsafe_before=false comparison=skipped', None), host.logs)

    def test_service_only_advertises_initializing_during_startup(self):
        host = FakeHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(
            host, service_initialization=True)

        self.assertTrue(guard.initialize())
        self.assertEqual('ready', host.properties[
            guard_module.SAFETY_READY_PROPERTY])
        self.assertTrue(guard.poll(force=True))
        self.assertEqual('ready', host.properties[
            guard_module.SAFETY_READY_PROPERTY])

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

    def test_unrelated_addon_inventory_change_with_consistent_settings_is_safe(self):
        self.host.inventory_value = 'inventory-after-install'

        self.assertTrue(self.guard.poll(force=True))
        self.assertFalse(self.guard.is_unsafe())
        self.assertEqual('inventory-after-install', self.host.properties[
            guard_module.INVENTORY_SIGNATURE_PROPERTY])
        self.assertIn(
            ('addon inventory changed; revalidating settings', None),
            self.host.logs)

    def test_unrelated_addon_inventory_change_with_stale_settings_is_unsafe(self):
        self.host.inventory_value = 'inventory-after-install'
        self.host.settings_value = 'stale-default-view'

        self.assertFalse(self.guard.poll(force=True))
        self.assertEqual('settings_view_changed', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])

    def test_same_final_inventory_stale_settings_view_is_unsafe(self):
        # Models uninstall/reinstall completing with the same final inventory:
        # the known-good settings signature remains an independent detector.
        self.host.settings_value = 'stale-default-view'

        self.assertFalse(self.guard.poll(force=True))
        self.assertEqual('settings_view_changed', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])

    def test_stale_view_logs_changed_non_sensitive_field_names(self):
        host = FakeObservationHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
        self.assertTrue(guard.initialize())
        host.settings_value = 'stale-default-view'
        host.diagnostic_value = (
            ('backup_database', True),
            ('backup_thumbnails', True),
            ('remote_path_configured', True),
        )

        self.assertFalse(guard.poll(force=True))
        messages = [message for message, _level in host.logs]
        self.assertIn(
            'settings signature fields changed: backup_database,backup_thumbnails',
            messages)
        self.assertNotIn('smb://private/path', '\n'.join(messages))
        self.assertNotIn('secret', '\n'.join(messages))

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
        self.assertTrue(self.guard.poll())
        self.assertEqual('changed', self.host.properties[
            guard_module.INVENTORY_SIGNATURE_PROPERTY])

    def test_normal_backup_failure_does_not_poison_unchanged_session(self):
        # Backup results are not guard inputs.  A later operation with the
        # same fresh settings and inventory remains allowed. In particular,
        # backup construction must not rewrite a destination setting and
        # thereby make this otherwise unchanged signature unsafe.
        self.assertTrue(self.guard.allow_operation())
        self.assertTrue(self.guard.allow_operation())
        self.assertFalse(self.guard.is_unsafe())

    def test_allowed_operation_logs_named_decision(self):
        self.assertTrue(self.guard.allow_operation('manual_backup'))

        self.assertIn(
            ('operation guard: action=manual_backup result=allowed '
             'reason=allowed unsafe_before=false comparison=performed', None),
            self.host.logs)

    def test_existing_unsafe_property_logs_its_reason(self):
        self.host.properties[guard_module.UNSAFE_PROPERTY] = '1'
        self.host.properties[guard_module.UNSAFE_REASON_PROPERTY] = (
            'settings_view_changed')

        self.assertFalse(self.guard.allow_operation('restore'))
        self.assertIn(
            ('operation guard: action=restore result=restart_required '
             'reason=existing_unsafe_property unsafe_before=true '
             'comparison=skipped existing_reason=settings_view_changed', None),
            self.host.logs)

    def test_bootstrap_restart_required_logs_named_reason(self):
        host = FakeHost(marker=None)
        guard = guard_module.TvOSSettingsGuard(host)

        self.assertFalse(guard.allow_operation('program_open'))
        self.assertIn(
            ('operation guard: action=program_open result=restart_required '
             'reason=bootstrap unsafe_before=false comparison=skipped', None),
            host.logs)

    def test_operation_boundary_logs_fresh_selection_and_baseline_difference(self):
        host = FakeObservationHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
        self.assertTrue(guard.initialize())
        host.diagnostic_value = (
            ('backup_database', True),
            ('backup_thumbnails', True),
            ('remote_path_configured', True),
        )

        guard.log_operation_boundary('manual_backup')

        messages = '\n'.join(message for message, _level in host.logs)
        self.assertIn('action=manual_backup', messages)
        self.assertIn('selection=backup_addons=false', messages)
        self.assertIn('backup_database=true', messages)
        self.assertIn('backup_thumbnails=true', messages)
        self.assertIn('changes=backup_database:false->true,backup_thumbnails:false->true',
                      messages)
        for forbidden in ('smb://', 'password', 'secret', 'private'):
            self.assertNotIn(forbidden, messages)

    def test_safety_check_failure_fails_closed(self):
        self.host.inventory_signature = mock.Mock(
            side_effect=RuntimeError('rpc unavailable'))

        self.assertFalse(self.guard.poll(force=True))
        self.assertEqual('safety_check_failed', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])

    def test_snapshot_admission_requires_two_equal_complete_captures(self):
        snapshot = FakeSnapshot('stable', 'settings-a')

        self.assertEqual(
            snapshot, self.guard.admit_backup_snapshot(
                mock.Mock(side_effect=(snapshot, snapshot))))
        self.assertFalse(self.guard.is_unsafe())

    def test_snapshot_admission_rejects_inconsistent_captures(self):
        self.assertIsNone(self.guard.admit_backup_snapshot(mock.Mock(
            side_effect=(FakeSnapshot('one', 'settings-a'),
                         FakeSnapshot('two', 'settings-a')))))
        self.assertEqual('snapshot_capture_inconsistent', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])

    def test_snapshot_admission_rejects_baseline_mismatch_without_values(self):
        self.assertIsNone(self.guard.admit_backup_snapshot(mock.Mock(
            return_value=FakeSnapshot('private-value', 'different'))))
        self.assertEqual('settings_view_changed', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])
        messages = '\n'.join(message for message, _level in self.host.logs)
        self.assertNotIn('private-value', messages)

    def test_snapshot_admission_rejects_ready_state_change_after_capture(self):
        snapshot = FakeSnapshot('stable', 'settings-a')

        def capture():
            if not hasattr(capture, 'called'):
                capture.called = True
                return snapshot
            self.host.properties[guard_module.SAFETY_READY_PROPERTY] = (
                'initializing')
            return snapshot

        self.assertIsNone(self.guard.admit_backup_snapshot(capture))
        self.assertFalse(self.guard.is_unsafe())

    def test_admitted_snapshot_continues_after_later_settings_view_change(self):
        snapshot = FakeSnapshot('stable', 'settings-a')
        self.assertEqual(
            snapshot, self.guard.admit_backup_snapshot(
                mock.Mock(side_effect=(snapshot, snapshot))))

        self.host.settings_value = 'stale-default-view'
        self.assertFalse(self.guard.poll(force=True))
        self.assertTrue(self.guard.is_unsafe())
        self.assertEqual('settings_view_changed', self.host.properties[
            guard_module.UNSAFE_REASON_PROPERTY])
        self.assertFalse(self.guard.operation_revoked(admitted_snapshot=True))
        self.assertTrue(self.guard.operation_revoked())
        self.assertFalse(self.guard.allow_operation('manual_backup'))
        self.assertFalse(self.guard.allow_operation('restore'))

    def test_admitted_snapshot_still_stops_for_lifecycle_or_readiness_risks(self):
        for reason in ('live_update', 'bootstrap', 'invalid',
                       'safety_check_failed'):
            with self.subTest(reason=reason):
                self.host.properties[guard_module.UNSAFE_PROPERTY] = '1'
                self.host.properties[guard_module.UNSAFE_REASON_PROPERTY] = reason
                self.assertTrue(
                    self.guard.operation_revoked(admitted_snapshot=True))
                self.host.properties.clear()
        self.host.properties[guard_module.SAFETY_READY_PROPERTY] = 'initializing'
        self.assertTrue(self.guard.operation_revoked(admitted_snapshot=True))


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

    def test_destination_presence_change_changes_signature(self):
        # A Backup Pro operation used to clear remote_path while using the
        # secondary destination. That write alone changes the guard's
        # non-sensitive signature and was the Example Room false trigger.
        configured = self.Addon({'remote_path': 'smb://configured/path'})
        cleared = self.Addon({'remote_path': ''})

        self.assertNotEqual(
            guard_module.normalized_settings_signature(configured),
            guard_module.normalized_settings_signature(cleared))

    def test_simple_selection_diagnostic_excludes_sensitive_values(self):
        addon = self.Addon({
            'backup_database': True,
            'backup_thumbnails': False,
            'remote_path': 'smb://user:password@server/private',
            'dropbox_secret': 'private-secret',
        })

        state = guard_module.simple_selection_state(
            guard_module.diagnostic_settings_state(addon))
        document = json.dumps(state)
        self.assertIn('backup_database', document)
        self.assertIn('backup_thumbnails', document)
        for forbidden in ('remote_path', 'password', 'private-secret'):
            self.assertNotIn(forbidden, document)

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


DEFAULT_CRITICAL_BASELINE = {'remote_selection': 'baseline-remote-selection',
                            'backup_addons': 'baseline-backup-addons'}


class FakeCriticalBaselineHost(FakeObservationHost):
    """Host double additionally exposing the trusted scheduler baseline."""
    def __init__(self, *args, **kwargs):
        super(FakeCriticalBaselineHost, self).__init__(*args, **kwargs)
        self.critical_baseline_value = dict(DEFAULT_CRITICAL_BASELINE)

    def critical_scheduler_baseline(self):
        return dict(self.critical_baseline_value)


@dataclass(frozen=True)
class FakeSchedulerCapture:
    value: str
    destination_valid: bool = True
    critical_baseline: dict = None

    def destination_state_valid(self):
        return self.destination_valid

    def critical_scheduler_baseline(self):
        return dict(self.critical_baseline or DEFAULT_CRITICAL_BASELINE)


def _live_update_guard(host=None):
    host = host or FakeCriticalBaselineHost(marker=marker())
    guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
    guard.initialize()
    host.properties[guard_module.UNSAFE_PROPERTY] = '1'
    host.properties[guard_module.UNSAFE_REASON_PROPERTY] = 'live_update'
    host.properties[guard_module.SAFETY_READY_PROPERTY] = 'unsafe'
    return guard, host


class SchedulerRecoveryTests(unittest.TestCase):
    """admit_scheduler_recovery_snapshot() - the scheduler-only recovery
    path for a `live_update` unsafe reason. See
    docs/TVOS_LIVE_UPDATE_SCHEDULER_RECOVERY_REVIEW.md for the design.
    """

    def test_matching_captures_valid_destination_and_baseline_match_admit(self):
        guard, host = _live_update_guard()
        capture = FakeSchedulerCapture('stable')

        result = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))

        self.assertEqual(capture, result)
        self.assertTrue(guard.is_unsafe())
        self.assertEqual(
            'live_update',
            host.properties[guard_module.UNSAFE_REASON_PROPERTY])
        self.assertTrue(guard.recovery_attempt_was_first())

    def test_recovery_never_clears_the_global_unsafe_state(self):
        guard, host = _live_update_guard()
        capture = FakeSchedulerCapture('stable')

        guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))

        self.assertEqual('1', host.properties[guard_module.UNSAFE_PROPERTY])
        self.assertEqual(
            'unsafe', host.properties[guard_module.SAFETY_READY_PROPERTY])
        self.assertFalse(guard.allow_operation('manual_backup'))
        self.assertFalse(guard.allow_operation('program_open'))

    def test_mismatched_double_capture_blocks(self):
        guard, host = _live_update_guard()

        result = guard.admit_scheduler_recovery_snapshot(mock.Mock(
            side_effect=(FakeSchedulerCapture('one'),
                         FakeSchedulerCapture('two'))))

        self.assertIsNone(result)
        self.assertEqual(
            'recovery_capture_inconsistent', guard.last_block_reason())

    def test_capture_exception_blocks(self):
        guard, host = _live_update_guard()

        result = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=RuntimeError('addon unavailable')))

        self.assertIsNone(result)
        self.assertEqual(
            'recovery_capture_failed', guard.last_block_reason())

    def test_invalid_destination_blocks(self):
        guard, host = _live_update_guard()
        capture = FakeSchedulerCapture('stable', destination_valid=False)

        result = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))

        self.assertIsNone(result)
        self.assertEqual(
            'recovery_destination_invalid', guard.last_block_reason())

    def test_missing_trusted_baseline_blocks(self):
        host = FakeHost(marker=marker())  # no critical_scheduler_baseline
        guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
        guard.initialize()
        host.properties[guard_module.UNSAFE_PROPERTY] = '1'
        host.properties[guard_module.UNSAFE_REASON_PROPERTY] = 'live_update'
        capture = FakeSchedulerCapture('stable')

        result = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))

        self.assertIsNone(result)
        self.assertEqual(
            'recovery_no_trusted_baseline', guard.last_block_reason())

    def test_critical_field_unknown_to_baseline_fails_closed(self):
        """Schema evolution: the running (post-update) code recognizes a
        critical field the pre-update trusted baseline never captured -
        must fail closed, not silently skip it.
        """
        guard, host = _live_update_guard()
        capture = FakeSchedulerCapture(
            'stable', critical_baseline=dict(
                DEFAULT_CRITICAL_BASELINE, brand_new_critical_field='x'))

        result = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))

        self.assertIsNone(result)
        self.assertEqual(
            'recovery_field_unvalidated', guard.last_block_reason())

    def test_stable_but_wrong_capture_fails_baseline_comparison(self):
        """Two fresh captures agreeing with EACH OTHER is not sufficient:
        Kodi/tvOS can return a stable but stale/default view. Only a
        mismatch against the separately-trusted pre-update baseline
        catches this - this is the core design requirement this whole
        recovery path exists to satisfy.
        """
        guard, host = _live_update_guard()
        stably_wrong = FakeSchedulerCapture(
            'stable', critical_baseline={
                'remote_selection': 'DEFAULT-remote-selection',
                'backup_addons': 'DEFAULT-backup-addons'})

        result = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(stably_wrong, stably_wrong)))

        self.assertIsNone(result)
        self.assertEqual(
            'recovery_baseline_mismatch', guard.last_block_reason())

    def test_baseline_mismatch_log_names_fields_never_values(self):
        guard, host = _live_update_guard()
        stably_wrong = FakeSchedulerCapture(
            'stable', critical_baseline={
                'remote_selection': 'DEFAULT-remote-selection',
                'backup_addons': 'baseline-backup-addons'})

        guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(stably_wrong, stably_wrong)))

        messages = '\n'.join(message for message, _level in host.logs)
        self.assertIn('remote_selection', messages)
        self.assertNotIn('DEFAULT-remote-selection', messages)
        self.assertNotIn('baseline-remote-selection', messages)

    def test_only_live_update_reason_is_eligible(self):
        for reason in ('bootstrap', 'invalid', 'initialization_failed',
                       'safety_check_failed', 'settings_view_changed',
                       'settings_rebaseline_failed',
                       'snapshot_capture_failed'):
            with self.subTest(reason=reason):
                host = FakeCriticalBaselineHost(marker=marker())
                guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
                guard.initialize()
                host.properties[guard_module.UNSAFE_PROPERTY] = '1'
                host.properties[guard_module.UNSAFE_REASON_PROPERTY] = reason
                capture = FakeSchedulerCapture('stable')

                result = guard.admit_scheduler_recovery_snapshot(
                    mock.Mock(side_effect=(capture, capture)))

                self.assertIsNone(result)
                self.assertEqual(
                    'unsafe_reason_not_recoverable', guard.last_block_reason())

    def test_service_initializing_blocks_before_checking_reason(self):
        guard, host = _live_update_guard()
        host.properties[guard_module.SAFETY_READY_PROPERTY] = 'initializing'
        capture = FakeSchedulerCapture('stable')

        result = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))

        self.assertIsNone(result)
        self.assertEqual('service_initializing', guard.last_block_reason())

    def test_safe_session_has_nothing_to_recover(self):
        host = FakeCriticalBaselineHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
        guard.initialize()
        capture = FakeSchedulerCapture('stable')

        result = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))

        self.assertIsNone(result)
        self.assertEqual('not_unsafe', guard.last_block_reason())

    def test_repeated_attempts_are_throttled_by_a_cooldown(self):
        guard, host = _live_update_guard()
        capture = FakeSchedulerCapture('stable')

        first = guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))
        self.assertEqual(capture, first)
        self.assertTrue(guard.recovery_attempt_was_first())

        # A second attempt inside the cooldown window must not even invoke
        # the capture callable again - avoids repeated expensive work (and
        # repeated logging) on every 500ms scheduler tick while blocked.
        second_capture_fn = mock.Mock()
        second = guard.admit_scheduler_recovery_snapshot(second_capture_fn)

        self.assertIsNone(second)
        self.assertEqual('recovery_cooldown', guard.last_block_reason())
        self.assertFalse(guard.recovery_attempt_was_first())
        second_capture_fn.assert_not_called()

    def test_recovery_ready_reports_cooldown_state_without_attempting(self):
        guard, host = _live_update_guard()
        capture = FakeSchedulerCapture('stable')
        self.assertTrue(guard.scheduler_recovery_ready())

        guard.admit_scheduler_recovery_snapshot(
            mock.Mock(side_effect=(capture, capture)))

        self.assertFalse(guard.scheduler_recovery_ready())
        host.now += guard_module.RECOVERY_COOLDOWN_SECONDS
        self.assertTrue(guard.scheduler_recovery_ready())

    def test_recovered_snapshot_tolerates_sticky_live_update_reason(self):
        guard, host = _live_update_guard()

        self.assertFalse(guard.operation_revoked(
            admitted_snapshot=True, recovered_from_live_update=True))

    def test_recovered_snapshot_still_stops_for_other_reasons(self):
        # settings_view_changed is deliberately excluded here: an admitted
        # snapshot (recovered or not) already tolerates that SAME reason
        # per the pre-existing admitted_snapshot policy (see
        # test_admitted_snapshot_continues_after_later_settings_view_change
        # above) - Task D of the scheduler-recovery work keeps that
        # unchanged rather than narrowing it for recovered operations.
        for reason in ('bootstrap', 'invalid', 'safety_check_failed'):
            with self.subTest(reason=reason):
                host = FakeHost(marker=marker())
                guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
                guard.initialize()
                host.properties[guard_module.UNSAFE_PROPERTY] = '1'
                host.properties[guard_module.UNSAFE_REASON_PROPERTY] = reason
                self.assertTrue(guard.operation_revoked(
                    admitted_snapshot=True, recovered_from_live_update=True))

    def test_unsafe_reason_reports_none_when_safe_or_inactive(self):
        host = FakeCriticalBaselineHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
        guard.initialize()
        self.assertIsNone(guard.unsafe_reason())

        non_tvos_host = FakeCriticalBaselineHost(marker=marker(), tvos=False)
        non_tvos_guard = guard_module.TvOSSettingsGuard(non_tvos_host)
        self.assertIsNone(non_tvos_guard.unsafe_reason())

    def test_unsafe_reason_reports_the_sticky_reason(self):
        guard, host = _live_update_guard()
        self.assertEqual('live_update', guard.unsafe_reason())

    def test_critical_baseline_established_alongside_other_baselines(self):
        host = FakeCriticalBaselineHost(marker=marker())
        guard_module.TvOSSettingsGuard(host, poll_interval=1.0).initialize()

        self.assertEqual(
            json.dumps(DEFAULT_CRITICAL_BASELINE, sort_keys=True,
                       separators=(',', ':')),
            host.properties[guard_module.CRITICAL_BASELINE_PROPERTY])

    def test_legitimate_settings_edit_refreshes_the_critical_baseline(self):
        host = FakeCriticalBaselineHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)
        guard.initialize()
        host.critical_baseline_value = {'remote_selection': 'new-value'}

        self.assertTrue(guard.rebaseline_settings())

        self.assertEqual(
            json.dumps({'remote_selection': 'new-value'}, sort_keys=True,
                       separators=(',', ':')),
            host.properties[guard_module.CRITICAL_BASELINE_PROPERTY])

    def test_host_without_critical_baseline_support_degrades_gracefully(self):
        # Older/simpler test doubles (plain FakeHost) must not raise - the
        # absence of critical-baseline support simply means scheduler
        # recovery will fail closed later (no trusted baseline), not that
        # ordinary initialize()/allow_operation() breaks.
        host = FakeHost(marker=marker())
        guard = guard_module.TvOSSettingsGuard(host, poll_interval=1.0)

        self.assertTrue(guard.initialize())
        self.assertNotIn(
            guard_module.CRITICAL_BASELINE_PROPERTY, host.properties)


class CriticalSchedulerBaselineDigestTests(unittest.TestCase):
    def test_only_named_critical_fields_are_represented(self):
        bool_values = {name: False for name in guard_module._BOOL_SETTINGS}
        int_values = {name: 0 for name in guard_module._INT_SETTINGS}
        string_values = {name: '' for name in guard_module._STRING_SETTINGS}
        presence_values = {name: False
                           for name in guard_module._PRESENCE_SETTINGS}

        baseline = guard_module.critical_scheduler_baseline_from_values(
            bool_values, int_values, string_values, presence_values)

        expected_keys = (set(guard_module._CRITICAL_BOOL_SETTINGS)
                         | set(guard_module._CRITICAL_INT_SETTINGS)
                         | set(guard_module._STRING_SETTINGS)
                         | set(guard_module._PRESENCE_SETTINGS))
        self.assertEqual(expected_keys, set(baseline))
        # A non-critical bool setting (e.g. always_prompt_restore_settings,
        # verbose_logging, progress_mode) must never appear - a legitimate
        # schema change to one of those must never affect this baseline.
        self.assertNotIn('always_prompt_restore_settings', baseline)
        self.assertNotIn('progress_mode', baseline)

    def test_free_form_string_values_are_digested_not_stored(self):
        bool_values = {name: False for name in guard_module._BOOL_SETTINGS}
        int_values = {name: 0 for name in guard_module._INT_SETTINGS}
        string_values = dict.fromkeys(
            guard_module._STRING_SETTINGS, 'MyPersonalSuffixText')
        presence_values = {name: False
                           for name in guard_module._PRESENCE_SETTINGS}

        baseline = guard_module.critical_scheduler_baseline_from_values(
            bool_values, int_values, string_values, presence_values)

        self.assertNotIn('MyPersonalSuffixText', json.dumps(baseline))


if __name__ == '__main__':
    unittest.main()
