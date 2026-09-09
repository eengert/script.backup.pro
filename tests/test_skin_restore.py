from __future__ import unicode_literals

import hashlib
import unittest

from resources.lib.archive import ARCHIVE_ID, ARCHIVE_VERSION, validate_manifest
from resources.lib.skin_adapter import snapshot_fingerprint
from resources.lib import skin_restore


SETTINGS = 'addon_data/skin.arctic.fuse.3/settings.xml'
HELPER = ('addon_data/script.skinvariables/nodes/'
          'skin.arctic.fuse.3/main.json')
TRANSACTION_ID = 'b137a8f5-130e-4a32-9df5-688632ee64ed'


def fixture():
    payloads = {
        SETTINGS: b'<?xml version="1.0"?><settings><setting id="enabled" type="bool">true</setting></settings>',
        HELPER: b'{"label":"Home"}',
    }
    records = [{
        'path': path,
        'size': len(data),
        'sha256': hashlib.sha256(data).hexdigest(),
    } for path, data in sorted(payloads.items())]
    metadata = {
        'adapter_id': 'backup-pro.af3',
        'adapter_version': 1,
        'skin_id': 'skin.arctic.fuse.3',
        'skin_version': '3.9.0',
        'helper_id': 'script.skinvariables',
        'helper_version': '2.2.1',
        'source_device': 'MacBook',
        'source_profile': 'Master',
        'setting_count': 1,
        'helper_file_count': 1,
        'file_count': 2,
        'total_bytes': sum(len(data) for data in payloads.values()),
        'fingerprint': snapshot_fingerprint(payloads),
    }
    manifest = validate_manifest({
        'archive_id': ARCHIVE_ID,
        'archive_version': ARCHIVE_VERSION,
        'created_utc': '2026-09-08T12:00:00Z',
        'skin_config': metadata,
        'directories': [{
            'name': 'skin_config',
            'path': 'special://profile/',
            'files': records,
        }],
    })
    return manifest, payloads


class SkinRestoreTests(unittest.TestCase):
    def test_preview_reports_source_versions_counts_and_rollback(self):
        manifest, _payloads = fixture()
        preview = skin_restore.skin_restore_preview(manifest)
        self.assertEqual('skin.arctic.fuse.3', preview['skin_id'])
        self.assertEqual('MacBook', preview['source_device'])
        self.assertEqual('Master', preview['source_profile'])
        self.assertEqual('3.9.0', preview['skin_version'])
        self.assertEqual(1, preview['setting_count'])
        self.assertEqual(1, preview['helper_file_count'])
        self.assertTrue(preview['rollback_required'])

    def test_load_verifies_every_payload_and_honors_cancellation(self):
        manifest, payloads = fixture()
        reads = []

        def read(path):
            reads.append(path)
            return payloads[path[len('skin_config/'):]]

        self.assertEqual(payloads, skin_restore.load_skin_snapshot(
            manifest, read))
        self.assertEqual(2, len(reads))
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.load_skin_snapshot(
                manifest, read, check_cancel=lambda: True)

    def test_load_rejects_same_size_payload_change_and_fingerprint_change(self):
        manifest, payloads = fixture()
        changed = dict(payloads)
        changed[HELPER] = b'X' * len(payloads[HELPER])
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.load_skin_snapshot(
                manifest,
                lambda path: changed[path[len('skin_config/'):]])

        damaged = dict(manifest)
        damaged['skin_config'] = dict(
            manifest['skin_config'], fingerprint='0' * 64)
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.load_skin_snapshot(
                damaged,
                lambda path: payloads[path[len('skin_config/'):]])

        wrong_count = dict(manifest)
        wrong_count['skin_config'] = dict(
            manifest['skin_config'], setting_count=0)
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.load_skin_snapshot(
                wrong_count,
                lambda path: payloads[path[len('skin_config/'):]])

    def test_pending_record_is_complete_and_rejects_tampering(self):
        manifest, payloads = fixture()
        record = skin_restore.build_pending_restore(
            manifest, payloads, '20260908120000.zip')
        self.assertEqual('prepared', record['phase'])
        self.assertEqual([{
            'id': 'enabled', 'type': 'boolean', 'value': True,
        }], record['skin_settings'])
        self.assertEqual({
            HELPER: hashlib.sha256(payloads[HELPER]).hexdigest(),
        }, record['helper_hashes'])

        damaged = dict(record)
        damaged['helper_hashes'] = {HELPER: '0' * 64}
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.validate_pending_restore(damaged)

        damaged = dict(record, phase='rebuild')
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.validate_pending_restore(damaged)

    def test_pending_codec_is_canonical_bounded_and_strict(self):
        manifest, payloads = fixture()
        record = skin_restore.build_pending_restore(
            manifest, payloads, '20260908120000.zip')
        encoded = skin_restore.dump_pending_restore(record)
        self.assertEqual(record, skin_restore.load_pending_restore(encoded))
        self.assertEqual(encoded, skin_restore.dump_pending_restore(
            skin_restore.load_pending_restore(encoded)))

        for invalid in (
                'not bytes', b'{', b'[]', b'\xff',
                b'x' * (skin_restore.PENDING_MAX_BYTES + 1)):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(skin_restore.SkinRestoreError):
                    skin_restore.load_pending_restore(invalid)

    def test_pending_phase_transitions_require_and_preserve_rollback(self):
        manifest, payloads = fixture()
        prepared = skin_restore.build_pending_restore(
            manifest, payloads, '20260908120000.zip')
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.advance_pending_restore(prepared, 'files_applied')
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.advance_pending_restore(
                prepared, 'rebuild', '/rollback/one')

        transaction = skin_restore.advance_pending_restore(
            prepared, 'transaction_prepared', '/rollback/one',
            TRANSACTION_ID)
        applied = skin_restore.advance_pending_restore(
            transaction, 'files_applied')
        rebuilding = skin_restore.advance_pending_restore(applied, 'rebuild')
        recovering = skin_restore.advance_pending_restore(
            rebuilding, 'rollback_rebuild')
        self.assertEqual('/rollback/one', recovering['rollback'])

        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.advance_pending_restore(
                applied, 'rebuild', '/rollback/two')
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.advance_pending_restore(
                applied, 'rebuild', transaction_id=
                'd937a8f5-130e-4a32-9df5-688632ee64ed')
        with self.assertRaises(skin_restore.SkinRestoreError):
            skin_restore.advance_pending_restore(recovering, 'rebuild')

if __name__ == '__main__':
    unittest.main()
