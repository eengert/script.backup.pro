from __future__ import unicode_literals

import hashlib
import json
import os
import stat
import tempfile
import unittest
import zipfile

from resources.lib.archive import (
    ARCHIVE_ID,
    ARCHIVE_VERSION,
    ArchiveValidationError,
    build_manifest,
    load_manifest,
    normalize_member_path,
    sha256_reader,
    validate_archive_members,
    validate_archive_layout,
    validate_manifest,
    verify_manifest_files,
    verify_zip_archive,
)
from resources.lib.skin_adapter import snapshot_fingerprint


class Entry:
    def __init__(self, filename, size=0, compressed=None, mode=0,
                 compression=zipfile.ZIP_DEFLATED, flags=0):
        self.filename = filename
        self.file_size = size
        self.compress_size = size if compressed is None else compressed
        self.external_attr = mode << 16
        self.compress_type = compression
        self.flag_bits = flags

    def is_dir(self):
        return self.filename.endswith('/')


def manifest(files=None):
    return {
        'archive_id': ARCHIVE_ID,
        'archive_version': ARCHIVE_VERSION,
        'directories': [{
            'name': 'addon_data',
            'path': 'special://home/userdata/addon_data',
            'files': files if files is not None else [{
                'path': 'example/settings.xml',
                'size': 3,
                'sha256': hashlib.sha256(b'abc').hexdigest(),
            }],
        }],
    }


class MemberPathTests(unittest.TestCase):
    def test_accepts_portable_unicode_relative_path(self):
        self.assertEqual('folder/café.txt',
                         normalize_member_path('folder/café.txt'))

    def test_rejects_traversal_absolute_drive_backslash_and_dot_paths(self):
        unsafe = ('../escape', 'a/../../escape', '/absolute',
                  'C:/drive', '//server/share', 'a\\b', './dot',
                  'a//b', 'a/./b', 'nul\x00name')
        for path in unsafe:
            with self.subTest(path=path):
                with self.assertRaises(ArchiveValidationError):
                    normalize_member_path(path)


class ArchiveMemberTests(unittest.TestCase):
    def test_accepts_regular_files_and_directories(self):
        result = validate_archive_members([
            Entry('backup/', mode=stat.S_IFDIR | 0o755),
            Entry('backup/file.txt', size=30, compressed=15,
                  mode=stat.S_IFREG | 0o644),
        ])
        self.assertEqual(1, result['file_count'])
        self.assertEqual(30, result['total_bytes'])

    def test_rejects_exact_and_case_colliding_duplicates(self):
        for second in ('a.txt', 'A.TXT'):
            with self.subTest(second=second):
                with self.assertRaises(ArchiveValidationError):
                    validate_archive_members([Entry('a.txt'), Entry(second)])

    def test_rejects_symlink_and_special_file_entries(self):
        for mode in (stat.S_IFLNK | 0o777, stat.S_IFIFO | 0o600):
            with self.subTest(mode=mode):
                with self.assertRaises(ArchiveValidationError):
                    validate_archive_members([Entry('unsafe', mode=mode)])

    def test_rejects_encryption_and_unsupported_compression(self):
        with self.assertRaises(ArchiveValidationError):
            validate_archive_members([Entry('secret', flags=1)])
        with self.assertRaises(ArchiveValidationError):
            validate_archive_members([
                Entry('odd', compression=zipfile.ZIP_BZIP2)])

    def test_enforces_count_member_total_and_ratio_limits(self):
        cases = (
            ({'max_file_count': 1}, [Entry('a'), Entry('b')]),
            ({'max_member_bytes': 9}, [Entry('a', size=10)]),
            ({'max_total_bytes': 9}, [Entry('a', size=5), Entry('b', size=5)]),
            ({'max_compression_ratio': 9},
             [Entry('a', size=100, compressed=10)]),
        )
        for limits, entries in cases:
            with self.subTest(limits=limits):
                with self.assertRaises(ArchiveValidationError):
                    validate_archive_members(entries, **limits)

    def test_rejects_empty_archive(self):
        with self.assertRaises(ArchiveValidationError):
            validate_archive_members([])


