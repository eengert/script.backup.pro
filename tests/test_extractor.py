from __future__ import unicode_literals

import hashlib
import io
import json
import unittest

from tests.test_backup_bridge import install_kodi_stubs

install_kodi_stubs()

from resources.lib.extractor import ZipExtractor  # noqa: E402
from resources.lib.archive import ARCHIVE_ID, ARCHIVE_VERSION, MANIFEST_NAME  # noqa: E402
from tests.test_archive import Entry  # noqa: E402


class Progress:
    def __init__(self):
        self.updates = []

    def updateProgress(self, percent, message):
        self.updates.append((percent, message))


class FakeZip:
    def __init__(self, entries, manifest_data=b'', payloads=None):
        self.entries = entries
        self.manifest_data = manifest_data
        self.payloads = payloads or {}
        self.extracted = []

    def listFiles(self):
        return self.entries

    def extract(self, entry, location):
        self.extracted.append((entry.filename, location))

    def readFile(self, _entry):
        return self.manifest_data

    def openFile(self, entry):
        return io.BytesIO(self.payloads[entry.filename])


def valid_archive():
    document = {
        'archive_id': ARCHIVE_ID,
        'archive_version': ARCHIVE_VERSION,
        'directories': [{
            'name': 'addon_data',
            'path': 'special://home/userdata/addon_data',
            'files': [{
                'path': 'example/settings.xml',
                'size': 3,
                'sha256': hashlib.sha256(b'abc').hexdigest(),
            }],
        }],
    }
    encoded = json.dumps(document).encode('utf-8')
    return FakeZip([
        Entry('202609081200/' + MANIFEST_NAME, size=len(encoded)),
        Entry('202609081200/.nomedia'),
        Entry('202609081200/addon_data/example/settings.xml', size=3),
    ], encoded, {
        '202609081200/addon_data/example/settings.xml': b'abc',
    })


class ExtractorTests(unittest.TestCase):
    def test_validates_all_members_before_extracting_anything(self):
        archive = FakeZip([Entry('safe.txt'), Entry('../escape')])
        self.assertFalse(ZipExtractor().extract(archive, '/staging', Progress()))
        self.assertEqual([], archive.extracted)

    def test_extracts_valid_members_after_preflight(self):
        archive = valid_archive()
        self.assertTrue(ZipExtractor().extract(archive, '/staging', Progress()))
        self.assertEqual(3, len(archive.extracted))

    def test_rejects_archive_without_backup_pro_manifest(self):
        archive = FakeZip([Entry('202609081200/file.txt')])
        self.assertFalse(ZipExtractor().extract(archive, '/staging', Progress()))
        self.assertEqual([], archive.extracted)

    def test_rejects_same_size_payload_with_wrong_checksum(self):
        archive = valid_archive()
        archive.payloads[
            '202609081200/addon_data/example/settings.xml'] = b'xyz'
        self.assertFalse(ZipExtractor().extract(archive, '/staging', Progress()))
        self.assertEqual([], archive.extracted)


if __name__ == '__main__':
    unittest.main()
