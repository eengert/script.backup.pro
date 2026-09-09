from __future__ import unicode_literals

"""Atomic local persistence for the AF3 restore recovery record."""

import os
import tempfile
from pathlib import Path

from .skin_restore import (
    PENDING_MAX_BYTES,
    SkinRestoreError,
    dump_pending_restore,
    load_pending_restore,
)


class SkinStateError(RuntimeError):
    pass


def _parent_for(path):
    target = Path(path)
    parent = target.parent
    try:
        info = parent.lstat()
    except OSError as error:
        raise SkinStateError('pending-state directory is unavailable') from error
    if parent.is_symlink() or not parent.is_dir():
        raise SkinStateError('pending-state directory is unsafe')
    return target, parent


def _reject_unsafe_target(target):
    try:
        info = target.lstat()
    except FileNotFoundError:
        return
    except OSError as error:
        raise SkinStateError('cannot inspect pending restore state') from error
    if target.is_symlink() or not target.is_file():
        raise SkinStateError('pending restore state path is unsafe')


def _sync_directory(directory):
    if os.name == 'nt':
        return
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise SkinStateError('cannot sync pending-state directory') from error


def write_pending_state(path, record):
    """Atomically persist a validated record and return its canonical form."""
    try:
        data = dump_pending_restore(record)
    except SkinRestoreError as error:
        raise SkinStateError(str(error)) from error
    target, parent = _parent_for(path)
    _reject_unsafe_target(target)
    descriptor = -1
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix='.backup-pro-pending-', dir=str(parent))
        temporary = Path(name)
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _reject_unsafe_target(target)
        os.replace(str(temporary), str(target))
        temporary = None
        _sync_directory(parent)
    except SkinStateError:
        raise
    except OSError as error:
        raise SkinStateError('cannot persist pending restore state') from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    return load_pending_restore(data)


def read_pending_state(path):
    """Return validated pending state, or None when no state exists."""
    target, _parent = _parent_for(path)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise SkinStateError('cannot inspect pending restore state') from error
    if target.is_symlink() or not target.is_file():
        raise SkinStateError('pending restore state path is unsafe')
    try:
        with target.open('rb') as stream:
            data = stream.read(PENDING_MAX_BYTES + 1)
        return load_pending_restore(data)
    except SkinRestoreError as error:
        raise SkinStateError(str(error)) from error
    except OSError as error:
        raise SkinStateError('cannot read pending restore state') from error


def clear_pending_state(path, expected_record):
    """Remove only the exact validated state the caller has verified complete."""
    expected = read_pending_state(path)
    if expected is None:
        raise SkinStateError('pending restore state is unavailable')
    try:
        canonical_expected = load_pending_restore(
            dump_pending_restore(expected_record))
    except SkinRestoreError as error:
        raise SkinStateError(str(error)) from error
    if expected != canonical_expected:
        raise SkinStateError('pending restore state changed before completion')
    target, parent = _parent_for(path)
    _reject_unsafe_target(target)
    try:
        target.unlink()
        _sync_directory(parent)
    except OSError as error:
        raise SkinStateError('cannot clear pending restore state') from error
