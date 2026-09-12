from __future__ import unicode_literals

import hashlib
import json
import os
import re
import stat
import zipfile

from .planning import relative_path
from .skin_adapter import SkinAdapterError, validate_snapshot_manifest


ARCHIVE_ID = 'script.backup.pro.archive'
ARCHIVE_VERSION = 1
MANIFEST_NAME = 'backup-pro.manifest.json'

# Hard ceilings are deliberately far above a normal Kodi profile. They are a
# final safety boundary; free-space checks and user-facing estimates belong in
# the restore preflight phase.
MAX_FILE_COUNT = 500000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 1000

_DRIVE_PATH = re.compile(r'^[A-Za-z]:')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')
_SUPPORTED_COMPRESSION = frozenset((zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED))


class ArchiveValidationError(ValueError):
    pass


def normalize_member_path(path, allow_directory=False):
    """Validate and normalize one portable archive-relative path."""
    if not isinstance(path, str) or not path:
        raise ArchiveValidationError('archive path must be a non-empty string')
    if '\x00' in path:
        raise ArchiveValidationError('archive path contains a null byte')
    if '\\' in path:
        raise ArchiveValidationError('archive path must use forward slashes')
    if path.startswith('/') or path.startswith('//') or _DRIVE_PATH.match(path):
        raise ArchiveValidationError('archive path must be relative')

    is_directory = path.endswith('/')
    if is_directory and not allow_directory:
        raise ArchiveValidationError('directory path is not allowed here')

    value = path[:-1] if is_directory else path
    parts = value.split('/')
    if not value or any(part in ('', '.', '..') for part in parts):
        raise ArchiveValidationError('archive path has unsafe components')

    normalized = '/'.join(parts)
    return normalized + ('/' if is_directory else '')


def _entry_value(entry, name, default=None):
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _entry_is_directory(entry, name):
    marker = _entry_value(entry, 'is_dir')
    if callable(marker):
        return bool(marker())
    if marker is not None:
        return bool(marker)
    return name.endswith('/')


def _entry_type(entry, is_directory):
    external_attr = int(_entry_value(entry, 'external_attr', 0) or 0)
    mode = external_attr >> 16
    kind = stat.S_IFMT(mode)
    if not kind:
        return 'directory' if is_directory else 'file'
    if stat.S_ISLNK(mode):
        return 'symlink'
    if stat.S_ISDIR(mode):
        return 'directory'
    if stat.S_ISREG(mode):
        return 'file'
    return 'unsupported'


def validate_archive_members(entries, max_file_count=MAX_FILE_COUNT,
                             max_member_bytes=MAX_MEMBER_BYTES,
                             max_total_bytes=MAX_TOTAL_BYTES,
                             max_compression_ratio=MAX_COMPRESSION_RATIO):
    """Reject unsafe ZIP metadata before any member is extracted."""
    seen = set()
    total_bytes = 0
    file_count = 0
    validated = []

    for entry in entries:
        raw_name = _entry_value(entry, 'filename', _entry_value(entry, 'name'))
        is_directory = _entry_is_directory(entry, raw_name or '')
        name = normalize_member_path(raw_name, allow_directory=is_directory)
        duplicate_key = name.rstrip('/').casefold()
        if duplicate_key in seen:
            raise ArchiveValidationError('duplicate archive path: ' + name)
        seen.add(duplicate_key)

        entry_type = _entry_type(entry, is_directory)
        if entry_type == 'symlink':
            raise ArchiveValidationError('symbolic links are not supported: ' + name)
        if entry_type == 'unsupported' or (
                is_directory and entry_type != 'directory') or (
                not is_directory and entry_type != 'file'):
            raise ArchiveValidationError('unsupported archive entry: ' + name)

        flag_bits = int(_entry_value(entry, 'flag_bits', 0) or 0)
        if flag_bits & 0x1:
            raise ArchiveValidationError('encrypted archive entry: ' + name)
        compression = int(_entry_value(
            entry, 'compress_type', zipfile.ZIP_STORED))
        if compression not in _SUPPORTED_COMPRESSION:
            raise ArchiveValidationError('unsupported compression: ' + name)

        size = int(_entry_value(entry, 'file_size', 0) or 0)
        compressed = int(_entry_value(entry, 'compress_size', size) or 0)
        if size < 0 or compressed < 0:
            raise ArchiveValidationError('negative archive size: ' + name)
        if is_directory and size:
            raise ArchiveValidationError('directory contains file data: ' + name)
        if not is_directory:
            file_count += 1
            if file_count > max_file_count:
                raise ArchiveValidationError('archive file-count limit exceeded')
            if size > max_member_bytes:
                raise ArchiveValidationError('archive member-size limit exceeded: ' + name)
            total_bytes += size
            if total_bytes > max_total_bytes:
                raise ArchiveValidationError('archive total-size limit exceeded')
            if compressed == 0 and size > 0:
                raise ArchiveValidationError('invalid compressed size: ' + name)
            if compressed and size / float(compressed) > max_compression_ratio:
                raise ArchiveValidationError('archive compression-ratio limit exceeded: ' + name)

        validated.append({
            'name': name,
            'is_dir': is_directory,
            'size': size,
        })

    if not validated:
        raise ArchiveValidationError('archive is empty')
    return {
        'members': validated,
        'file_count': file_count,
        'total_bytes': total_bytes,
    }


