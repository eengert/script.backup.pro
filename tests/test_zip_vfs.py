from __future__ import unicode_literals

import os
import hashlib
import json
import tempfile
import unittest
import zipfile

from tests.test_backup_bridge import install_kodi_stubs

install_kodi_stubs()

import xbmcvfs  # noqa: E402
from resources.lib import vfs as vfs_module  # noqa: E402
from resources.lib.vfs import (  # noqa: E402
    DropboxFileSystem,
    XBMCFileSystem,
    ZipFileSystem,
)
from resources.lib.archive import ARCHIVE_ID, ARCHIVE_VERSION, MANIFEST_NAME  # noqa: E402
from resources.lib.extractor import ZipExtractor  # noqa: E402


class LocalFile:
    read_sizes = []

    def __init__(self, path, _mode):
        self.handle = open(path, 'rb')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.handle.close()

    def readBytes(self, size=0):
        self.read_sizes.append(size)
        return self.handle.read(size)


class LocalReadWriteFile:
    def __init__(self, path, mode='r'):
        self.handle = open(path, 'wb' if mode == 'w' else 'rb')

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.handle.close()

    def readBytes(self, size=0):
        return self.handle.read(size)

    def write(self, content):
        self.handle.write(content)
        return True


class XBMCFileSystemTests(unittest.TestCase):
    def test_streamed_copy_reports_real_cumulative_bytes(self):
        original_file = getattr(xbmcvfs, 'File', None)
        original_translate = xbmcvfs.translatePath
        xbmcvfs.File = LocalReadWriteFile
        xbmcvfs.translatePath = lambda path: path
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = os.path.join(directory, 'source.bin')
                destination = os.path.join(directory, 'destination.bin')
                content = b'x' * 17
                with open(source, 'wb') as handle:
                    handle.write(content)
                filesystem = object.__new__(XBMCFileSystem)
                filesystem.COPY_CHUNK_BYTES = 8
                updates = []

                self.assertTrue(filesystem.put_with_progress(
                    source, destination,
                    lambda copied: updates.append(copied) or True))
                with open(destination, 'rb') as handle:
                    self.assertEqual(content, handle.read())
                self.assertEqual([8, 16, 17], updates)
        finally:
            xbmcvfs.translatePath = original_translate
            if original_file is None:
                delattr(xbmcvfs, 'File')
            else:
                xbmcvfs.File = original_file

    def test_streamed_copy_removes_partial_destination_on_cancel(self):
        original_file = getattr(xbmcvfs, 'File', None)
        original_delete = getattr(xbmcvfs, 'delete', None)
        original_translate = xbmcvfs.translatePath
        xbmcvfs.File = LocalReadWriteFile
        xbmcvfs.delete = lambda path: os.remove(path) or True
        xbmcvfs.translatePath = lambda path: path
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = os.path.join(directory, 'source.bin')
                destination = os.path.join(directory, 'destination.bin')
                with open(source, 'wb') as handle:
                    handle.write(b'x' * 17)
                filesystem = object.__new__(XBMCFileSystem)
                filesystem.COPY_CHUNK_BYTES = 8

                self.assertFalse(filesystem.put_with_progress(
                    source, destination, lambda _copied: False))
                self.assertFalse(os.path.exists(destination))
        finally:
            xbmcvfs.translatePath = original_translate
            if original_delete is None:
                delattr(xbmcvfs, 'delete')
            else:
                xbmcvfs.delete = original_delete
            if original_file is None:
                delattr(xbmcvfs, 'File')
            else:
                xbmcvfs.File = original_file


class ZipFileSystemTests(unittest.TestCase):
    def test_put_streams_source_in_bounded_chunks(self):
        original_file = getattr(xbmcvfs, 'File', None)
        xbmcvfs.File = LocalFile
        LocalFile.read_sizes = []
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = os.path.join(directory, 'source.bin')
                archive = os.path.join(directory, 'archive.zip')
                content = b'x' * (1024 * 1024 + 7)
                with open(source, 'wb') as handle:
                    handle.write(content)

                target = ZipFileSystem(archive, 'w')
                self.assertTrue(target.put(source, 'backup/source.bin'))
                target.cleanup()

                with zipfile.ZipFile(archive) as result:
                    self.assertEqual(content, result.read('backup/source.bin'))
                self.assertTrue(LocalFile.read_sizes)
                self.assertEqual({1024 * 1024}, set(LocalFile.read_sizes))
        finally:
            if original_file is None:
                delattr(xbmcvfs, 'File')
            else:
                xbmcvfs.File = original_file

    def test_real_zip_is_preflighted_verified_and_extracted(self):
        payload = b'backup payload'
        document = {
            'archive_id': ARCHIVE_ID,
            'archive_version': ARCHIVE_VERSION,
            'directories': [{
                'name': 'config',
                'path': 'special://home/userdata',
                'files': [{
                    'path': 'settings.xml',
                    'size': len(payload),
                    'sha256': hashlib.sha256(payload).hexdigest(),
                }],
            }],
        }

        class Progress:
            def updateProgress(self, _percent, _message):
                pass

        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, 'backup.zip')
            output = os.path.join(directory, 'output')
            with zipfile.ZipFile(
                    archive, 'w', compression=zipfile.ZIP_DEFLATED) as handle:
                handle.writestr(
                    '202609081200/' + MANIFEST_NAME,
                    json.dumps(document).encode('utf-8'))
                handle.writestr(
                    '202609081200/config/settings.xml', payload)
                handle.writestr('202609081200/.nomedia', b'')

            source = ZipFileSystem(archive, 'r')
            self.assertTrue(ZipExtractor().extract(
                source, output, Progress()))
            source.cleanup()
            with open(os.path.join(
                    output, '202609081200', 'config', 'settings.xml'),
                    'rb') as handle:
                self.assertEqual(payload, handle.read())


class DropboxFileSystemTests(unittest.TestCase):
    def test_get_file_reports_blocking_download_success(self):
        calls = []

        class Client:
            def files_download_to_file(self, destination, source):
                calls.append((destination, source))

        filesystem = object.__new__(DropboxFileSystem)
        filesystem.client = Client()
        self.assertTrue(filesystem.get_file('/backup.zip', '/local.zip'))
        self.assertEqual([('/local.zip', '/backup.zip')], calls)

    def test_exact_chunk_size_uses_complete_single_upload(self):
        calls = []

        class Client:
            def files_upload(self, content, path, mode=None):
                calls.append((content, path, mode))

            def files_upload_session_start(self, _content):
                raise AssertionError('exact boundary must not start a session')

        original_write_mode = vfs_module.WriteMode
        vfs_module.WriteMode = lambda value: value
        try:
            with tempfile.TemporaryDirectory() as directory:
                source = os.path.join(directory, 'boundary.bin')
                with open(source, 'wb') as handle:
                    handle.write(b'abcd')
                target = object.__new__(DropboxFileSystem)
                target.client = Client()
                target.MAX_CHUNK = 4
                self.assertTrue(target.put(source, '/boundary.bin'))
                self.assertEqual(
                    [(b'abcd', '/boundary.bin', 'overwrite')], calls)
        finally:
            vfs_module.WriteMode = original_write_mode


if __name__ == '__main__':
    unittest.main()
