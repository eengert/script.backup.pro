from __future__ import unicode_literals

import unittest

from tests.test_backup_bridge import install_kodi_stubs

install_kodi_stubs()

from resources.lib.utils import diskString  # noqa: E402


class DiskStringTests(unittest.TestCase):
    def test_formats_small_kilobyte_value(self):
        self.assertEqual('512.00KB', diskString(512))

    def test_preserves_exact_1024_boundaries(self):
        self.assertEqual('1024.00KB', diskString(1024))
        self.assertEqual('1024.00MB', diskString(1024 ** 2))
        self.assertEqual('1024.00GB', diskString(1024 ** 3))
        self.assertEqual('1024.00TB', diskString(1024 ** 4))

    def test_formats_typical_gigabyte_and_terabyte_values(self):
        self.assertEqual('1.50GB', diskString(1.5 * 1024 ** 2))
        self.assertEqual('1.50TB', diskString(1.5 * 1024 ** 3))

    def test_caps_overflow_at_terabytes(self):
        self.assertEqual('2048.00TB', diskString(2 * 1024 ** 4))


if __name__ == '__main__':
    unittest.main()