def find_manifest_member(entries):
    matches = []
    for entry in entries:
        raw_name = _entry_value(
            entry, 'filename', _entry_value(entry, 'name', ''))
        name = normalize_member_path(
            raw_name, allow_directory=_entry_is_directory(entry, raw_name))
        if not name.endswith('/') and name.rsplit('/', 1)[-1] == MANIFEST_NAME:
            matches.append((entry, name))
    if len(matches) != 1:
        raise ArchiveValidationError(
            'archive must contain exactly one Backup Pro manifest')
    entry, name = matches[0]
    if int(_entry_value(entry, 'file_size', 0) or 0) > MAX_MANIFEST_BYTES:
        raise ArchiveValidationError('Backup Pro manifest-size limit exceeded')
    parts = name.split('/')
    if len(parts) != 2:
        raise ArchiveValidationError(
            'Backup Pro manifest must be inside one archive root')
    return entry, parts[0]


def validate_archive_layout(entries, manifest):
    """Require the ZIP file list to match the validated manifest exactly."""
    member_summary = validate_archive_members(entries)
    _manifest_entry, root = find_manifest_member(entries)
    validated_manifest = validate_manifest(manifest)

    expected = {
        (root + '/' + MANIFEST_NAME).casefold(): None,
    }
    optional = {(root + '/.nomedia').casefold(): 0}
    for group in validated_manifest['directories']:
        for item in group['files']:
            path = '%s/%s/%s' % (root, group['name'], item['path'])
            key = path.casefold()
            if key in expected:
                raise ArchiveValidationError(
                    'manifest collides with reserved archive path: ' + path)
            expected[key] = item['size']

    actual = {}
    for member in member_summary['members']:
        if member['is_dir']:
            raise ArchiveValidationError(
                'explicit directory entries are not supported: ' +
                member['name'])
        actual[member['name'].casefold()] = member['size']

    missing = sorted(set(expected) - set(actual))
    unexpected = sorted(set(actual) - set(expected) - set(optional))
    if missing:
        raise ArchiveValidationError(
            'archive is missing declared members: ' + ', '.join(missing[:3]))
    if unexpected:
        raise ArchiveValidationError(
            'archive contains undeclared members: ' + ', '.join(unexpected[:3]))
    for path, expected_size in expected.items():
        if expected_size is not None and actual[path] != expected_size:
            raise ArchiveValidationError(
                'archive member size does not match manifest: ' + path)
    for path, expected_size in optional.items():
        if path in actual and actual[path] != expected_size:
            raise ArchiveValidationError(
                'archive metadata member has unexpected data: ' + path)

    return {
        'root': root,
        'manifest': validated_manifest,
        'file_count': validated_manifest['file_count'],
        'total_bytes': validated_manifest['total_bytes'],
    }


