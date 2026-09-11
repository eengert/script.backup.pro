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

    def stage_rollback_settings(self, skin, document, values):
        if self.skin == skin:
            raise AssertionError('rollback settings staged while target active')
        self._event('stage_rollback_settings', skin, document, values)

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

    def verify_rollback_settings_unchanged(self, skin, document):
        if self.skin != skin:
            raise AssertionError('verified settings for an inactive skin')
        self._event('verify_rollback_settings_unchanged', skin, document)

    def apply_appearance(self, values):
        self._event('apply_appearance', values)

    def rebuild_skin(self, skin, pending):
        if self.skin != skin:
            raise AssertionError('rebuilt an inactive skin')
        self._event('rebuild_skin', skin, pending)

    def progress(self, percent, message):
        self.events.append(('progress', percent, message))

    def wait(self, seconds):
        self._event('wait', seconds)


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

    def test_resume_staging_after_crash_post_transaction_commit(self):
        real_write = skin_coordinator.write_pending_state

        def fail_after_commit(path, record):
            if record['phase'] == 'files_applied':
                raise KeyboardInterrupt('crash after transaction')
            return real_write(path, record)

        with mock.patch.object(
                skin_coordinator, 'write_pending_state',
                side_effect=fail_after_commit):
            with self.assertRaises(KeyboardInterrupt):
                self.stage(FakeHost())
        self.assertEqual('resume_staging',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])
        host = FakeHost()

        pending = skin_coordinator.resume_skin_restore_staging(
            self.profile, self.rollback, self.state, host)

        self.assertEqual('rebuild', pending['phase'])
        self.assertEqual('finish_rebuild',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])
        self.assertIn('stage_settings', [event[0] for event in host.events])

    def test_resume_staging_rejects_changed_committed_files(self):
        real_write = skin_coordinator.write_pending_state

        def fail_after_commit(path, record):
            if record['phase'] == 'files_applied':
                raise KeyboardInterrupt('crash after transaction')
            return real_write(path, record)

        with mock.patch.object(
                skin_coordinator, 'write_pending_state',
                side_effect=fail_after_commit):
            with self.assertRaises(KeyboardInterrupt):
                self.stage(FakeHost())
        helper = next(path for path in self.files if path != SETTINGS_PATH)
        (self.profile / helper).write_bytes(b'changed')
        host = FakeHost()

        with self.assertRaisesRegex(
                skin_coordinator.SkinCoordinatorError, 'no longer'):
            skin_coordinator.resume_skin_restore_staging(
                self.profile, self.rollback, self.state, host)
        self.assertEqual([], host.events)

    def test_resume_restages_settings_after_vfs_failure(self):
        with self.assertRaises(RuntimeError):
            self.stage(FakeHost(fail='stage_settings'))
        self.assertEqual('restage_settings',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])
        host = FakeHost()

        pending = skin_coordinator.resume_skin_restore_staging(
            self.profile, self.rollback, self.state, host)

        self.assertEqual('rebuild', pending['phase'])
        self.assertIn('stage_settings', [event[0] for event in host.events])

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

    def test_finish_retries_helper_verification_through_a_transient_mismatch(self):
        # regression guard: rebuild_skin()'s own RunScript completion
        # signal can fire a moment before its file writes are fully
        # flushed to disk - confirmed live 2026-09-10, where a hash
        # mismatch here resolved on its own within a few seconds with
        # no further action taken (a settling delay, not corrupted or
        # missing content). _verify_helper_sources() must retry briefly
        # (the same bounded pattern verify_loaded_settings() already
        # uses) rather than fail the whole restore for that delay.
        host = FakeHost()
        pending = self.stage(host)
        helper = next(iter(pending['helper_hashes']))
        original = (self.profile / helper).read_bytes()
        (self.profile / helper).write_bytes(b'{"still-writing": true}')

        def settle_on_wait(_seconds):
            (self.profile / helper).write_bytes(original)
        host.wait = settle_on_wait

        result = skin_coordinator.finish_skin_restore(
            self.profile, self.rollback, self.state, host)

        self.assertEqual(AF3_ID, result['skin_id'])
        self.assertIsNone(skin_state.read_pending_state(self.state))

    def test_finish_waits_for_writes_to_settle_not_a_fixed_retry_count(self):
        # regression guard: rebuild_skin()'s writes can land in more than
        # one step before settling on their final content - simulate a
        # helper file rewritten several times before finally settling,
        # and confirm verification keeps retrying through each
        # intermediate value rather than judging too early.
        host = FakeHost()
        pending = self.stage(host)
        helper = next(iter(pending['helper_hashes']))
        original = (self.profile / helper).read_bytes()
        (self.profile / helper).write_bytes(b'{"still-writing": 1}')

        remaining = [b'{"still-writing": 2}', b'{"still-writing": 3}', original]

        def keep_changing(_seconds):
            if remaining:
                (self.profile / helper).write_bytes(remaining.pop(0))
        host.wait = keep_changing

        result = skin_coordinator.finish_skin_restore(
            self.profile, self.rollback, self.state, host)

        self.assertEqual(AF3_ID, result['skin_id'])
        self.assertEqual([], remaining)
        self.assertIsNone(skin_state.read_pending_state(self.state))

    def test_finish_ignores_helper_files_rebuild_creates_that_backup_never_captured(self):
        # regression guard, root-caused 2026-09-11: three straight fixed-
        # bound widenings (15s, 100s, 300s) all failed live even though
        # the four captured helper files matched their expected hashes
        # every single time this was checked directly. The real bug was
        # never about timing: read_current_managed_files() walks every
        # *currently* managed helper file, including a `-viewtypes.json`
        # cache rebuild_skin()'s own buildviews action can create fresh
        # on a profile that had none at backup time - `expected` (built
        # from what backup actually captured) never has that key, so
        # comparing the two dicts for exact equality could never
        # succeed, on any bound, however long. Verification must score
        # only the paths the backup actually captured, ignoring any
        # extra managed file rebuild_skin() legitimately creates as a
        # side effect.
        host = FakeHost()
        pending = self.stage(host)
        self.assertNotIn(
            'addon_data/script.skinvariables/skin.arctic.fuse.3-viewtypes.json',
            pending['helper_hashes'])
        viewtypes = (self.profile
                     / 'addon_data/script.skinvariables'
                     / 'skin.arctic.fuse.3-viewtypes.json')
        viewtypes.parent.mkdir(parents=True, exist_ok=True)
        viewtypes.write_bytes(b'{"created-by-rebuild": true}')

        result = skin_coordinator.finish_skin_restore(
            self.profile, self.rollback, self.state, host)

        self.assertEqual(AF3_ID, result['skin_id'])
        self.assertIsNone(skin_state.read_pending_state(self.state))

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

    def test_rollback_restores_previous_files_rebuilds_and_clears_state(self):
        prior_settings = (
            b'<settings><setting id="old" type="string">kept</setting>'
            b'</settings>')
        prior_helper = b'{"old":true}'
        (self.profile / SETTINGS_PATH).parent.mkdir(parents=True)
        (self.profile / SETTINGS_PATH).write_bytes(prior_settings)
        helper = next(path for path in self.files if path != SETTINGS_PATH)
        (self.profile / helper).parent.mkdir(parents=True)
        (self.profile / helper).write_bytes(prior_helper)
        host = FakeHost()
        pending = self.stage(host)

        result = skin_coordinator.rollback_skin_restore(
            self.profile, self.rollback, self.state, host)

        self.assertEqual(prior_settings,
                         (self.profile / SETTINGS_PATH).read_bytes())
        self.assertEqual(prior_helper, (self.profile / helper).read_bytes())
        self.assertIsNone(skin_state.read_pending_state(self.state))
        self.assertEqual('rolled_back', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))
        self.assertTrue(result['settings_verified'])
        names = [event[0] for event in host.events]
        rollback_stage = names.index('stage_rollback_settings')
        activate = len(names) - 1 - names[::-1].index('activate_skin')
        rebuild = len(names) - 1 - names[::-1].index('rebuild_skin')
        self.assertLess(rollback_stage, activate)
        self.assertLess(activate, rebuild)

    def test_rollback_restores_absent_settings_with_raw_verification(self):
        host = FakeHost()
        pending = self.stage(host)
        host.events = []

        result = skin_coordinator.rollback_skin_restore(
            self.profile, self.rollback, self.state, host)

        self.assertFalse(result['settings_verified'])
        rollback_event = next(
            event for event in host.events
            if event[0] == 'stage_rollback_settings')
        self.assertIsNone(rollback_event[2])
        self.assertFalse(any(
            event[0] == 'verify_loaded_settings'
            for event in host.events))
        raw_checks = [
            event for event in host.events
            if event[0] == 'verify_rollback_settings_unchanged']
        # Once before rebuild (post-activation) and once after rebuild
        # (post-ReloadSkin), matching the parsed-values verification path.
        self.assertEqual(2, len(raw_checks))
        for event in raw_checks:
            self.assertEqual(skin_coordinator.AF3_ID, event[1])
            self.assertIsNone(event[2])
        self.assertEqual('rolled_back', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))

    def test_rollback_stages_exact_malformed_previous_settings(self):
        malformed = b'not xml but exact prior bytes'
        (self.profile / SETTINGS_PATH).parent.mkdir(parents=True)
        (self.profile / SETTINGS_PATH).write_bytes(malformed)
        host = FakeHost()
        self.stage(host)
        host.events = []

        result = skin_coordinator.rollback_skin_restore(
            self.profile, self.rollback, self.state, host)

        rollback_event = next(
            event for event in host.events
            if event[0] == 'stage_rollback_settings')
        self.assertEqual(malformed, rollback_event[2])
        self.assertIsNone(rollback_event[3])
        self.assertFalse(result['settings_verified'])
        raw_checks = [
            event for event in host.events
            if event[0] == 'verify_rollback_settings_unchanged']
        self.assertEqual(2, len(raw_checks))
        for event in raw_checks:
            self.assertEqual(malformed, event[2])

    def test_rollback_rejects_target_that_no_longer_matches_snapshot(self):
        helper = next(path for path in self.files if path != SETTINGS_PATH)
        (self.profile / helper).parent.mkdir(parents=True)
        (self.profile / helper).write_bytes(b'{"prior":true}')
        host = FakeHost()
        pending = self.stage(host)
        damaged = dict(pending)
        target = dict(damaged['rollback_target'])
        target['helper_hashes'] = dict(target['helper_hashes'])
        target['helper_hashes'][helper] = '0' * 64
        damaged['rollback_target'] = target
        skin_state.write_pending_state(self.state, damaged)
        host.events = []

        with self.assertRaisesRegex(
                skin_coordinator.SkinCoordinatorError, 'no longer matches'):
            skin_coordinator.rollback_skin_restore(
                self.profile, self.rollback, self.state, host)
        self.assertEqual([], host.events)
        self.assertEqual('complete', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))

    def test_rollback_resumes_after_crash_following_file_rollback(self):
        host = FakeHost()
        pending = self.stage(host)
        real_rollback = skin_coordinator.rollback_skin_transaction

        def crash_after_rollback(profile, directory):
            real_rollback(profile, directory)
            raise KeyboardInterrupt('crash after rollback')

        with mock.patch.object(
                skin_coordinator, 'rollback_skin_transaction',
                side_effect=crash_after_rollback):
            with self.assertRaises(KeyboardInterrupt):
                skin_coordinator.rollback_skin_restore(
                    self.profile, self.rollback, self.state, host)

        self.assertEqual('finish_rollback_rebuild',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])
        result = skin_coordinator.rollback_skin_restore(
            self.profile, self.rollback, self.state, host)
        self.assertFalse(result['settings_verified'])
        self.assertIsNone(skin_state.read_pending_state(self.state))

    def test_rollback_recovers_interrupted_forward_file_apply(self):
        def crash(_root, _relative, _data):
            raise KeyboardInterrupt('crash during apply')

        with mock.patch.object(
                skin_transaction, '_atomic_write', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                self.stage(FakeHost())
        pending = skin_state.read_pending_state(self.state)
        self.assertEqual('transaction_prepared', pending['phase'])

        result = skin_coordinator.rollback_skin_restore(
            self.profile, self.rollback, self.state, FakeHost())

        self.assertFalse(result['settings_verified'])
        self.assertEqual('rolled_back', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))
        self.assertIsNone(skin_state.read_pending_state(self.state))

    def test_rollback_failure_keeps_durable_rollback_rebuild_state(self):
        host = FakeHost()
        pending = self.stage(host)
        host.fail = 'rebuild_skin'
        with self.assertRaises(RuntimeError):
            skin_coordinator.rollback_skin_restore(
                self.profile, self.rollback, self.state, host)
        saved = skin_state.read_pending_state(self.state)
        self.assertEqual('rollback_rebuild', saved['phase'])
        self.assertEqual('rolled_back', skin_transaction.skin_transaction_status(
            self.profile, self.rollback, pending['rollback'],
            pending['transaction_id']))

    def test_discard_restart_preflight_clears_state_with_no_transaction(self):
        with self.assertRaises(RuntimeError):
            self.stage(FakeHost(fail='stop_playback'))
        self.assertEqual('restart_preflight',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])

        result = skin_coordinator.discard_prepared_skin_restore(
            self.profile, self.rollback, self.state)

        self.assertEqual(
            {'action': 'restart_preflight', 'transaction_recovered': False},
            result)
        self.assertIsNone(skin_state.read_pending_state(self.state))
        self.assertEqual([], skin_transaction.pending_skin_transactions(
            self.profile, self.rollback))

    def test_discard_recovers_unlinked_transaction_then_clears_state(self):
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
        self.assertEqual('recover_unlinked_transaction',
                         skin_coordinator.inspect_pending_restore(
                             self.profile, self.rollback,
                             self.state)['action'])

        result = skin_coordinator.discard_prepared_skin_restore(
            self.profile, self.rollback, self.state)

        self.assertEqual({
            'action': 'recover_unlinked_transaction',
            'transaction_recovered': True,
        }, result)
        self.assertIsNone(skin_state.read_pending_state(self.state))
        self.assertEqual([], skin_transaction.pending_skin_transactions(
            self.profile, self.rollback))
        self.assertFalse((self.profile / SETTINGS_PATH).exists())

    def test_discard_rejects_a_staged_or_completed_restore(self):
        pending = self.stage(FakeHost())
        with self.assertRaisesRegex(
                skin_coordinator.SkinCoordinatorError,
                'not a discardable prepared state'):
            skin_coordinator.discard_prepared_skin_restore(
                self.profile, self.rollback, self.state)
        self.assertEqual(pending, skin_state.read_pending_state(self.state))

    def test_discard_rejects_when_nothing_is_pending(self):
        with self.assertRaises(skin_coordinator.SkinCoordinatorError):
            skin_coordinator.discard_prepared_skin_restore(
                self.profile, self.rollback, self.state)


if __name__ == '__main__':
    unittest.main()