class ManifestTests(unittest.TestCase):
    def test_builds_portable_sorted_file_records(self):
        contents = {
            '/profile/addon_data/z.txt': b'z',
            '/profile/addon_data/a.txt': b'abc',
        }

        def hash_file(path):
            value = contents[path]
            return hashlib.sha256(value).hexdigest(), len(value)

        result = build_manifest([{
            'name': 'addon_data',
            'source': 'special://home/userdata/addon_data',
            'plan_root': '/profile/addon_data',
            'files': [
                {'file': '/profile/addon_data/z.txt', 'is_dir': False},
                {'file': '/profile/addon_data/folder', 'is_dir': True},
                {'file': '/profile/addon_data/a.txt', 'is_dir': False},
            ],
        }], hash_file, {'kodi_version': '22.0'})

        self.assertEqual(['a.txt', 'z.txt'], [
            item['path'] for item in result['directories'][0]['files']])
        self.assertEqual(4, result['total_bytes'])
        self.assertEqual('22.0', result['kodi_version'])

    def test_skin_snapshot_uses_restore_path_and_validates_metadata(self):
        payloads = {
            '/stage/addon_data/skin.arctic.fuse.3/settings.xml': b'<settings />',
            '/stage/addon_data/script.skinvariables/nodes/skin.arctic.fuse.3/main.json': b'{}',
        }
        snapshot_files = {
            path[len('/stage/'):]: data for path, data in payloads.items()
        }
        metadata = {
            'adapter_id': 'backup-pro.af3',
            'adapter_version': 2,
            'skin_id': 'skin.arctic.fuse.3',
            'skin_version': '3.9.0',
            'helper_id': 'script.skinvariables',
            'helper_version': '2.2.1',
            'source_device': 'MacBook',
            'source_profile': 'Master',
            'setting_count': 0,
            'helper_file_count': 1,
            'file_count': 2,
            'total_bytes': sum(len(value) for value in payloads.values()),
            'fingerprint': snapshot_fingerprint(snapshot_files),
            'appearance': {},
        }
        result = build_manifest([{
            'name': 'skin_config',
            'source': '/stage',
            'restore_path': 'special://profile/',
            'plan_root': '/stage',
            'files': [
                {'file': path, 'is_dir': False} for path in payloads
            ],
        }], lambda path: (
            hashlib.sha256(payloads[path]).hexdigest(), len(payloads[path])),
            {'skin_config': metadata})

        self.assertEqual('special://profile/', result['directories'][0]['path'])
        self.assertEqual(metadata, result['skin_config'])

        damaged = dict(result)
        damaged['skin_config'] = dict(metadata, helper_file_count=2)
        with self.assertRaises(ArchiveValidationError):
            validate_manifest(damaged)

    def test_skin_snapshot_directory_and_metadata_must_appear_together(self):
        ordinary = manifest()
        ordinary['skin_config'] = {}
        with self.assertRaises(ArchiveValidationError):
            validate_manifest(ordinary)

        renamed = manifest()
        renamed['directories'][0]['name'] = 'Skin_Config'
        with self.assertRaises(ArchiveValidationError):
            validate_manifest(renamed)

    def test_validates_and_recomputes_counts(self):
        result = validate_manifest(manifest())
        self.assertEqual(1, result['file_count'])
        self.assertEqual(3, result['total_bytes'])

    def test_rejects_other_or_future_formats(self):
        wrong_id = manifest()
        wrong_id['archive_id'] = 'script.xbmcbackup'
        with self.assertRaises(ArchiveValidationError):
            validate_manifest(wrong_id)
        future = manifest()
        future['archive_version'] += 1
        with self.assertRaises(ArchiveValidationError):
            validate_manifest(future)

    def test_rejects_duplicate_and_reserved_group_names(self):
        for names in (('Group', 'group'), ('backup-pro.manifest.json',)):
            document = manifest([])
            document['directories'] = [
                {'name': name, 'path': 'special://home', 'files': []}
                for name in names
            ]
            with self.subTest(names=names):
                with self.assertRaises(ArchiveValidationError):
                    validate_manifest(document)

    def test_rejects_case_colliding_paths_and_bad_hash(self):
        duplicate = manifest([
            {'path': 'A.txt', 'size': 0, 'sha256': '0' * 64},
            {'path': 'a.TXT', 'size': 0, 'sha256': '0' * 64},
        ])
        with self.assertRaises(ArchiveValidationError):
            validate_manifest(duplicate)
        bad_hash = manifest()
        bad_hash['directories'][0]['files'][0]['sha256'] = 'not-a-hash'
        with self.assertRaises(ArchiveValidationError):
            validate_manifest(bad_hash)

    def test_invalid_json_is_reported_as_validation_error(self):
        with self.assertRaises(ArchiveValidationError):
            load_manifest('{')

    def test_archive_layout_must_match_manifest(self):
        document = manifest()
        manifest_size = len(str(document))
        valid = [
            Entry('backup/backup-pro.manifest.json', size=manifest_size),
            Entry('backup/addon_data/example/settings.xml', size=3),
        ]
        self.assertEqual('backup',
                         validate_archive_layout(valid, document)['root'])

        failures = (
            valid[:1],
            valid + [Entry('backup/undeclared.txt')],
            [valid[0], Entry(
                'backup/addon_data/example/settings.xml', size=4)],
        )
        for entries in failures:
            with self.subTest(entries=[entry.filename for entry in entries]):
                with self.assertRaises(ArchiveValidationError):
                    validate_archive_layout(entries, document)

    def test_archive_layout_rejects_explicit_directories(self):
        document = manifest()
        entries = [
            Entry('backup/', mode=stat.S_IFDIR | 0o755),
            Entry('backup/backup-pro.manifest.json'),
            Entry('backup/addon_data/example/settings.xml', size=3),
        ]
        with self.assertRaises(ArchiveValidationError):
            validate_archive_layout(entries, document)


