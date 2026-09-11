import os
import re
import unittest

from resources.lib.help_content import HELP_SECTIONS, build_help_text

STRINGS_PO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'resources', 'language', 'resource.language.en_gb', 'strings.po')


def _real_string_ids():
    with open(STRINGS_PO, 'r', encoding='utf-8') as handle:
        contents = handle.read()
    return {int(match) for match in re.findall(r'msgctxt "#(\d+)"', contents)}


class HelpContentTests(unittest.TestCase):
    def test_every_referenced_string_id_exists_in_strings_po(self):
        # catches a typo'd or renumbered id that would otherwise only
        # surface as a blank line (or Kodi's raw numeric fallback) the
        # next time someone actually opens Help in Kodi.
        real_ids = _real_string_ids()
        for header_id, body_ids in HELP_SECTIONS:
            self.assertIn(header_id, real_ids,
                          'missing header string #%d' % header_id)
            for body_id in body_ids:
                self.assertIn(body_id, real_ids,
                              'missing body string #%d' % body_id)

    def test_covers_every_required_topic(self):
        # one header per topic Task D's brief calls out at minimum:
        # Overview, What Gets Backed Up, Compression, Destination /
        # Storage, Restore, Scheduling, Status, Warnings and Failures,
        # Safety / Limitations.
        self.assertEqual(9, len(HELP_SECTIONS))

    def test_no_duplicate_string_ids_across_sections(self):
        seen = []
        for header_id, body_ids in HELP_SECTIONS:
            seen.append(header_id)
            seen.extend(body_ids)
        self.assertEqual(len(seen), len(set(seen)))

    def test_every_section_has_a_header_and_at_least_one_body_string(self):
        for header_id, body_ids in HELP_SECTIONS:
            self.assertIsInstance(header_id, int)
            self.assertTrue(body_ids)

    def test_build_help_text_orders_header_before_its_own_body(self):
        resolve = lambda string_id: 'text-%d' % string_id
        text = build_help_text(resolve)
        for header_id, body_ids in HELP_SECTIONS:
            header_text = 'text-%d' % header_id
            self.assertIn(header_text, text)
            header_index = text.index(header_text)
            for body_id in body_ids:
                body_text = 'text-%d' % body_id
                self.assertIn(body_text, text)
                self.assertGreater(text.index(body_text), header_index)

    def test_build_help_text_separates_sections_and_resolves_every_id(self):
        resolved = []

        def resolve(string_id):
            resolved.append(string_id)
            return 'S%d' % string_id

        text = build_help_text(resolve)
        expected_ids = []
        for header_id, body_ids in HELP_SECTIONS:
            expected_ids.append(header_id)
            expected_ids.extend(body_ids)
        self.assertEqual(expected_ids, resolved)
        # sections are visibly separated (a blank line within a
        # section's own header+body, a larger gap between sections)
        self.assertIn('\n\n\n', text)

    def test_compression_section_explains_the_shared_destination(self):
        # regression guard, 2026-09-11: Eric's complaint was that
        # enabling Compress Archives appeared to expose/require a
        # separate destination. Help must say plainly that it doesn't.
        compression_section = next(
            body_ids for header_id, body_ids in HELP_SECTIONS
            if header_id == 30207)
        self.assertIn(30228, compression_section)


if __name__ == '__main__':
    unittest.main()
