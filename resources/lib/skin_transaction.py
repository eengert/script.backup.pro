from __future__ import unicode_literals

"""Crash-recoverable local transaction for adapter-owned skin files."""

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from .skin_adapter import (
    AF3_ID,
    MAX_FILE_BYTES,
    MAX_FILE_COUNT,
    MAX_TOTAL_BYTES,
    SkinAdapterError,
    current_managed_paths,
    managed_source_paths,
    read_current_managed_files,
    validate_snapshot_files,
)


JOURNAL_NAME = 'transaction.json'
TRANSACTION_VERSION = 1
ROLLBACK_FILES = 'files'


class SkinTransactionError(RuntimeError):
    pass


def _safe_root(path, create=False):
    root = Path(path)
    try:
        if create:
            root.mkdir(parents=True, exist_ok=True)
        info = root.lstat()
    except OSError as error:
        raise SkinTransactionError('transaction directory is unavailable') from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SkinTransactionError('transaction path is not a safe directory')
    return root


def _fsync_directory(directory):
    if os.name == 'nt':
        return
    try:
        descriptor = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise SkinTransactionError('cannot sync transaction directory') from error


def _safe_parent(root, relative):
    current = root
    parts = PurePosixPath(relative).parts[:-1]
    for part in parts:
        current = current / part
        try:
            current.mkdir()
            _fsync_directory(current.parent)
        except FileExistsError:
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise SkinTransactionError('unsafe transaction parent path')
        except OSError as error:
            raise SkinTransactionError('cannot create transaction directory') from error
    return current


def _reject_symlink_components(root, relative):
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise SkinTransactionError('cannot inspect transaction path') from error
        if stat.S_ISLNK(info.st_mode):
            raise SkinTransactionError('symbolic links are not allowed in transactions')


def _atomic_write_impl(root, relative, data):
    parent = _safe_parent(root, relative)
    destination = root.joinpath(*PurePosixPath(relative).parts)
    _reject_symlink_components(root, relative)
    descriptor = -1
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(prefix='.backup-pro-', dir=str(parent))
        temporary = Path(name)
        with os.fdopen(descriptor, 'wb') as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(destination))
        temporary = None
        _fsync_directory(parent)
    except SkinTransactionError:
        raise
    except OSError as error:
        raise SkinTransactionError('cannot atomically write ' + relative) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def _atomic_write(root, relative, data):
    """Failure-injection seam for tests."""
    _atomic_write_impl(root, relative, data)


