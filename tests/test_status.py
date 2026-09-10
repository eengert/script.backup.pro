from __future__ import unicode_literals

import unittest

from resources.lib.status import describe_status


def base_facts(**overrides):
    facts = {
        'last_backup': {'known': False},
        'remote': {'configured': False},
        'recovery_pending': False,
        'scheduler': {'enabled': False},
    }
    facts.update(overrides)
    return facts


class DescribeStatusTests(unittest.TestCase):
    def test_reports_unknown_last_backup_when_no_history_exists(self):
        lines = describe_status(base_facts())
        self.assertIn(('last_backup_unknown', None), lines)

    def test_reports_known_last_backup_label(self):
        lines = describe_status(base_facts(
            last_backup={'known': True, 'label': '2026-09-10 06:00'}))
        self.assertIn(
            ('last_backup_known', '2026-09-10 06:00'), lines)

    def test_reports_remote_not_configured(self):
        lines = describe_status(base_facts())
        self.assertIn(('remote_not_configured', None), lines)

    def test_reports_configured_remote_label(self):
        lines = describe_status(base_facts(
            remote={'configured': True, 'label': 'Dropbox'}))
        self.assertIn(('remote_configured', 'Dropbox'), lines)

    def test_reports_recovery_clear_by_default(self):
        lines = describe_status(base_facts())
        self.assertIn(('recovery_clear', None), lines)
        self.assertNotIn(('recovery_pending', None), lines)

    def test_pending_recovery_is_reported_and_moved_to_the_front(self):
        lines = describe_status(base_facts(recovery_pending=True))
        self.assertEqual(lines[0], ('recovery_pending', None))
        self.assertNotIn(('recovery_clear', None), lines)

    def test_reports_scheduler_disabled(self):
        lines = describe_status(base_facts())
        self.assertIn(('scheduler_disabled', None), lines)

    def test_reports_scheduler_enabled_with_next_run(self):
        lines = describe_status(base_facts(
            scheduler={'enabled': True, 'next_run_label': '2026-09-11 03:00'}))
        self.assertIn(
            ('scheduler_enabled_next', '2026-09-11 03:00'), lines)

    def test_reports_scheduler_enabled_without_known_next_run(self):
        lines = describe_status(base_facts(scheduler={'enabled': True}))
        self.assertIn(('scheduler_enabled_unknown', None), lines)

    def test_full_order_when_nothing_pending(self):
        lines = describe_status(base_facts())
        self.assertEqual(
            [key for key, _detail in lines],
            ['last_backup_unknown', 'remote_not_configured',
             'recovery_clear', 'scheduler_disabled'])

    def test_recovery_pending_only_reorders_that_line(self):
        lines = describe_status(base_facts(
            last_backup={'known': True, 'label': 'X'},
            remote={'configured': True, 'label': 'Y'},
            recovery_pending=True,
            scheduler={'enabled': True, 'next_run_label': 'Z'}))
        self.assertEqual(
            [key for key, _detail in lines],
            ['recovery_pending', 'last_backup_known', 'remote_configured',
             'scheduler_enabled_next'])


if __name__ == '__main__':
    unittest.main()
