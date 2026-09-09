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
UNSAFE_ACTIONS = frozenset((
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
      - 'diagnostic': the safe coordinator primitives for this state do not
        exist yet (or the state is otherwise unrecoverable here). Only a
        diagnostic may be shown; pending/rollback state must not be touched.
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
    if action in UNSAFE_ACTIONS:
        return {
            'kind': 'diagnostic',
            'action': action,
            'reason': (
                'Arctic Fuse 3 restore recovery requires "{}", which has no '
                'safe automatic recovery yet. No files or settings were '
                'changed.'.format(action)),
        }
    raise SkinRecoveryDecisionError(
        'unrecognized AF3 recovery action: {}'.format(action))