def verify_archive_payload(entries, manifest, open_member, check_cancel=None):
    """Stream and verify every declared payload member before extraction."""
    layout = validate_archive_layout(entries, manifest)
    root = layout['root']
    index = {}
    for entry in entries:
        raw_name = _entry_value(
            entry, 'filename', _entry_value(entry, 'name', ''))
        index[normalize_member_path(raw_name).casefold()] = entry

    for group in layout['manifest']['directories']:
        for item in group['files']:
            if check_cancel and check_cancel():
                raise ArchiveValidationError('archive verification cancelled')
            archive_path = '%s/%s/%s' % (
                root, group['name'], item['path'])
            try:
                with open_member(index[archive_path.casefold()]) as source:
                    checksum, size = sha256_reader(
                        source.read, check_cancel=check_cancel)
            except Exception as error:
                raise ArchiveValidationError(
                    'unable to read archive member %s: %s' % (
                        archive_path, error))
            if size != item['size']:
                raise ArchiveValidationError(
                    'archive payload size mismatch: ' + archive_path)
            if checksum != item['sha256']:
                raise ArchiveValidationError(
                    'archive payload checksum mismatch: ' + archive_path)
    return layout


def verify_manifest_files(manifest, hash_file, check_cancel=None):
    """Verify a folder-form backup through a caller-provided streaming hasher."""
    validated = validate_manifest(manifest)
    verified_files = 0
    verified_bytes = 0
    for group in validated['directories']:
        for item in group['files']:
            if check_cancel and check_cancel():
                raise ArchiveValidationError('backup verification cancelled')
            path = group['name'] + '/' + item['path']
            try:
                checksum, size = hash_file(path)
            except Exception as error:
                raise ArchiveValidationError(
                    'unable to read backup file %s: %s' % (path, error))
            if size != item['size']:
                raise ArchiveValidationError(
                    'backup file size mismatch: ' + path)
            if checksum != item['sha256']:
                raise ArchiveValidationError(
                    'backup file checksum mismatch: ' + path)
            verified_files += 1
            verified_bytes += size
    return {
        'file_count': verified_files,
        'total_bytes': verified_bytes,
    }


def verify_zip_archive(path, check_cancel=None):
    """Open and fully verify one completed Backup Pro ZIP on local storage."""
    try:
        with zipfile.ZipFile(path, 'r') as archive:
            entries = archive.infolist()
            validate_archive_members(entries)
            manifest_member, _root = find_manifest_member(entries)
            manifest_data = archive.read(manifest_member).decode('utf-8')
            manifest = load_manifest(manifest_data)
            return verify_archive_payload(
                entries, manifest, archive.open, check_cancel=check_cancel)
    except ArchiveValidationError:
        raise
    except Exception as error:
        raise ArchiveValidationError('unable to verify ZIP archive: %s' % error)


def sha256_reader(read_chunk, chunk_size=1024 * 1024, check_cancel=None):
    digest = hashlib.sha256()
    size = 0
    while True:
        if check_cancel and check_cancel():
            raise ArchiveValidationError('hashing cancelled')
        chunk = read_chunk(chunk_size)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode('utf-8')
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def build_manifest(groups, hash_file, metadata=None, check_cancel=None):
    """Create a validated manifest from a measured backup plan."""
    directories = []
    for group in groups:
        name = normalize_member_path(group['name'])
        if '/' in name:
            raise ArchiveValidationError(
                'backup directory name must be one component')
        root = group.get('plan_root', group['source'])
        files = []
        for item in group.get('files', []):
            if item.get('is_dir'):
                continue
            if check_cancel and check_cancel():
                raise ArchiveValidationError('manifest creation cancelled')
            path = normalize_member_path(relative_path(item['file'], root))
            checksum, size = hash_file(item['file'])
            files.append({
                'path': path,
                'size': int(size),
                'sha256': checksum,
            })
        files.sort(key=lambda item: item['path'].casefold())
        directories.append({
            'name': name,
            'path': group.get('restore_path', group['source']),
            'files': files,
        })

    document = dict(metadata or {})
    document.update({
        'archive_id': ARCHIVE_ID,
        'archive_version': ARCHIVE_VERSION,
        'directories': directories,
    })
    return validate_manifest(document)


def _hash_local_file(path):
    """Independently hash a path as a native local file, bypassing Kodi VFS.

    Used only as a fallback when the VFS-based hash disagrees for
    same-size case-colliding aliases (observed on tvOS, where
    ``xbmcvfs.File``/``xbmcvfs.translatePath`` produced different
    fingerprints for two case-variant paths that are byte-identical on
    disk). Raises if the path is not a real, directly-openable local file
    (e.g. a remote/VFS URL), so the caller can fail closed.
    """
    if '://' in path or not os.path.isfile(path):
        raise ArchiveValidationError(
            'path is not a native local file: ' + path)
    with open(path, 'rb') as source:
        return sha256_reader(lambda size: source.read(size))


