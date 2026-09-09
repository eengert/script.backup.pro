from __future__ import unicode_literals

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from resources.lib import skin_transaction


SETTINGS = 'addon_data/skin.arctic.fuse.3/settings.xml'
NODE = ('addon_data/script.skinvariables/nodes/'
        'skin.arctic.fuse.3/main.json')


class SkinTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = self.root / 'profile'
        self.rollback = self.root / 'rollback'
        self.profile.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, relative, data):
        path = self.profile / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def incoming(self):
        return {
            SETTINGS: b'<?xml version="1.0"?><settings><setting id="new" type="string">yes</setting></settings>',
            NODE: b'{"new":true}',
        }

    def journal(self, directory):
        return json.loads((Path(directory) / 'transaction.json').read_text())

    def test_apply_and_manual_rollback_restore_exact_previous_files(self):
        old_settings = self.write(SETTINGS, b'<settings><setting id="old">yes</setting></settings>')
        stale = self.write(NODE.replace('main.json', 'stale.json'), b'{broken json')

        directory = skin_transaction.apply_skin_snapshot(
            self.profile, self.incoming(), self.rollback)

        self.assertEqual(self.incoming()[SETTINGS], old_settings.read_bytes())
        self.assertFalse(stale.exists())
        self.assertEqual('complete', self.journal(directory)['status'])

        skin_transaction.rollback_skin_transaction(self.profile, directory)
        self.assertEqual(b'<settings><setting id="old">yes</setting></settings>',
                         old_settings.read_bytes())
        self.assertEqual(b'{broken json', stale.read_bytes())
        self.assertFalse((self.profile / NODE).exists())
        self.assertEqual('rolled_back', self.journal(directory)['status'])

    def test_write_failure_automatically_restores_previous_files(self):
        original = self.write(SETTINGS, b'<settings />')
        real_write = skin_transaction._atomic_write

        def fail_node(root, relative, data):
            if relative == NODE:
                raise OSError('disk full')
            return real_write(root, relative, data)

        with mock.patch.object(
                skin_transaction, '_atomic_write', side_effect=fail_node):
            with self.assertRaises(skin_transaction.SkinTransactionError):
                skin_transaction.apply_skin_snapshot(
                    self.profile, self.incoming(), self.rollback)

        self.assertEqual(b'<settings />', original.read_bytes())
        directory = next(self.rollback.iterdir())
        self.assertEqual('rolled_back', self.journal(directory)['status'])

    def test_process_crash_while_applying_is_recovered_next_run(self):
        original = self.write(SETTINGS, b'<settings />')

        def crash(_root, _relative, _data):
            raise KeyboardInterrupt('simulated crash')

        with mock.patch.object(skin_transaction, '_atomic_write', side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):
                skin_transaction.apply_skin_snapshot(
                    self.profile, self.incoming(), self.rollback)

        directory = next(self.rollback.iterdir())
        self.assertEqual('applying', self.journal(directory)['status'])
        self.assertEqual([str(directory)], skin_transaction.pending_skin_transactions(
            self.profile, self.rollback))
        with self.assertRaises(skin_transaction.SkinTransactionError):
            skin_transaction.apply_skin_snapshot(
                self.profile, self.incoming(), self.rollback)
        self.assertEqual(1, len(tuple(self.rollback.iterdir())))
        recovered = skin_transaction.recover_pending_transactions(
            self.profile, self.rollback)
        self.assertEqual([str(directory)], recovered)
        self.assertEqual(b'<settings />', original.read_bytes())
        self.assertEqual('rolled_back', self.journal(directory)['status'])

    def test_rejects_invalid_payload_before_creating_rollback(self):
        invalid = self.incoming()
        invalid[NODE] = b'not json'
        with self.assertRaises(skin_transaction.SkinTransactionError):
            skin_transaction.apply_skin_snapshot(
                self.profile, invalid, self.rollback)
        self.assertFalse(self.rollback.exists())

    def test_rollback_is_bound_to_exact_profile(self):
        directory = skin_transaction.apply_skin_snapshot(
            self.profile, self.incoming(), self.rollback)
        other = self.root / 'other'
        other.mkdir()
        with self.assertRaises(skin_transaction.SkinTransactionError):
            skin_transaction.rollback_skin_transaction(other, directory)

    def test_rollback_rejects_symlinked_snapshot_before_mutation(self):
        original = self.write(SETTINGS, b'<settings />')
        directory = Path(skin_transaction.apply_skin_snapshot(
            self.profile, self.incoming(), self.rollback))
        snapshot = directory / 'files' / SETTINGS
        outside = self.root / 'outside'
        outside.write_bytes(b'<settings><setting id="attacker" /></settings>')
        snapshot.unlink()
        try:
            snapshot.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are unavailable')
        with self.assertRaises(skin_transaction.SkinTransactionError):
            skin_transaction.rollback_skin_transaction(self.profile, directory)
        self.assertEqual(self.incoming()[SETTINGS], original.read_bytes())


if __name__ == '__main__':
    unittest.main()
