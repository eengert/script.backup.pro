from __future__ import unicode_literals

"""Verified AF3 restore preflight and durable-state data model."""

import hashlib

from .archive import ARCHIVE_ID, ARCHIVE_VERSION, ArchiveValidationError, validate_manifest
from .skin_adapter import (
    AF3_ID,
    SkinAdapterError,
    checked_skin_setting_values,
    skin_setting_values,
    snapshot_fingerprint,
    validate_snapshot_files,
    validate_snapshot_manifest,
)


PENDING_VERSION = 1
SETTINGS_PATH = 'addon_data/{}/settings.xml'.format(AF3_ID)


class SkinRestoreError(RuntimeError):
    pass


def _skin_group(manifest):
    try:
        validated = validate_manifest(manifest)
    except ArchiveValidationError as error:
        raise SkinRestoreError(str(error)) from error
    matches = [group for group in validated['directories']
               if group['name'].casefold() == 'skin_config']
    if len(matches) != 1:
        raise SkinRestoreError('archive has no unique AF3 configuration group')
    return validated, matches[0]


def skin_restore_preview(manifest):
    """Return bounded, user-facing AF3 restore facts from a valid manifest."""
    validated, group = _skin_group(manifest)
    metadata = validated['skin_config']
    return {
        'skin_id': metadata['skin_id'],
        'skin_version': metadata['skin_version'],
        'helper_id': metadata['helper_id'],
        'helper_version': metadata['helper_version'],
        'source_device': metadata['source_device'],
        'source_profile': metadata['source_profile'],
        'created_utc': validated.get('created_utc', ''),
        'setting_count': metadata['setting_count'],
        'helper_file_count': metadata['helper_file_count'],
        'total_bytes': sum(item['size'] for item in group['files']),
        'rollback_required': True,
    }


def load_skin_snapshot(manifest, read_file, check_cancel=None):
    """Read and verify every AF3 payload byte before restore mutation."""
    if not callable(read_file):
        raise SkinRestoreError('skin snapshot reader is unavailable')
    validated, group = _skin_group(manifest)
    files = {}
    for item in group['files']:
        if check_cancel and check_cancel():
            raise SkinRestoreError('skin restore preflight cancelled')
        archive_path = group['name'] + '/' + item['path']
        try:
            data = read_file(archive_path)
        except Exception as error:
            raise SkinRestoreError(
                'unable to read AF3 snapshot file: ' + item['path']) from error
        if not isinstance(data, bytes):
            raise SkinRestoreError('AF3 snapshot reader returned non-byte data')
        if (len(data) != item['size']
                or hashlib.sha256(data).hexdigest() != item['sha256']):
            raise SkinRestoreError(
                'AF3 snapshot payload failed verification: ' + item['path'])
        files[item['path']] = data
    try:
        checked = validate_snapshot_files(files, AF3_ID)
    except SkinAdapterError as error:
        raise SkinRestoreError(str(error)) from error
    if snapshot_fingerprint(checked) != validated['skin_config']['fingerprint']:
        raise SkinRestoreError('AF3 snapshot fingerprint does not match its payload')
    if (len(skin_setting_values(checked[SETTINGS_PATH])) !=
            validated['skin_config']['setting_count']):
        raise SkinRestoreError('AF3 setting count does not match its payload')
    return checked


def build_pending_restore(manifest, files, restore_point):
    """Create the complete validated record that must precede mutation."""
    validated, group = _skin_group(manifest)
    try:
        checked = validate_snapshot_files(files, AF3_ID)
    except SkinAdapterError as error:
        raise SkinRestoreError(str(error)) from error
    if (not isinstance(restore_point, str) or not restore_point
            or len(restore_point) > 1024 or '\x00' in restore_point):
        raise SkinRestoreError('invalid restore point')
    records = [dict(item) for item in group['files']]
    expected = {item['path']: item for item in records}
    if set(expected) != set(checked):
        raise SkinRestoreError('pending restore files do not match the manifest')
    for path, data in checked.items():
        item = expected[path]
        if (len(data) != item['size']
                or hashlib.sha256(data).hexdigest() != item['sha256']):
            raise SkinRestoreError('pending restore payload changed after preflight')
    if snapshot_fingerprint(checked) != validated['skin_config']['fingerprint']:
        raise SkinRestoreError('pending restore fingerprint mismatch')
    settings = skin_setting_values(checked[SETTINGS_PATH])
    if len(settings) != validated['skin_config']['setting_count']:
        raise SkinRestoreError('pending AF3 setting count mismatch')
    record = {
        'schema_version': PENDING_VERSION,
        'phase': 'prepared',
        'archive_id': ARCHIVE_ID,
        'archive_version': ARCHIVE_VERSION,
        'restore_point': restore_point,
        'skin_config': dict(validated['skin_config']),
        'files': records,
        'skin_settings': settings,
        'helper_hashes': {
            path: hashlib.sha256(data).hexdigest()
            for path, data in checked.items() if path != SETTINGS_PATH
        },
        'rollback': None,
    }
    return validate_pending_restore(record)


def validate_pending_restore(record):
    """Reject damaged or invented pending state before orchestration uses it."""
    required = {
        'schema_version', 'phase', 'archive_id', 'archive_version',
        'restore_point', 'skin_config', 'files', 'skin_settings',
        'helper_hashes', 'rollback',
    }
    if not isinstance(record, dict) or set(record) != required:
        raise SkinRestoreError('pending AF3 restore state is invalid')
    if (record.get('schema_version') != PENDING_VERSION
            or record.get('archive_id') != ARCHIVE_ID
            or record.get('archive_version') != ARCHIVE_VERSION
            or record.get('phase') not in (
                'prepared', 'files_applied', 'rebuild', 'rollback_rebuild')):
        raise SkinRestoreError('pending AF3 restore state is unsupported')
    restore_point = record.get('restore_point')
    if (not isinstance(restore_point, str) or not restore_point
            or len(restore_point) > 1024 or '\x00' in restore_point):
        raise SkinRestoreError('pending AF3 restore point is invalid')
    try:
        metadata = validate_snapshot_manifest(
            record.get('skin_config'), record.get('files'))
        settings = checked_skin_setting_values(record.get('skin_settings'))
    except SkinAdapterError as error:
        raise SkinRestoreError(str(error)) from error
    if len(settings) != metadata['setting_count']:
        raise SkinRestoreError('pending AF3 setting count is invalid')
    hashes = record.get('helper_hashes')
    if not isinstance(hashes, dict):
        raise SkinRestoreError('pending AF3 helper hashes are invalid')
    expected_helpers = {
        item['path']: item['sha256'] for item in record['files']
        if item['path'] != SETTINGS_PATH
    }
    if hashes != expected_helpers:
        raise SkinRestoreError('pending AF3 helper hashes do not match the manifest')
    rollback = record.get('rollback')
    if rollback is not None and (
            not isinstance(rollback, str) or not rollback or '\x00' in rollback):
        raise SkinRestoreError('pending AF3 rollback location is invalid')
    if record['phase'] == 'prepared' and rollback is not None:
        raise SkinRestoreError('prepared AF3 restore cannot have rollback state')
    if record['phase'] != 'prepared' and rollback is None:
        raise SkinRestoreError('applied AF3 restore is missing rollback state')
    result = dict(record)
    result['skin_config'] = metadata
    result['skin_settings'] = settings
    result['files'] = [dict(item) for item in record['files']]
    result['helper_hashes'] = dict(hashes)
    return result