def _journal_bytes(journal):
    return json.dumps(
        journal, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _write_journal(directory, journal, status):
    journal['status'] = status
    _atomic_write_impl(directory, JOURNAL_NAME, _journal_bytes(journal))


def _read_journal(directory):
    path = directory / JOURNAL_NAME
    if not path.exists() or path.is_symlink() or not path.is_file():
        raise SkinTransactionError('rollback journal is unavailable')
    try:
        with path.open('rb') as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise SkinTransactionError('rollback journal is too large')
        journal = json.loads(data.decode('utf-8'))
    except SkinTransactionError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise SkinTransactionError('rollback journal is invalid') from error
    if not isinstance(journal, dict) or journal.get('version') != TRANSACTION_VERSION:
        raise SkinTransactionError('rollback journal is invalid')
    return journal


def _remove_files(root, paths):
    for relative in sorted(set(paths), reverse=True):
        managed_source_paths({relative: b''}, AF3_ID)
        path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise SkinTransactionError('cannot inspect managed restore path') from error
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise SkinTransactionError('refusing to remove unsafe managed path')
        try:
            path.unlink()
            _fsync_directory(path.parent)
        except OSError as error:
            raise SkinTransactionError('cannot remove managed restore file') from error


def _restore_previous(profile, directory, journal):
    entries = journal.get('previous_entries')
    targets = journal.get('target_paths')
    if (not isinstance(entries, list) or not isinstance(targets, list)
            or len(entries) > MAX_FILE_COUNT or len(targets) > MAX_FILE_COUNT):
        raise SkinTransactionError('invalid rollback journal entries')
    previous = {}
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256'}:
            raise SkinTransactionError('invalid rollback entry')
        relative = entry.get('path')
        managed_source_paths({relative: b''}, AF3_ID)
        size = entry.get('size')
        checksum = entry.get('sha256')
        if (not isinstance(size, int) or isinstance(size, bool)
                or size < 0 or size > MAX_FILE_BYTES
                or not isinstance(checksum, str)
                or not re.fullmatch(r'[0-9a-f]{64}', checksum)):
            raise SkinTransactionError('invalid rollback entry metadata')
        snapshot = directory.joinpath(
            *PurePosixPath(ROLLBACK_FILES + '/' + relative).parts)
        _reject_symlink_components(directory, ROLLBACK_FILES + '/' + relative)
        try:
            with snapshot.open('rb') as stream:
                data = stream.read(size + 1)
        except OSError as error:
            raise SkinTransactionError('rollback snapshot is unavailable') from error
        if (len(data) != size
                or hashlib.sha256(data).hexdigest() != checksum):
            raise SkinTransactionError('rollback snapshot verification failed')
        total += len(data)
        if total > MAX_TOTAL_BYTES or relative in previous:
            raise SkinTransactionError('invalid rollback snapshot')
        previous[relative] = data
    for relative in targets:
        managed_source_paths({relative: b''}, AF3_ID)
    current = current_managed_paths(profile, AF3_ID)
    _remove_files(profile, set(current) | set(targets))
    for relative, data in sorted(previous.items()):
        _atomic_write_impl(profile, relative, data)


def apply_skin_snapshot(profile_path, files, rollback_root):
    """Apply validated AF3 files and retain an exact durable rollback."""
    try:
        checked = validate_snapshot_files(files, AF3_ID)
        profile = _safe_root(profile_path)
        existing = read_current_managed_files(profile, AF3_ID)
    except (SkinAdapterError, SkinTransactionError) as error:
        raise SkinTransactionError(str(error)) from error
    if pending_skin_transactions(profile, rollback_root):
        raise SkinTransactionError(
            'resolve the pending skin restore before starting another operation')
    rollback_base = _safe_root(rollback_root, create=True)
    try:
        directory = Path(tempfile.mkdtemp(prefix=AF3_ID + '-', dir=str(rollback_base)))
    except OSError as error:
        raise SkinTransactionError('cannot create rollback directory') from error
    journal = {
        'version': TRANSACTION_VERSION,
        'transaction_id': str(uuid.uuid4()),
        'profile_path': os.path.abspath(str(profile)),
        'skin_id': AF3_ID,
        'previous_entries': [],
        'target_paths': sorted(checked),
    }
    _write_journal(directory, journal, 'snapshotting')
    entries = []
    try:
        for relative, data in sorted(existing.items()):
            _atomic_write_impl(
                directory, ROLLBACK_FILES + '/' + relative, data)
            entries.append({
                'path': relative,
                'size': len(data),
                'sha256': hashlib.sha256(data).hexdigest(),
            })
    except Exception as error:
        try:
            _write_journal(directory, journal, 'rolled_back')
        except Exception:
            pass
        raise SkinTransactionError(
            'rollback snapshot failed; profile files were unchanged') from error
    journal['previous_entries'] = entries
    _write_journal(directory, journal, 'prepared')
    try:
        _write_journal(directory, journal, 'applying')
        _remove_files(profile, set(existing) - set(checked))
        for relative, data in sorted(checked.items()):
            _atomic_write(profile, relative, data)
        _write_journal(directory, journal, 'complete')
    except Exception as error:
        try:
            _restore_previous(profile, directory, journal)
            _write_journal(directory, journal, 'rolled_back')
        except Exception as rollback_error:
            try:
                _write_journal(directory, journal, 'rollback_failed')
            except Exception:
                pass
            raise SkinTransactionError(
                'restore failed and rollback failed: ' + str(rollback_error)) from error
        raise SkinTransactionError('restore failed; previous files were restored') from error
    return str(directory)


def rollback_skin_transaction(profile_path, rollback_directory):
    profile = _safe_root(profile_path)
    directory = _safe_root(rollback_directory)
    journal = _read_journal(directory)
    if journal.get('profile_path') != os.path.abspath(str(profile)):
        raise SkinTransactionError('rollback belongs to another profile')
    if journal.get('skin_id') != AF3_ID or journal.get('status') != 'complete':
        raise SkinTransactionError('rollback transaction is not complete')
    try:
        _restore_previous(profile, directory, journal)
        _write_journal(directory, journal, 'rolled_back')
    except Exception as error:
        try:
            _write_journal(directory, journal, 'rollback_failed')
        except Exception:
            pass
        raise SkinTransactionError('rollback failed') from error
    return str(directory)


def recover_pending_transactions(profile_path, rollback_root):
    """Roll back every interrupted transaction owned by this exact profile."""
    profile = _safe_root(profile_path)
    base = Path(rollback_root)
    if not base.exists():
        return []
    base = _safe_root(base)
    recovered = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        journal_path = directory / JOURNAL_NAME
        if not journal_path.exists():
            continue
        journal = _read_journal(directory)
        if journal.get('profile_path') != os.path.abspath(str(profile)):
            continue
        status = journal.get('status')
        if status in ('complete', 'rolled_back'):
            continue
        if status == 'snapshotting':
            _write_journal(directory, journal, 'rolled_back')
        elif status in ('prepared', 'applying', 'rollback_failed'):
            _restore_previous(profile, directory, journal)
            _write_journal(directory, journal, 'rolled_back')
        else:
            raise SkinTransactionError('invalid pending transaction status')
        recovered.append(str(directory))
    return recovered


def pending_skin_transactions(profile_path, rollback_root):
    """List unresolved journals without changing profile or transaction data."""
    profile = _safe_root(profile_path)
    base = Path(rollback_root)
    if not base.exists():
        return []
    base = _safe_root(base)
    pending = []
    for directory in sorted(base.iterdir()):
        if not directory.is_dir() or directory.is_symlink():
            continue
        if not (directory / JOURNAL_NAME).exists():
            continue
        journal = _read_journal(directory)
        if journal.get('profile_path') != os.path.abspath(str(profile)):
            continue
        status = journal.get('status')
        if status not in ('snapshotting', 'prepared', 'applying',
                          'rollback_failed', 'complete', 'rolled_back'):
            raise SkinTransactionError('invalid transaction status')
        if status in ('snapshotting', 'prepared', 'applying', 'rollback_failed'):
            pending.append(str(directory))
    return pending