class HashTests(unittest.TestCase):
    def test_hashes_incremental_reader_without_loading_whole_file(self):
        chunks = [b'ab', b'c', b'']

        def read_chunk(_size):
            return chunks.pop(0)

        digest, size = sha256_reader(read_chunk, chunk_size=2)
        self.assertEqual(hashlib.sha256(b'abc').hexdigest(), digest)
        self.assertEqual(3, size)

    def test_hashing_honors_cancellation_between_chunks(self):
        calls = []

        def cancelled():
            calls.append(True)
            return len(calls) > 1

        with self.assertRaises(ArchiveValidationError):
            sha256_reader(lambda _size: b'x', check_cancel=cancelled)

    def test_folder_payload_verification_detects_same_size_change(self):
        document = manifest()
        good = lambda _path: (hashlib.sha256(b'abc').hexdigest(), 3)
        self.assertEqual(1, verify_manifest_files(
            document, good)['file_count'])
        with self.assertRaises(ArchiveValidationError):
            verify_manifest_files(
                document,
                lambda _path: (hashlib.sha256(b'xyz').hexdigest(), 3))

    def test_completed_zip_verification_reads_every_payload(self):
        payload = b'abc'
        document = manifest()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'backup.zip')
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    'backup/backup-pro.manifest.json', json.dumps(document))
                archive.writestr(
                    'backup/addon_data/example/settings.xml', payload)
            self.assertEqual(1, verify_zip_archive(path)['file_count'])

            changed = os.path.join(directory, 'changed.zip')
            with zipfile.ZipFile(changed, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    'backup/backup-pro.manifest.json', json.dumps(document))
                archive.writestr(
                    'backup/addon_data/example/settings.xml', b'xyz')
            with self.assertRaises(ArchiveValidationError):
                verify_zip_archive(changed)


if __name__ == '__main__':
    unittest.main()
