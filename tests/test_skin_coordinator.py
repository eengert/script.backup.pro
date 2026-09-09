from __future__ import unicode_literals

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from resources.lib import skin_coordinator, skin_state, skin_transaction
from resources.lib.skin_adapter import AF3_ID
from resources.lib.skin_restore import (
    SETTINGS_PATH,
    advance_pending_restore,
    build_pending_restore,
)
from tests.test_skin_restore import fixture


class FakeHost:
    def __init__(self, fail=None):
        self.fail = fail
        self.events = []
        self.playing = True
        self.skin = skin_coordinator.AF3_ID

    def _event(self, name, *values):
        self.events.append((name,) + values)
        if self.fail == name:
            raise RuntimeError(name + ' failed')

    def ensure_dependencies(self, skin, helper):
        self._event('dependencies', skin, helper)

    def stop_playback(self):
        self._event('stop_playback')
        self.playing = False

    def is_playing(self):
        self._event('is_playing')
        return self.playing

    def ensure_inactive(self, skin):
        self._event('ensure_inactive', skin)
        self.skin = 'skin.estuary'

    def active_skin(self):
        self._event('active_skin')
        return self.skin

    def stage_settings(self, skin, document, values):
        if self.skin == skin:
            raise AssertionError('settings staged while target skin was active')
        self._event('stage_settings', skin, document, values)

    def capture_appearance(self):
        values = {'lookandfeel.skincolors': 'Previous'}
        self._event('capture_appearance', values)
        return values

    def activate_skin(self, skin):
        self._event('activate_skin', skin)
        self.skin = skin

    def verify_loaded_settings(self, skin, values):
        if self.skin != skin:
            raise AssertionError('verified settings for an inactive skin')
        self._event('verify_loaded_settings', skin, values)

    def apply_appearance(self, values):
        self._event('apply_appearance', values)

    def rebuild_skin(self, skin, pending):
        if self.skin != skin:
            raise AssertionError('rebuilt an inactive skin')
        self._event('rebuild_skin', skin, pending)

    def progress(self, percent, message):
        self.events.append(('progress', percent, message))


class SkinCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.profile = self.root / 'profile'
        self.profile.mkdir()
        self.rollback = self.root / 'rollback'
        self.state = self.root / 'addon-data' / 'pending.json'
        self.state.parent.mkdir()
        self.manifest, self.files = fixture()

    def stage(self, host=None):
        return skin_coordinator.stage_skin_restore(
            self.manifest, self.files, '20260908120000.zip',
            self.profile, self.rollback, self.state, host or FakeHost())

    def test_success_orders_inactive_transaction_vfs_and_rebuild_state(self):
        host = FakeHost()
        pending = self.stage(host)

        names = [event[0] for event in host.events]
        self.assertLess(names.index('stop_playback'),
                        names.index('ensure_inactive'))
        self.assertLess(names.index('ensure_inactive'),
                        names.index('stage_settings'))
        self.assertEqual('rebuild', pending['phase'])
        self.assertEqual(pending, skin_state.read_pending_state(self.state))
        self.assertEqual('complete', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))
        self.assertEqual(self.files[SETTINGS_PATH],
                         (self.profile / SETTINGS_PATH).read_bytes())
        self.assertEqual('finish_rebuild',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])

    def test_stage_binds_exact_previous_files_and_appearance_for_undo(self):
        prior_settings = (
            b'<settings><setting id="old" type="string">kept</setting>'
            b'</settings>')
        prior_helper = b'{"old":true}'
        settings_path = self.profile / SETTINGS_PATH
        settings_path.parent.mkdir(parents=True)
        settings_path.write_bytes(prior_settings)
        helper = next(path for path in self.files if path != SETTINGS_PATH)
        helper_path = self.profile / helper
        helper_path.parent.mkdir(parents=True)
        helper_path.write_bytes(prior_helper)

        pending = self.stage(FakeHost())
        target = pending['rollback_target']
        self.assertEqual([{
            'id': 'old', 'type': 'string', 'value': 'kept',
        }], target['skin_settings'])
        self.assertEqual(
            hashlib.sha256(prior_helper).hexdigest(),
            target['helper_hashes'][helper])
        self.assertEqual(
            {'lookandfeel.skincolors': 'Previous'},
            target['appearance'])

    def test_existing_pending_blocks_before_host_or_profile_effects(self):
        prepared = build_pending_restore(
            self.manifest, self.files, 'existing.zip')
        skin_state.write_pending_state(self.state, prepared)
        host = FakeHost()

        with self.assertRaises(skin_coordinator.SkinCoordinatorError):
            self.stage(host)
        self.assertEqual([], host.events)
        self.assertFalse((self.profile / SETTINGS_PATH).exists())

    def test_skin_rejection_preserves_prepared_state_without_file_changes(self):
        host = FakeHost(fail='ensure_inactive')
        with self.assertRaises(RuntimeError):
            self.stage(host)

        pending = skin_state.read_pending_state(self.state)
        self.assertEqual('prepared', pending['phase'])
        self.assertFalse(self.rollback.exists())
        self.assertFalse((self.profile / SETTINGS_PATH).exists())

    def test_playback_postcondition_blocks_before_transaction(self):
        host = FakeHost()

        def ineffective_stop():
            host._event('stop_playback')

        host.stop_playback = ineffective_stop
        with self.assertRaisesRegex(
                skin_coordinator.SkinCoordinatorError, 'playback did not stop'):
            self.stage(host)
        self.assertEqual('prepared', skin_state.read_pending_state(
            self.state)['phase'])
        self.assertFalse(self.rollback.exists())

    def test_inactive_skin_postcondition_blocks_before_transaction(self):
        host = FakeHost()

        def ineffective_switch(skin):
            host._event('ensure_inactive', skin)

        host.ensure_inactive = ineffective_switch
        with self.assertRaisesRegex(
                skin_coordinator.SkinCoordinatorError, 'still active'):
            self.stage(host)
        self.assertEqual('prepared', skin_state.read_pending_state(
            self.state)['phase'])
        self.assertFalse(self.rollback.exists())

    def test_vfs_failure_preserves_completed_rollback_and_files_applied(self):
        host = FakeHost(fail='stage_settings')
        with self.assertRaises(RuntimeError):
            self.stage(host)

        pending = skin_state.read_pending_state(self.state)
        self.assertEqual('files_applied', pending['phase'])
        self.assertEqual('complete', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))
        self.assertEqual('restage_settings',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])

    def test_crash_during_apply_preserves_exact_applying_transaction(self):
        def crash(_root, _relative, _data):
            raise KeyboardInterrupt('crash')

        with mock.patch.object(
                skin_transaction, '_atomic_write', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.stage(FakeHost())

        pending = skin_state.read_pending_state(self.state)
        self.assertEqual('transaction_prepared', pending['phase'])
        self.assertEqual('applying', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))
        self.assertEqual('rollback_transaction',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])

    def test_crash_after_commit_is_detected_as_resume_staging(self):
        real_write = skin_coordinator.write_pending_state
        writes = []

        def fail_after_commit(path, record):
            writes.append(record['phase'])
            if record['phase'] == 'files_applied':
                raise KeyboardInterrupt('crash after transaction')
            return real_write(path, record)

        with mock.patch.object(
                skin_coordinator, 'write_pending_state',
                side_effect=fail_after_commit):
            with self.assertRaises(KeyboardInterrupt):
                self.stage(FakeHost())

        self.assertEqual(
            ['prepared', 'transaction_prepared', 'files_applied'], writes)
        pending = skin_state.read_pending_state(self.state)
        self.assertEqual('transaction_prepared', pending['phase'])
        self.assertEqual('resume_staging',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])

    def test_failed_transaction_handoff_is_unlinked_and_recoverable(self):
        real_write = skin_coordinator.write_pending_state

        def fail_handoff(path, record):
            if record['phase'] == 'transaction_prepared':
                raise RuntimeError('state disk full')
            return real_write(path, record)

        with mock.patch.object(
                skin_coordinator, 'write_pending_state',
                side_effect=fail_handoff):
            with self.assertRaises(skin_transaction.SkinTransactionError):
                self.stage(FakeHost())

        pending = skin_state.read_pending_state(self.state)
        self.assertEqual('prepared', pending['phase'])
        self.assertEqual('recover_unlinked_transaction',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])
        self.assertFalse((self.profile / SETTINGS_PATH).exists())

    def test_inspection_rejects_substituted_transaction_identity(self):
        pending = self.stage(FakeHost())
        damaged = dict(pending)
        damaged['transaction_id'] = 'b137a8f5-130e-4a32-9df5-688632ee64ed'
        skin_state.write_pending_state(self.state, damaged)
        with self.assertRaises(skin_transaction.SkinTransactionError):
            skin_coordinator.inspect_pending_restore(
                self.profile, self.rollback, self.state)

    def test_inspection_rejects_an_additional_unresolved_transaction(self):
        self.stage(FakeHost())

        def crash(_root, _relative, _data):
            raise KeyboardInterrupt('second transaction')

        with mock.patch.object(
                skin_transaction, '_atomic_write', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                skin_transaction.apply_skin_snapshot(
                    self.profile, self.files, self.rollback)
        with self.assertRaisesRegex(
                skin_coordinator.SkinCoordinatorError,
                'another unresolved'):
            skin_coordinator.inspect_pending_restore(
                self.profile, self.rollback, self.state)

    def test_progress_failure_does_not_change_transaction_outcome(self):
        host = FakeHost()

        def broken_progress(_percent, _message):
            raise RuntimeError('dialog disappeared')

        host.progress = broken_progress
        pending = self.stage(host)
        self.assertEqual('rebuild', pending['phase'])

    def test_caller_payload_mutation_cannot_change_applied_snapshot(self):
        original = self.files[SETTINGS_PATH]
        host = FakeHost()
        real_switch = host.ensure_inactive

        def mutate_after_preflight(skin):
            real_switch(skin)
            self.files[SETTINGS_PATH] = b'<settings version="2" />'

        host.ensure_inactive = mutate_after_preflight
        self.stage(host)
        self.assertEqual(
            original, (self.profile / SETTINGS_PATH).read_bytes())

    def test_operation_lock_blocks_a_concurrent_restore_start(self):
        with skin_coordinator._operation_lock(self.state):
            with self.assertRaisesRegex(
                    skin_coordinator.SkinCoordinatorError,
                    'already running'):
                self.stage(FakeHost())
        self.assertIsNone(skin_state.read_pending_state(self.state))
        self.assertFalse((self.profile / SETTINGS_PATH).exists())

    def test_finish_activates_rebuilds_verifies_then_clears_state(self):
        host = FakeHost()
        pending = self.stage(host)
        result = skin_coordinator.finish_skin_restore(
            self.profile, self.rollback, self.state, host)

        finish_names = [event[0] for event in host.events]
        activate = finish_names.index('activate_skin')
        first_verify = finish_names.index('verify_loaded_settings')
        appearance = finish_names.index('apply_appearance')
        rebuild = finish_names.index('rebuild_skin')
        last_verify = len(finish_names) - 1 - finish_names[::-1].index(
            'verify_loaded_settings')
        self.assertLess(activate, first_verify)
        self.assertLess(first_verify, appearance)
        self.assertLess(appearance, rebuild)
        self.assertLess(rebuild, last_verify)
        self.assertIsNone(skin_state.read_pending_state(self.state))
        self.assertEqual(AF3_ID, result['skin_id'])
        self.assertEqual(len(pending['skin_settings']),
                         result['setting_count'])

    def test_finish_failure_preserves_pending_and_completed_rollback(self):
        host = FakeHost()
        pending = self.stage(host)
        host.fail = 'rebuild_skin'
        with self.assertRaises(RuntimeError):
            skin_coordinator.finish_skin_restore(
                self.profile, self.rollback, self.state, host)
        self.assertEqual(pending, skin_state.read_pending_state(self.state))
        self.assertEqual('complete', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))

    def test_finish_rejects_changed_helper_source_and_keeps_pending(self):
        host = FakeHost()
        pending = self.stage(host)
        helper = next(iter(pending['helper_hashes']))
        (self.profile / helper).write_bytes(b'{}\n')
        with self.assertRaisesRegex(
                skin_coordinator.SkinCoordinatorError,
                'helper sources'):
            skin_coordinator.finish_skin_restore(
                self.profile, self.rollback, self.state, host)
        self.assertEqual(pending, skin_state.read_pending_state(self.state))

    def test_finish_requires_exact_rebuild_phase(self):
        host = FakeHost(fail='stage_settings')
        with self.assertRaises(RuntimeError):
            self.stage(host)
        with self.assertRaisesRegex(
                skin_coordinator.SkinCoordinatorError, 'not ready'):
            skin_coordinator.finish_skin_restore(
                self.profile, self.rollback, self.state, FakeHost())


if __name__ == '__main__':
    unittest.main()
