from __future__ import unicode_literals

"""Pure AF3 recovery-menu decisions, independent of Kodi presentation code.

This module turns `skin_coordinator.inspect_pending_restore()`'s action into
the plain-language choices a Kodi front end should show. It never touches
Kodi, the filesystem, or pending state; it only classifies an already-computed
action so the same decision can be unit tested without any Kodi stubs.
"""

CONTINUE_ROLLBACK_ACTIONS = frozenset((
    'resume_staging', 'restage_settings', 'finish_rebuild',
))
ROLLBACK_ONLY_ACTIONS = frozenset((
    'rollback_transaction', 'finish_rollback_rebuild',
))
DISCARD_ACTIONS = frozenset((
    'restart_preflight', 'recover_unlinked_transaction',
))


class SkinRecoveryDecisionError(RuntimeError):
    pass


def describe_recovery_action(inspection):
    """Classify one `inspect_pending_restore()` result for presentation.

    Returns a dict with a `kind` of:
      - 'none': nothing pending; unrelated actions may proceed.
      - 'choice': a pending restore has at least one safe next action.
        `continue_available` and `rollback_available` say which of
        "continue the imported configuration" / "restore the previous
        configuration" may be offered.
      - 'discard': the restore never mutated a profile file (its pending
        record is still in the 'prepared' phase). The only safe action is
        to discard it -- rolling back any unlinked, never-completed
        transaction first if one exists -- and let the user restart the
        restore from the archive. There is nothing to "continue" (no file
        bytes are held in the pending record, only hashes) and nothing to
        "roll back to" (nothing was ever applied).
      - 'diagnostic': the state is genuinely inconsistent/corrupt and no
        safe coordinator primitive exists for it. Only a diagnostic may be
        shown; pending/rollback state must not be touched.
    """
    if not isinstance(inspection, dict) or 'action' not in inspection:
        raise SkinRecoveryDecisionError('recovery inspection result is invalid')
    action = inspection['action']
    if action == 'none':
        return {'kind': 'none'}
    if action in CONTINUE_ROLLBACK_ACTIONS:
        return {
            'kind': 'choice',
            'action': action,
            'continue_available': True,
            'rollback_available': True,
        }
    if action in ROLLBACK_ONLY_ACTIONS:
        return {
            'kind': 'choice',
            'action': action,
            'continue_available': False,
            'rollback_available': True,
        }
    if action in DISCARD_ACTIONS:
        return {'kind': 'discard', 'action': action}
    raise SkinRecoveryDecisionError(
        'unrecognized AF3 recovery action: {}'.format(action))
