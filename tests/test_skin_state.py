from __future__ import unicode_literals

import copy
import tempfile
import unittest
from pathlib import Path

from resources.lib import skin_state
from tests.test_skin_restore import fixture
from tests.test_skin_restore import TRANSACTION_ID
from resources.lib.skin_restore import (
    advance_pending_restore,
    build_pending_restore,
    build_rollback_target,
)


class SkinStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'pending.json'
        manifest, payloads = fixture()
        prepared = build_pending_restore(
            manifest, payloads, '20260908120000.zip')
        transaction = advance_pending_restore(
            prepared, 'transaction_prepared', '/rollback/one',
            TRANSACTION_ID,
            build_rollback_target({}, {}))
        self.record = advance_pending_restore(transaction, 'files_applied')

    def test_atomic_write_read_and_verified_clear(self):
        written = skin_state.write_pending_state(self.path, self.record)
        self.assertEqual(self.record, written)
        self.assertEqual(self.record, skin_state.read_pending_state(self.path))
        self.assertFalse(any(
            item.name.startswith('.backup-pro-pending-')
            for item in self.path.parent.iterdir()))

        skin_state.clear_pending_state(self.path, self.record)
        self.assertIsNone(skin_state.read_pending_state(self.path))

    def test_corrupt_or_changed_state_is_never_cleared(self):
        skin_state.write_pending_state(self.path, self.record)
        changed = advance_pending_restore(self.record, 'rebuild')
        with self.assertRaises(skin_state.SkinStateError):
            skin_state.clear_pending_state(self.path, changed)
        self.assertTrue(self.path.exists())

        self.path.write_bytes(b'{')
        with self.assertRaises(skin_state.SkinStateError):
            skin_state.read_pending_state(self.path)
        self.assertTrue(self.path.exists())

    def test_rejects_invalid_record_and_unsafe_paths(self):
        damaged = copy.deepcopy(self.record)
        damaged['rollback'] = ''
        with self.assertRaises(skin_state.SkinStateError):
            skin_state.write_pending_state(self.path, damaged)

        outside = self.path.parent / 'outside.json'
        outside.write_bytes(b'untouched')
        self.path.symlink_to(outside)
        with self.assertRaises(skin_state.SkinStateError):
            skin_state.write_pending_state(self.path, self.record)
        with self.assertRaises(skin_state.SkinStateError):
            skin_state.read_pending_state(self.path)
        self.assertEqual(b'untouched', outside.read_bytes())


if __name__ == '__main__':
    unittest.main()