def _confirm_identical_via_local_read(measured, local_hash_file):
    """Re-check same-size, VFS-disagreeing aliases via independent I/O.

    Returns True only if every alias is readable as a native local file and
    every independently computed fingerprint agrees. Any read failure,
    unavailable fallback, or disagreement returns False, so the caller
    keeps failing closed.
    """
    if local_hash_file is None:
        return False
    local_fingerprints = set()
    for alias, _checksum, _size in measured:
        try:
            local_checksum, local_size = local_hash_file(
                alias['source_path'])
        except Exception:
            return False
        local_fingerprints.add((local_checksum, int(local_size)))
    return len(local_fingerprints) == 1


def collapse_identical_case_collisions(
        groups, hash_file, local_hash_file=_hash_local_file):
    """Remove byte-identical aliases of one case-insensitive archive path.

    Kodi profiles on case-sensitive filesystems can retain paths that differ
    only by capitalization. Such paths cannot both be restored safely onto a
    case-insensitive filesystem. Select the lexicographically smallest
    normalized archive path (then source path as a tie-breaker), independent
    of enumeration order, but only when every candidate has the same size and
    SHA-256. Differing candidates remain a hard error.

    If the primary (VFS) hashes disagree but every candidate has the same
    byte length, a same-size disagreement is not by itself proof the files
    differ — it can also be a platform VFS read quirk. In that case only,
    ``local_hash_file`` (a native filesystem read, independent of the VFS
    path that produced the disagreement) is used to double-check; the
    aliases are only collapsed if that independent read proves them
    byte-identical. Any error, unavailability, non-local path, or
    disagreement in that check still fails closed. The final manifest
    validator deliberately remains a separate fail-closed boundary and is
    unaffected by this fallback.
    """
    filtered_groups = []
    all_exclusions = []

    for group in groups:
        group_name = normalize_member_path(group['name'])
        root = group.get('plan_root', group['source'])
        files = list(group.get('files', []))
        collisions = {}

        for index, item in enumerate(files):
            if item.get('is_dir'):
                continue
            relative = normalize_member_path(relative_path(item['file'], root))
            archive_path = group_name + '/' + relative
            collisions.setdefault(archive_path.casefold(), []).append({
                'index': index,
                'source_path': item['file'],
                'archive_path': archive_path,
            })

        dropped = set()
        group_exclusions = []
        for key in sorted(collisions):
            aliases = collisions[key]
            if len(aliases) < 2:
                continue
            aliases.sort(key=lambda item: (
                item['archive_path'], item['source_path']))
            measured = []
            for alias in aliases:
                checksum, size = hash_file(alias['source_path'])
                measured.append((alias, checksum, int(size)))

            fingerprints = {(checksum, size)
                            for _alias, checksum, size in measured}
            if len(fingerprints) != 1:
                sizes = {size for _alias, _checksum, size in measured}
                confirmed_identical = (
                    len(sizes) == 1
                    and _confirm_identical_via_local_read(
                        measured, local_hash_file))
                if not confirmed_identical:
                    details = '; '.join(
                        '%s -> %s' % (
                            alias['source_path'], alias['archive_path'])
                        for alias, _checksum, _size in measured)
                    raise ArchiveValidationError(
                        'case-colliding source files differ: ' + details)

            kept = measured[0][0]
            for alias, _checksum, size in measured[1:]:
                dropped.add(alias['index'])
                exclusion = {
                    'path': alias['source_path'],
                    'archive_path': alias['archive_path'],
                    'kept_path': kept['source_path'],
                    'kept_archive_path': kept['archive_path'],
                    'size_kib': size / 1024.0,
                    'file_count': 1,
                    'adapter': 'casefold_alias',
                    'reason': 'Byte-identical case-only source alias',
                }
                group_exclusions.append(exclusion)
                all_exclusions.append(exclusion)

        filtered = dict(group)
        filtered['files'] = [
            item for index, item in enumerate(files) if index not in dropped]
        if group_exclusions and 'summary' in group:
            summary = dict(group.get('summary') or {})
            excluded_kib = sum(
                item['size_kib'] for item in group_exclusions)
            summary['included_kib'] = max(
                0.0, float(summary.get('included_kib', 0.0)) - excluded_kib)
            summary['included_files'] = max(
                0, int(summary.get('included_files', 0))
                - len(group_exclusions))
            summary['excluded_kib'] = (
                float(summary.get('excluded_kib', 0.0)) + excluded_kib)
            summary['excluded_files'] = (
                int(summary.get('excluded_files', 0))
                + len(group_exclusions))
            summary['exclusions'] = (
                list(summary.get('exclusions', [])) + group_exclusions)
            filtered['summary'] = summary
        filtered_groups.append(filtered)

    return filtered_groups, all_exclusions


