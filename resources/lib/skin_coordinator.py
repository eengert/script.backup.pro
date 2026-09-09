from __future__ import unicode_literals

"""Crash-ordered AF3 restore staging independent of Kodi presentation code."""

import os
import stat
import hashlib
from contextlib import contextmanager

from .skin_adapter import AF3_ID, SKIN_VARIABLES_ID, validate_snapshot_files
from .skin_restore import (
    SETTINGS_PATH,
    advance_pending_restore,
    build_pending_restore,
)
from .skin_state import (
    clear_pending_state,
    read_pending_state,
    write_pending_state,
)
from .skin_transaction import (
    apply_skin_snapshot,
    pending_skin_transactions,
    read_current_managed_files,
    skin_transaction_status,
)


class SkinCoordinatorError(RuntimeError):
    pass


@contextmanager
def _operation_lock(pending_path):
    """Hold a non-blocking process lock for one profile restore operation."""
    lock_path = os.path.abspath(str(pending_path)) + '.lock'
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise SkinCoordinatorError('skin restore operation lock is unavailable') from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise SkinCoordinatorError('skin restore operation lock is unsafe')
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b'0')
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as error:
            raise SkinCoordinatorError(
                'another skin restore operation is already running') from error
        yield
    finally:
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except (OSError, IOError):
            pass
        os.close(descriptor)


def _call(host, name, *args):
    method = getattr(host, name, None)
    if not callable(method):
        raise SkinCoordinatorError('skin restore host is missing ' + name)
    return method(*args)


def _progress(host, percent, message):
    method = getattr(host, 'progress', None)
    if callable(method):
        try:
            method(percent, message)
        except Exception:
            # Presentation failure must not change restore transaction state.
            pass


def stage_skin_restore(manifest, files, restore_point, profile_path,
                       rollback_root, pending_path, host):
    """Stage verified AF3 data and stop at a durable pre-activation phase."""
    with _operation_lock(pending_path):
        return _stage_skin_restore_locked(
            manifest, files, restore_point, profile_path, rollback_root,
            pending_path, host)


def _stage_skin_restore_locked(manifest, files, restore_point, profile_path,
                               rollback_root, pending_path, host):
    if read_pending_state(pending_path) is not None:
        raise SkinCoordinatorError('finish or recover the pending skin restore first')
    if pending_skin_transactions(profile_path, rollback_root):
        raise SkinCoordinatorError(
            'recover the interrupted skin transaction before restoring')

    checked_files = validate_snapshot_files(files, AF3_ID)
    private_files = {path: bytes(data) for path, data in checked_files.items()}
    pending = build_pending_restore(manifest, private_files, restore_point)
    _call(host, 'ensure_dependencies', AF3_ID, SKIN_VARIABLES_ID)
    pending = write_pending_state(pending_path, pending)
    _progress(host, 10, 'Stopping playback')
    _call(host, 'stop_playback')
    if _call(host, 'is_playing'):
        raise SkinCoordinatorError('playback did not stop before skin restore')
    _progress(host, 20, 'Activating a safe skin')
    _call(host, 'ensure_inactive', AF3_ID)
    if _call(host, 'active_skin') == AF3_ID:
        raise SkinCoordinatorError('target skin is still active')

    # Revalidate the private payload at the last safe point. Host callbacks may
    # mutate caller-owned objects, but they cannot change this frozen mapping.
    if build_pending_restore(
            manifest, private_files, restore_point) != pending:
        raise SkinCoordinatorError('skin restore payload changed before apply')

    state = {'pending': pending}

    def transaction_prepared(directory, transaction_id):
        updated = advance_pending_restore(
            state['pending'], 'transaction_prepared', directory,
            transaction_id)
        state['pending'] = write_pending_state(pending_path, updated)

    _progress(host, 40, 'Saving rollback and applying AF3 files')
    rollback = apply_skin_snapshot(
        profile_path, private_files, rollback_root,
        before_apply=transaction_prepared)
    pending = state['pending']
    if rollback != pending['rollback']:
        raise SkinCoordinatorError('skin transaction returned another rollback')
    status = skin_transaction_status(
        profile_path, rollback_root, rollback, pending['transaction_id'])
    if status != 'complete':
        raise SkinCoordinatorError('skin transaction did not complete')
    pending = write_pending_state(
        pending_path,
        advance_pending_restore(pending, 'files_applied'))

    _progress(host, 70, 'Staging AF3 settings through Kodi')
    _call(host, 'stage_settings', AF3_ID, private_files[SETTINGS_PATH],
          pending['skin_settings'])

    pending = write_pending_state(
        pending_path,
        advance_pending_restore(pending, 'rebuild'))
    _progress(host, 100, 'AF3 restore staged for activation')
    return pending


