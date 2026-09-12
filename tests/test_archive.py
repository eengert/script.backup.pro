from __future__ import unicode_literals

import hashlib
import json
import os
import stat
import tempfile
import unittest
import zipfile
from unittest import mock

from resources.lib.archive import (
    ARCHIVE_ID,
    ARCHIVE_VERSION,
    ArchiveValidationError,
    build_manifest,
    collapse_identical_case_collisions,
    _hash_local_file,
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
    def _collision_groups(self, paths, contents, summary=None):
        return [{
            'name': 'addons',
            'source': '/profile/addons',
            'plan_root': '/profile/addons',
            'files': [{
                'file': '/profile/addons/' + path,
                'is_dir': False,
                'size': len(contents[path]) / 1024.0,
            } for path in paths],
            'summary': summary or {},
        }]

    def test_collapses_identical_case_only_sources_deterministically(self):
        contents = {
            'Example/File.txt': b'identical',
            'example/file.TXT': b'identical',
        }
        groups = self._collision_groups(
            ['example/file.TXT', 'Example/File.txt'], contents)

        filtered, exclusions = collapse_identical_case_collisions(
            groups, lambda path: (
                hashlib.sha256(contents[path[len('/profile/addons/'):]]).hexdigest(),
                len(contents[path[len('/profile/addons/'):]])))

        self.assertEqual(
            ['/profile/addons/Example/File.txt'],
            [item['file'] for item in filtered[0]['files']])
        self.assertEqual('addons/Example/File.txt',
                         exclusions[0]['kept_archive_path'])
        self.assertEqual('addons/example/file.TXT',
                         exclusions[0]['archive_path'])
        manifest_result = build_manifest(filtered, lambda path: (
            hashlib.sha256(
                contents[path[len('/profile/addons/'):]]).hexdigest(),
            len(contents[path[len('/profile/addons/'):]])))
        self.assertEqual(
            ['Example/File.txt'],
            [item['path']
             for item in manifest_result['directories'][0]['files']])

    def test_collision_result_does_not_depend_on_enumeration_order(self):
        contents = {'A.txt': b'same', 'a.TXT': b'same'}

        def collapse(paths):
            groups = self._collision_groups(paths, contents)
            filtered, _exclusions = collapse_identical_case_collisions(
                groups, lambda path: (
                    hashlib.sha256(
                        contents[path[len('/profile/addons/'):]]).hexdigest(),
                    len(contents[path[len('/profile/addons/'):]])))
            return [item['file'] for item in filtered[0]['files']]

        self.assertEqual(collapse(['A.txt', 'a.TXT']),
                         collapse(['a.TXT', 'A.txt']))

    def test_rejects_nonidentical_case_only_sources_with_all_paths(self):
        contents = {'A.txt': b'first', 'a.TXT': b'second'}
        groups = self._collision_groups(['A.txt', 'a.TXT'], contents)

        with self.assertRaises(ArchiveValidationError) as raised:
            collapse_identical_case_collisions(groups, lambda path: (
                hashlib.sha256(
                    contents[path[len('/profile/addons/'):]]).hexdigest(),
                len(contents[path[len('/profile/addons/'):]])))

        message = str(raised.exception)
        self.assertIn('/profile/addons/A.txt', message)
        self.assertIn('/profile/addons/a.TXT', message)
        self.assertIn('addons/A.txt', message)
        self.assertIn('addons/a.TXT', message)

    def test_collapses_more_than_two_identical_aliases(self):
        contents = {'ABC.txt': b'same', 'Abc.txt': b'same', 'abc.TXT': b'same'}
        groups = self._collision_groups(list(contents), contents)

        filtered, exclusions = collapse_identical_case_collisions(
            groups, lambda path: (
                hashlib.sha256(contents[path[len('/profile/addons/'):]]).hexdigest(),
                len(contents[path[len('/profile/addons/'):]])))

        self.assertEqual(['/profile/addons/ABC.txt'],
                         [item['file'] for item in filtered[0]['files']])
        self.assertEqual(2, len(exclusions))

    def test_noncolliding_sources_are_unchanged_without_hashing(self):
        contents = {'a.txt': b'a', 'b.txt': b'b'}
        groups = self._collision_groups(['b.txt', 'a.txt'], contents)

        filtered, exclusions = collapse_identical_case_collisions(
            groups, lambda _path: self.fail('ordinary files must not be hashed'))

        self.assertEqual(groups, filtered)
        self.assertEqual([], exclusions)

    def test_collapsed_alias_is_reflected_in_group_accounting(self):
        contents = {'A.txt': b'same', 'a.TXT': b'same'}
        groups = self._collision_groups(
            ['A.txt', 'a.TXT'], contents,
            summary={
                'included_kib': 8 / 1024.0,
                'included_files': 2,
                'excluded_kib': 0.0,
                'excluded_files': 0,
                'exclusions': [],
            })

        filtered, _exclusions = collapse_identical_case_collisions(
            groups, lambda path: (
                hashlib.sha256(contents[path[len('/profile/addons/'):]]).hexdigest(),
                len(contents[path[len('/profile/addons/'):]])))

        summary = filtered[0]['summary']
        self.assertEqual(1, summary['included_files'])
        self.assertEqual(1, summary['excluded_files'])
        self.assertEqual(4 / 1024.0, summary['included_kib'])
        self.assertEqual(4 / 1024.0, summary['excluded_kib'])
        self.assertEqual('casefold_alias', summary['exclusions'][0]['adapter'])

    def _mismatched_vfs_hash_file(self, contents, root='/profile/addons/'):
        # Simulates the tvOS bug: the primary (VFS) hash disagrees between
        # two same-size case-variant aliases even though their real bytes
        # are identical.
        def hash_file(path):
            key = path[len(root):]
            return 'vfs-fingerprint-of-' + key, len(contents[key])
        return hash_file

    def test_vfs_disagreement_collapses_when_local_read_proves_identical(self):
        contents = {'Example/File.txt': b'identical', 'example/file.TXT': b'identical'}
        groups = self._collision_groups(
            ['example/file.TXT', 'Example/File.txt'], contents)

        filtered, exclusions = collapse_identical_case_collisions(
            groups,
            self._mismatched_vfs_hash_file(contents),
            local_hash_file=lambda path: (
                hashlib.sha256(
                    contents[path[len('/profile/addons/'):]]).hexdigest(),
                len(contents[path[len('/profile/addons/'):]])))

        self.assertEqual(
            ['/profile/addons/Example/File.txt'],
            [item['file'] for item in filtered[0]['files']])
        self.assertEqual(1, len(exclusions))

    def test_vfs_disagreement_fails_closed_when_local_bytes_differ(self):
        contents = {'A.txt': b'aaaaa', 'a.TXT': b'bbbbb'}  # same size, real diff
        groups = self._collision_groups(['A.txt', 'a.TXT'], contents)

        with self.assertRaises(ArchiveValidationError) as raised:
            collapse_identical_case_collisions(
                groups,
                self._mismatched_vfs_hash_file(contents),
                local_hash_file=lambda path: (
                    hashlib.sha256(
                        contents[path[len('/profile/addons/'):]]).hexdigest(),
                    len(contents[path[len('/profile/addons/'):]])))

        self.assertIn('case-colliding source files differ',
                       str(raised.exception))

    def test_vfs_disagreement_fails_closed_when_fallback_unavailable(self):
        contents = {'Example/File.txt': b'identical', 'example/file.TXT': b'identical'}
        groups = self._collision_groups(
            ['example/file.TXT', 'Example/File.txt'], contents)

        with self.assertRaises(ArchiveValidationError):
            collapse_identical_case_collisions(
                groups,
                self._mismatched_vfs_hash_file(contents),
                local_hash_file=None)

    def test_vfs_disagreement_fails_closed_when_fallback_not_local(self):
        # The default local_hash_file (native filesystem access) cannot
        # resolve these synthetic, nonexistent test paths, so it must raise
        # and the caller must fail closed rather than treat that as safe.
        contents = {'Example/File.txt': b'identical', 'example/file.TXT': b'identical'}
        groups = self._collision_groups(
            ['example/file.TXT', 'Example/File.txt'], contents)

        with self.assertRaises(ArchiveValidationError):
            collapse_identical_case_collisions(
                groups,
                self._mismatched_vfs_hash_file(contents),
                local_hash_file=_hash_local_file)

    def test_vfs_disagreement_fails_closed_when_fallback_errors(self):
        contents = {'Example/File.txt': b'identical', 'example/file.TXT': b'identical'}
        groups = self._collision_groups(
            ['example/file.TXT', 'Example/File.txt'], contents)

        def broken(_path):
            raise IOError('device unavailable')

        with self.assertRaises(ArchiveValidationError):
            collapse_identical_case_collisions(
                groups,
                self._mismatched_vfs_hash_file(contents),
                local_hash_file=broken)

    def test_vfs_size_mismatch_logs_fallback_not_entered_and_fails(self):
        contents = {'A.txt': b'same', 'a.TXT': b'same'}
        groups = self._collision_groups(['A.txt', 'a.TXT'], contents)
        messages = []

        def mismatched_size(path):
            key = path[len('/profile/addons/'):]
            return 'vfs-' + key, 4 if key == 'A.txt' else 5

        with self.assertRaises(ArchiveValidationError):
            collapse_identical_case_collisions(
                groups, mismatched_size,
                local_hash_file=lambda _path: self.fail(
                    'size mismatch must not enter fallback'),
                diagnostic_log=messages.append)

        self.assertTrue(any(
            'native fallback not entered' in message and 'sizes=[4, 5]' in message
            for message in messages))
        self.assertEqual(2, sum(
            'primary VFS hash:' in message for message in messages))

    def test_fallback_entry_and_success_details_are_logged(self):
        contents = {'A.txt': b'same', 'a.TXT': b'same'}
        groups = self._collision_groups(['A.txt', 'a.TXT'], contents)
        messages = []
        digest = hashlib.sha256(b'same').hexdigest()

        with mock.patch('resources.lib.archive.os.path.isfile', return_value=True):
            filtered, exclusions = collapse_identical_case_collisions(
                groups, self._mismatched_vfs_hash_file(contents),
                local_hash_file=lambda _path: (digest, 4),
                diagnostic_log=messages.append)

        self.assertEqual(1, len(filtered[0]['files']))
        self.assertEqual(1, len(exclusions))
        self.assertTrue(any('native fallback entered:' in m for m in messages))
        self.assertEqual(2, sum(
            'eligible=true os.path.isfile=true' in m for m in messages))
        native = [m for m in messages if m.startswith('native hash:')]
        self.assertEqual(2, len(native))
        self.assertTrue(all('bytes=4 sha256=' + digest in m for m in native))
        self.assertIn('native fallback result: identical=true', messages)

    def test_non_file_eligibility_is_logged_and_still_fails_closed(self):
        contents = {'A.txt': b'same', 'a.TXT': b'same'}
        groups = self._collision_groups(['A.txt', 'a.TXT'], contents)
        messages = []

        with mock.patch('resources.lib.archive.os.path.isfile', return_value=False):
            with self.assertRaises(ArchiveValidationError):
                collapse_identical_case_collisions(
                    groups, self._mismatched_vfs_hash_file(contents),
                    local_hash_file=_hash_local_file,
                    diagnostic_log=messages.append)

        self.assertTrue(any(
            'eligible=false os.path.isfile=false' in message
            for message in messages))
        self.assertTrue(any(
            'exception=ArchiveValidationError: path is not a native local file:'
            in message for message in messages))

    def test_native_hash_exception_is_logged_and_still_fails_closed(self):
        contents = {'A.txt': b'same', 'a.TXT': b'same'}
        groups = self._collision_groups(['A.txt', 'a.TXT'], contents)
        messages = []

        def broken(_path):
            raise OSError('native read denied')

        with self.assertRaises(ArchiveValidationError):
            collapse_identical_case_collisions(
                groups, self._mismatched_vfs_hash_file(contents),
                local_hash_file=broken, diagnostic_log=messages.append)

        self.assertTrue(any(
            'exception=OSError: native read denied' in message
            for message in messages))

    def test_fallback_not_consulted_when_vfs_hashes_already_agree(self):
        contents = {'A.txt': b'same', 'a.TXT': b'same'}
        groups = self._collision_groups(['A.txt', 'a.TXT'], contents)

        def must_not_be_called(_path):
            self.fail('local fallback must not run on the agreeing fast path')

        filtered, exclusions = collapse_identical_case_collisions(
            groups, lambda path: (
                hashlib.sha256(
                    contents[path[len('/profile/addons/'):]]).hexdigest(),
                len(contents[path[len('/profile/addons/'):]])),
            local_hash_file=must_not_be_called)

        self.assertEqual(1, len(exclusions))

    def test_vfs_disagreement_with_three_aliases_requires_all_local_matches(self):
        contents = {
            'ABC.txt': b'same', 'Abc.txt': b'same', 'abc.TXT': b'different',
        }
        groups = self._collision_groups(list(contents), contents)

        with self.assertRaises(ArchiveValidationError):
            collapse_identical_case_collisions(
                groups,
                self._mismatched_vfs_hash_file(
                    {k: b'same' for k in contents}),  # same size for all
                local_hash_file=lambda path: (
                    hashlib.sha256(
                        contents[path[len('/profile/addons/'):]]).hexdigest(),
                    len(contents[path[len('/profile/addons/'):]])))

    def test_real_local_fallback_reads_actual_bytes_from_disk(self):
        tmpdir = tempfile.mkdtemp()
        upper = os.path.join(tmpdir, 'UP.txt')
        lower = os.path.join(tmpdir, 'up.txt')
        with open(upper, 'wb') as handle:
            handle.write(b'payload')
        with open(lower, 'wb') as handle:
            handle.write(b'payload')

        checksum_a, size_a = _hash_local_file(upper)
        checksum_b, size_b = _hash_local_file(lower)
        self.assertEqual(size_a, size_b)
        self.assertEqual(checksum_a, checksum_b)
        self.assertEqual(hashlib.sha256(b'payload').hexdigest(), checksum_a)

        with self.assertRaises(ArchiveValidationError):
            _hash_local_file('smb://example/share/file.txt')
        with self.assertRaises(ArchiveValidationError):
            _hash_local_file(os.path.join(tmpdir, 'missing.txt'))

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

    def test_hashes_bytearray_chunks(self):
        # regression guard: xbmcvfs.File.read() returns bytearray on at
        # least one real Kodi 21.1 build (confirmed empirically against
        # a live disposable-profile backup on 2026-09-10 - see
        # docs/MAC_KODI_VALIDATION.md). bytearray is not an instance of
        # bytes, so it fell through to `.encode('utf-8')`, which
        # bytearray does not have, crashing every real backup's manifest
        # creation on that build.
        chunks = [bytearray(b'ab'), bytearray(b'c'), bytearray(b'')]

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
