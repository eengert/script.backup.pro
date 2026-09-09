from __future__ import unicode_literals

import unittest

from resources.lib.skin_recovery import (
    SkinRecoveryDecisionError,
    describe_recovery_action,
)


class SkinRecoveryTests(unittest.TestCase):
    def test_none_action_means_nothing_pending(self):
        self.assertEqual(
            {'kind': 'none'},
            describe_recovery_action({'action': 'none', 'phase': None,
                                      'status': None}))

    def test_resume_and_restage_offer_continue_and_rollback(self):
        for action in ('resume_staging', 'restage_settings'):
            result = describe_recovery_action({'action': action})
            self.assertEqual('choice', result['kind'])
            self.assertTrue(result['continue_available'])
            self.assertTrue(result['rollback_available'])

    def test_finish_rebuild_offers_continue_and_rollback(self):
        result = describe_recovery_action({'action': 'finish_rebuild'})
        self.assertEqual('choice', result['kind'])
        self.assertTrue(result['continue_available'])
        self.assertTrue(result['rollback_available'])

    def test_rollback_transaction_offers_rollback_only(self):
        result = describe_recovery_action({'action': 'rollback_transaction'})
        self.assertEqual('choice', result['kind'])
        self.assertFalse(result['continue_available'])
        self.assertTrue(result['rollback_available'])

    def test_finish_rollback_rebuild_offers_rollback_only(self):
        result = describe_recovery_action(
            {'action': 'finish_rollback_rebuild'})
        self.assertEqual('choice', result['kind'])
        self.assertFalse(result['continue_available'])
        self.assertTrue(result['rollback_available'])

    def test_unsafe_actions_are_diagnostic_only(self):
        for action in ('restart_preflight', 'recover_unlinked_transaction'):
            result = describe_recovery_action({'action': action})
            self.assertEqual('diagnostic', result['kind'])
            self.assertIn(action, result['reason'])

    def test_unrecognized_action_raises(self):
        with self.assertRaises(SkinRecoveryDecisionError):
            describe_recovery_action({'action': 'made_up_action'})

    def test_missing_action_key_raises(self):
        with self.assertRaises(SkinRecoveryDecisionError):
            describe_recovery_action({})

    def test_non_dict_input_raises(self):
        with self.assertRaises(SkinRecoveryDecisionError):
            describe_recovery_action(None)


if __name__ == '__main__':
    unittest.main()