def inspect_pending_restore(profile_path, rollback_root, pending_path):
    """Describe the one safe next action without mutating recovery state."""
    pending = read_pending_state(pending_path)
    unresolved = pending_skin_transactions(profile_path, rollback_root)
    if pending is None:
        if unresolved:
            raise SkinCoordinatorError('orphaned skin transaction requires recovery')
        return {'action': 'none', 'phase': None, 'status': None}

    phase = pending['phase']
    if phase == 'prepared':
        if len(unresolved) > 1:
            raise SkinCoordinatorError(
                'multiple unlinked skin transactions require recovery')
        return {
            'action': ('recover_unlinked_transaction'
                       if unresolved else 'restart_preflight'),
            'phase': phase,
            'status': None,
        }

    status = skin_transaction_status(
        profile_path, rollback_root, pending['rollback'],
        pending['transaction_id'])
    unresolved_paths = {os.path.abspath(path) for path in unresolved}
    linked = os.path.abspath(pending['rollback'])
    if unresolved_paths - {linked}:
        raise SkinCoordinatorError(
            'another unresolved skin transaction requires recovery')
    if status in ('snapshotting', 'prepared', 'applying', 'rollback_failed'):
        if linked not in unresolved_paths:
            raise SkinCoordinatorError(
                'pending transaction was not reported as unresolved')
    elif linked in unresolved_paths:
        raise SkinCoordinatorError(
            'completed transaction was reported as unresolved')
    actions = {
        'transaction_prepared': {
            'prepared': 'rollback_transaction',
            'applying': 'rollback_transaction',
            'rollback_failed': 'rollback_transaction',
            'complete': 'resume_staging',
            'rolled_back': 'restart_preflight',
        },
        'files_applied': {
            'complete': 'restage_settings',
            'rolled_back': 'restart_preflight',
        },
        'rebuild': {
            'complete': 'finish_rebuild',
        },
    }
    action = actions.get(phase, {}).get(status)
    if action is None:
        raise SkinCoordinatorError(
            'pending restore and transaction phases are inconsistent')
    return {'action': action, 'phase': phase, 'status': status}


def _verify_helper_sources(profile_path, expected):
    files = read_current_managed_files(profile_path, AF3_ID)
    actual = {
        path: hashlib.sha256(data).hexdigest()
        for path, data in files.items() if path != SETTINGS_PATH
    }
    if actual != expected:
        raise SkinCoordinatorError(
            'restored AF3 helper sources did not remain applied')


def finish_skin_restore(profile_path, rollback_root, pending_path, host):
    """Activate, rebuild and verify a staged AF3 restore before clearing it."""
    with _operation_lock(pending_path):
        action = inspect_pending_restore(
            profile_path, rollback_root, pending_path)
        if action['action'] != 'finish_rebuild':
            raise SkinCoordinatorError(
                'staged skin restore is not ready for rebuild')
        pending = read_pending_state(pending_path)
        _call(host, 'ensure_dependencies', AF3_ID, SKIN_VARIABLES_ID)
        _progress(host, 10, 'Activating restored Arctic Fuse 3')
        _call(host, 'activate_skin', AF3_ID)
        if _call(host, 'active_skin') != AF3_ID:
            raise SkinCoordinatorError('restored AF3 skin is not active')

        _progress(host, 30, 'Verifying restored AF3 settings')
        _call(host, 'verify_loaded_settings', AF3_ID,
              pending['skin_settings'])
        _progress(host, 45, 'Applying restored AF3 appearance')
        _call(host, 'apply_appearance',
              pending['skin_config']['appearance'])
        _progress(host, 60, 'Rebuilding AF3 menus and widgets')
        _call(host, 'rebuild_skin', AF3_ID, pending)

        _progress(host, 90, 'Verifying completed AF3 restore')
        if _call(host, 'active_skin') != AF3_ID:
            raise SkinCoordinatorError('AF3 changed during restore rebuild')
        _call(host, 'verify_loaded_settings', AF3_ID,
              pending['skin_settings'])
        _verify_helper_sources(profile_path, pending['helper_hashes'])
        clear_pending_state(pending_path, pending)
        _progress(host, 100, 'AF3 restore complete')
        return {
            'skin_id': AF3_ID,
            'setting_count': len(pending['skin_settings']),
            'helper_file_count': len(pending['helper_hashes']),
            'appearance_count': len(pending['skin_config']['appearance']),
        }