def validate_manifest(document):
    """Validate a parsed Backup Pro manifest and return a normalized copy."""
    if not isinstance(document, dict):
        raise ArchiveValidationError('manifest must be an object')
    if document.get('archive_id') != ARCHIVE_ID:
        raise ArchiveValidationError('not a Backup Pro archive')
    if document.get('archive_version') != ARCHIVE_VERSION:
        raise ArchiveValidationError('unsupported Backup Pro archive version')

    groups = document.get('directories')
    if not isinstance(groups, list) or not groups:
        raise ArchiveValidationError('manifest has no backup directories')

    group_names = set()
    member_paths = set()
    file_count = 0
    total_bytes = 0
    normalized_groups = []
    for group in groups:
        if not isinstance(group, dict):
            raise ArchiveValidationError('invalid backup directory entry')
        name = normalize_member_path(group.get('name', ''))
        if '/' in name:
            raise ArchiveValidationError('backup directory name must be one component')
        name_key = name.casefold()
        if name_key in group_names:
            raise ArchiveValidationError('duplicate backup directory: ' + name)
        if name_key in (MANIFEST_NAME.casefold(), '.nomedia'):
            raise ArchiveValidationError('reserved backup directory name: ' + name)
        group_names.add(name_key)
        source = group.get('path')
        if not isinstance(source, str) or not source:
            raise ArchiveValidationError('backup directory source is missing')
        files = group.get('files')
        if not isinstance(files, list):
            raise ArchiveValidationError('backup directory files must be a list')

        normalized_files = []
        for item in files:
            if not isinstance(item, dict):
                raise ArchiveValidationError('invalid manifest file entry')
            path = normalize_member_path(item.get('path', ''))
            archive_path = name + '/' + path
            member_key = archive_path.casefold()
            if member_key in member_paths:
                raise ArchiveValidationError('duplicate manifest path: ' + archive_path)
            member_paths.add(member_key)

            size = item.get('size')
            checksum = item.get('sha256')
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ArchiveValidationError('invalid manifest file size: ' + path)
            if size > MAX_MEMBER_BYTES:
                raise ArchiveValidationError('manifest member-size limit exceeded: ' + path)
            if not isinstance(checksum, str) or not _SHA256.match(checksum):
                raise ArchiveValidationError('invalid SHA-256: ' + path)
            file_count += 1
            total_bytes += size
            if file_count > MAX_FILE_COUNT:
                raise ArchiveValidationError('manifest file-count limit exceeded')
            if total_bytes > MAX_TOTAL_BYTES:
                raise ArchiveValidationError('manifest total-size limit exceeded')
            normalized_files.append({
                'path': path,
                'size': size,
                'sha256': checksum,
            })

        normalized_group = dict(group)
        normalized_group['name'] = name
        normalized_group['files'] = normalized_files
        normalized_groups.append(normalized_group)

    result = dict(document)
    result['directories'] = normalized_groups
    result['file_count'] = file_count
    result['total_bytes'] = total_bytes
    skin_groups = [group for group in normalized_groups
                   if group['name'].casefold() == 'skin_config']
    skin_metadata = document.get('skin_config')
    if bool(skin_groups) != (skin_metadata is not None):
        raise ArchiveValidationError(
            'AF3 snapshot metadata and directory must appear together')
    if skin_groups:
        try:
            result['skin_config'] = validate_snapshot_manifest(
                skin_metadata, skin_groups[0]['files'])
        except SkinAdapterError as error:
            raise ArchiveValidationError(str(error))
    return result


def load_manifest(text):
    try:
        document = json.loads(text)
    except (TypeError, ValueError, RecursionError) as error:
        raise ArchiveValidationError('manifest is not valid JSON: %s' % error)
    return validate_manifest(document)
