from __future__ import unicode_literals

"""Validated capture support for skin configuration adapters.

The first adapter is deliberately limited to Arctic Fuse 3 and the source
files that Script Skin Variables uses to rebuild its generated output.  Kodi's
live setting map is authoritative; the skin's on-disk settings.xml is never
read by this module.
"""

import hashlib
import json
import math
import os
import re
import stat
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree


AF3_ID = 'skin.arctic.fuse.3'
SKIN_VARIABLES_ID = 'script.skinvariables'
ADAPTER_ID = 'backup-pro.af3'
ADAPTER_VERSION = 2
APPEARANCE_SETTINGS = (
    'lookandfeel.skintheme',
    'lookandfeel.skincolors',
    'lookandfeel.font',
    'lookandfeel.skinzoom',
)

MAX_SETTING_COUNT = 10000
MAX_FILE_COUNT = 2000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024

_SKIN_ID = re.compile(r'^skin\.[A-Za-z0-9][A-Za-z0-9._-]{0,122}$')
_SETTING_ID = re.compile(r'^[A-Za-z0-9_.-]{1,256}$')
_SKIN_USER_SLUG = re.compile(r'^user-[0-9A-Za-z]+$')
_SHA256 = re.compile(r'^[0-9a-f]{64}$')


class SkinAdapterError(ValueError):
    pass


def validate_skin_id(skin_id):
    if (not isinstance(skin_id, str) or not _SKIN_ID.fullmatch(skin_id)
            or '..' in skin_id):
        raise SkinAdapterError('invalid skin id')
    return skin_id


def volatile_skin_setting(setting_id):
    """Exclude Skin Variables build fingerprints, not user preferences.

    Live restore testing 2026-09-10 found two sibling self-written,
    session-local cache keys reappearing in settings.xml as an ordinary
    side effect of AF3 reactivating and rebuilding, each missed by an
    earlier, narrower version of this pattern: `script-skinviewtypes-hash`
    (missing the `-variables-` segment a `script-skinvariables-*-hash`
    pattern required) and, once rebuild_skin() itself ran and
    regenerated view templates, `script-skinviewtypes-checksum` (a
    `-checksum` suffix, not `-hash`) - confirmed by diffing the staged
    snapshot against live settings.xml at the exact
    verify_loaded_settings() failure point both times: it was the only
    difference, every real user setting matched. Matches any
    `script-skin*` id ending in either `-hash` or `-checksum`, the two
    build-fingerprint suffixes actually observed, without excluding
    anything that looks like a real, user-authored setting."""
    return (setting_id.startswith('script-skin')
            and (setting_id.endswith('-hash')
                 or setting_id.endswith('-checksum')))


def checked_skin_setting_values(values):
    """Validate and normalize Kodi's complete typed skin-setting map."""
    if not isinstance(values, list) or len(values) > MAX_SETTING_COUNT:
        raise SkinAdapterError('Kodi returned invalid skin settings')
    checked = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            raise SkinAdapterError('Kodi returned an invalid skin setting')
        setting_id = item.get('id')
        kind = item.get('type')
        value = item.get('value')
        if (not isinstance(setting_id, str)
                or not _SETTING_ID.fullmatch(setting_id)
                or setting_id in seen
                or kind not in ('boolean', 'string')
                or (kind == 'boolean' and not isinstance(value, bool))
                or (kind == 'string' and not isinstance(value, str))):
            raise SkinAdapterError('Kodi returned an invalid or duplicate skin setting')
        seen.add(setting_id)
        if not volatile_skin_setting(setting_id):
            checked.append({'id': setting_id, 'type': kind, 'value': value})
    return sorted(checked, key=lambda item: item['id'].lower())


def live_skin_setting_values(result, expected_skin=AF3_ID):
    """Validate one Settings.GetSkinSettings result from Kodi JSON-RPC."""
    validate_skin_id(expected_skin)
    if (not isinstance(result, dict) or result.get('skin') != expected_skin
            or not isinstance(result.get('settings'), list)):
        raise SkinAdapterError('Kodi did not expose the active skin settings')
    return checked_skin_setting_values(result['settings'])


def skin_settings_document(values):
    """Encode typed live settings in Kodi's portable settings.xml format."""
    checked = checked_skin_setting_values(values)
    root = ElementTree.Element('settings')
    for item in checked:
        kind = 'bool' if item['type'] == 'boolean' else 'string'
        node = ElementTree.SubElement(
            root, 'setting', {'id': item['id'], 'type': kind})
        if kind == 'bool':
            node.text = 'true' if item['value'] else 'false'
        else:
            node.text = item['value']
    return ElementTree.tostring(root, encoding='utf-8', xml_declaration=True)


def skin_setting_values(document):
    """Decode and validate one portable settings.xml document."""
    if not isinstance(document, bytes) or len(document) > MAX_FILE_BYTES:
        raise SkinAdapterError('settings.xml data is invalid')
    try:
        root = ElementTree.fromstring(document)
    except (ElementTree.ParseError, ValueError) as error:
        raise SkinAdapterError('settings.xml is not valid XML') from error
    if root.tag != 'settings' or any(child.tag != 'setting' for child in root):
        raise SkinAdapterError('settings.xml must contain direct setting entries')
    values = []
    for item in root:
        if list(item):
            raise SkinAdapterError('skin setting entries cannot be nested')
        setting_id = item.get('id') or item.get('name')
        kind = item.get('type') or 'string'
        if kind == 'bool':
            text = (item.text or '').strip().lower()
            if text not in ('true', 'false'):
                raise SkinAdapterError('invalid boolean skin setting')
            values.append({'id': setting_id, 'type': 'boolean',
                           'value': text == 'true'})
        elif kind == 'string':
            values.append({'id': setting_id, 'type': 'string',
                           'value': item.text or ''})
        else:
            raise SkinAdapterError('unsupported skin setting type')
    return checked_skin_setting_values(values)


def skin_settings_equal(left, right):
    if left is None or right is None or len(left) != len(right):
        return False
    return ({item['id']: (item['type'], item['value']) for item in left} ==
            {item['id']: (item['type'], item['value']) for item in right})


def checked_appearance(values):
    """Validate the Kodi appearance values needed to reproduce AF3's look."""
    if not isinstance(values, dict) or any(
            key not in APPEARANCE_SETTINGS for key in values):
        raise SkinAdapterError('invalid AF3 appearance settings')
    checked = {}
    for key in APPEARANCE_SETTINGS:
        if key not in values:
            continue
        value = values[key]
        if key == 'lookandfeel.skinzoom':
            if (not isinstance(value, (int, float)) or isinstance(value, bool)
                    or not math.isfinite(value) or abs(value) > 1000):
                raise SkinAdapterError('invalid AF3 skin zoom')
        elif (not isinstance(value, str) or len(value) > 256
              or '\x00' in value):
            raise SkinAdapterError('invalid AF3 appearance value')
        checked[key] = value
    return checked


def _settings_path(skin_id):
    return 'addon_data/{}/settings.xml'.format(skin_id)


def _viewtypes_path(skin_id):
    return 'addon_data/{}/{}-viewtypes.json'.format(
        SKIN_VARIABLES_ID, skin_id)


def _skinusers_path(skin_id):
    return 'addon_data/{}/logins/{}/skinusers.json'.format(
        SKIN_VARIABLES_ID, skin_id)


def _helper_roots(skin_id, user_slugs=()):
    base = 'addon_data/{}'.format(SKIN_VARIABLES_ID)
    node_ids = [skin_id]
    node_ids.extend('{}-{}'.format(skin_id, slug)
                    for slug in sorted(set(user_slugs)))
    roots = ['{}/nodes/{}'.format(base, node_id) for node_id in node_ids]
    roots.append('{}/logins/{}'.format(base, skin_id))
    return tuple(roots)


def _profile_file(root, relative):
    return root.joinpath(*PurePosixPath(relative).parts)


def _path_signature(info):
    return (info.st_dev, info.st_ino, info.st_size,
            getattr(info, 'st_mtime_ns', int(info.st_mtime * 1000000000)),
            getattr(info, 'st_ctime_ns', int(info.st_ctime * 1000000000)))


def _assert_safe_root(root):
    try:
        info = root.lstat()
    except OSError as error:
        raise SkinAdapterError('profile directory is unavailable') from error
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SkinAdapterError('profile path is not a safe directory')


def _assert_no_symlink_components(root, relative):
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        except OSError as error:
            raise SkinAdapterError('cannot inspect managed path: ' + relative) from error
        if stat.S_ISLNK(info.st_mode):
            raise SkinAdapterError('symlinks are not allowed in managed paths: ' + relative)


def _read_once(path):
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise SkinAdapterError('managed path is not a regular file: ' + str(path))
        if before.st_size > MAX_FILE_BYTES:
            raise SkinAdapterError('managed file exceeds the size limit: ' + str(path))
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            opened = os.fstat(descriptor)
            chunks = []
            remaining = MAX_FILE_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = path.lstat()
    except SkinAdapterError:
        raise
    except OSError as error:
        raise SkinAdapterError('cannot read managed file: ' + str(path)) from error
    if len({_path_signature(before), _path_signature(opened),
            _path_signature(after_open), _path_signature(after)}) != 1:
        raise SkinAdapterError('managed file changed while being read: ' + str(path))
    data = b''.join(chunks)
    if len(data) > MAX_FILE_BYTES:
        raise SkinAdapterError('managed file exceeds the size limit: ' + str(path))
    return data, _path_signature(after)


def _read_consistent(path):
    first, first_signature = _read_once(path)
    second, second_signature = _read_once(path)
    if first_signature != second_signature or first != second:
        raise SkinAdapterError('managed file changed while being read: ' + str(path))
    return first


def _load_json(data, description):
    def reject_constant(value):
        raise ValueError('non-standard JSON constant: ' + value)
    try:
        return json.loads(data.decode('utf-8'), parse_constant=reject_constant)
    except (UnicodeDecodeError, ValueError) as error:
        raise SkinAdapterError('invalid JSON in ' + description) from error


def _declared_user_slugs(root, skin_id):
    relative = _skinusers_path(skin_id)
    _assert_no_symlink_components(root, relative)
    path = _profile_file(root, relative)
    if not path.exists():
        return ()
    users = _load_json(_read_consistent(path), 'Skin Variables skinusers.json')
    if not isinstance(users, list):
        raise SkinAdapterError('Skin Variables skinusers.json must contain a list')
    found = []
    for user in users:
        slug = user.get('slug') if isinstance(user, dict) else None
        if (not isinstance(slug, str) or not _SKIN_USER_SLUG.fullmatch(slug)
                or slug in found):
            raise SkinAdapterError('invalid or duplicate Skin Variables user slug')
        found.append(slug)
    return tuple(sorted(found))


def _inferred_user_slugs(root, skin_id):
    relative = 'addon_data/{}/nodes'.format(SKIN_VARIABLES_ID)
    _assert_no_symlink_components(root, relative)
    nodes = _profile_file(root, relative)
    if not nodes.exists():
        return ()
    try:
        info = nodes.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SkinAdapterError('Skin Variables nodes path is not a safe directory')
        entries = tuple(os.scandir(str(nodes)))
    except SkinAdapterError:
        raise
    except OSError as error:
        raise SkinAdapterError('cannot inspect Skin Variables profile directories') from error
    prefix = skin_id + '-'
    found = []
    for entry in entries:
        if not entry.name.startswith(prefix):
            continue
        slug = entry.name[len(prefix):]
        if not _SKIN_USER_SLUG.fullmatch(slug):
            continue
        try:
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                raise SkinAdapterError('unsafe Skin Variables profile directory: ' + entry.name)
        except OSError as error:
            raise SkinAdapterError('cannot inspect Skin Variables profile directory') from error
        found.append(slug)
    return tuple(sorted(found))


def _enumerate_helper_files(root, skin_id, user_slugs):
    _assert_safe_root(root)
    found = []
    viewtypes = _viewtypes_path(skin_id)
    _assert_no_symlink_components(root, viewtypes)
    viewtypes_path = _profile_file(root, viewtypes)
    if viewtypes_path.exists():
        info = viewtypes_path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise SkinAdapterError('managed viewtypes path is not a regular file')
        found.append(viewtypes)

    for relative_root in _helper_roots(skin_id, user_slugs):
        _assert_no_symlink_components(root, relative_root)
        directory = _profile_file(root, relative_root)
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise SkinAdapterError('managed helper root is not a directory')
        for dirpath, dirnames, filenames in os.walk(str(directory), followlinks=False):
            base = Path(dirpath)
            for name in tuple(dirnames) + tuple(filenames):
                child = base / name
                try:
                    info = child.lstat()
                except OSError as error:
                    raise SkinAdapterError(
                        'cannot inspect managed helper path') from error
                if stat.S_ISLNK(info.st_mode):
                    raise SkinAdapterError('symlinks are not allowed in managed helper paths')
            for name in filenames:
                if not name.endswith('.json'):
                    continue
                child = base / name
                try:
                    info = child.lstat()
                except OSError as error:
                    raise SkinAdapterError(
                        'cannot inspect managed helper file') from error
                if not stat.S_ISREG(info.st_mode):
                    raise SkinAdapterError('managed helper path is not a regular file')
                found.append(child.relative_to(root).as_posix())
    # Reserve one entry for the authoritative portable settings document.
    if len(found) >= MAX_FILE_COUNT:
        raise SkinAdapterError('skin adapter contains too many helper files')
    return tuple(sorted(found))


def collect_helper_files(profile_path, skin_id=AF3_ID):
    """Collect only validated Skin Variables source JSON for one skin."""
    validate_skin_id(skin_id)
    root = Path(profile_path)
    first_slugs = tuple(sorted(set(_declared_user_slugs(root, skin_id)) |
                               set(_inferred_user_slugs(root, skin_id))))
    first_paths = _enumerate_helper_files(root, skin_id, first_slugs)
    files = {}
    total = 0
    for relative in first_paths:
        data = _read_consistent(_profile_file(root, relative))
        _load_json(data, relative)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SkinAdapterError('skin adapter helper files exceed the total size limit')
        files[relative] = data
    current_slugs = tuple(sorted(set(_declared_user_slugs(root, skin_id)) |
                                 set(_inferred_user_slugs(root, skin_id))))
    if (current_slugs != first_slugs
            or _enumerate_helper_files(root, skin_id, first_slugs) != first_paths):
        raise SkinAdapterError('managed helper files changed while being collected')
    return files


def current_managed_paths(profile_path, skin_id=AF3_ID):
    """Enumerate raw adapter-owned files, including corrupt rollback inputs."""
    validate_skin_id(skin_id)
    root = Path(profile_path)
    try:
        declared = _declared_user_slugs(root, skin_id)
    except SkinAdapterError:
        # A restore must be able to repair corrupt Skin Variables declarations.
        declared = ()
    slugs = tuple(sorted(set(declared) | set(_inferred_user_slugs(root, skin_id))))
    paths = list(_enumerate_helper_files(root, skin_id, slugs))
    settings = _settings_path(skin_id)
    _assert_no_symlink_components(root, settings)
    settings_path = _profile_file(root, settings)
    if settings_path.exists():
        if not stat.S_ISREG(settings_path.lstat().st_mode):
            raise SkinAdapterError('managed settings path is not a regular file')
        paths.append(settings)
    return tuple(sorted(paths))


def read_current_managed_files(profile_path, skin_id=AF3_ID):
    root = Path(profile_path)
    files = {}
    total = 0
    for relative in current_managed_paths(root, skin_id):
        data = _read_consistent(_profile_file(root, relative))
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SkinAdapterError('current managed files exceed the total size limit')
        files[relative] = data
    return files


def validate_snapshot_files(files, skin_id=AF3_ID):
    """Validate extracted adapter payload bytes before any restore mutation."""
    validate_skin_id(skin_id)
    if not isinstance(files, dict) or not files or len(files) > MAX_FILE_COUNT:
        raise SkinAdapterError('invalid skin adapter file mapping')
    managed_source_paths(files, skin_id)
    settings = _settings_path(skin_id)
    if settings not in files:
        raise SkinAdapterError('skin adapter snapshot is missing settings.xml')
    checked = {}
    total = 0
    for path, data in files.items():
        if not isinstance(data, bytes) or len(data) > MAX_FILE_BYTES:
            raise SkinAdapterError('invalid managed file data: ' + path)
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SkinAdapterError('skin adapter snapshot exceeds the total size limit')
        if path == settings:
            skin_setting_values(data)
        else:
            _load_json(data, path)
        checked[path] = data
    return checked


def snapshot_fingerprint(files):
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode('utf-8'))
        digest.update(b'\0')
        digest.update(files[path])
        digest.update(b'\0')
    return digest.hexdigest()


def managed_source_paths(files, skin_id=AF3_ID):
    """Return profile paths owned by the adapter for generic-backup exclusion."""
    validate_skin_id(skin_id)
    if not isinstance(files, dict):
        raise SkinAdapterError('skin adapter files must be a mapping')
    roots = set()
    nodes_prefix = 'addon_data/{}/nodes/'.format(SKIN_VARIABLES_ID)
    logins_prefix = 'addon_data/{}/logins/{}/'.format(
        SKIN_VARIABLES_ID, skin_id)
    for path in files:
        if path == _settings_path(skin_id) or path == _viewtypes_path(skin_id):
            roots.add(path)
        elif path.startswith(nodes_prefix):
            parts = path.split('/')
            if len(parts) < 5 or not path.endswith('.json'):
                raise SkinAdapterError('invalid managed Skin Variables node path')
            node_id = parts[3]
            if node_id != skin_id:
                prefix = skin_id + '-'
                slug = node_id[len(prefix):] if node_id.startswith(prefix) else ''
                if not _SKIN_USER_SLUG.fullmatch(slug):
                    raise SkinAdapterError('invalid managed Skin Variables node path')
            roots.add('/'.join(parts[:4]))
        elif path.startswith(logins_prefix):
            if not path.endswith('.json'):
                raise SkinAdapterError('invalid managed Skin Variables login path')
            roots.add(logins_prefix.rstrip('/'))
        else:
            raise SkinAdapterError('path is outside the AF3 adapter scope: ' + path)
    return tuple(sorted(roots))


def validate_snapshot_manifest(metadata, files):
    """Validate untrusted AF3 metadata and manifest file records."""
    if not isinstance(metadata, dict) or not isinstance(files, list):
        raise SkinAdapterError('invalid AF3 snapshot manifest')
    required = {
        'adapter_id', 'adapter_version', 'skin_id', 'skin_version',
        'helper_id', 'helper_version', 'source_device', 'source_profile',
        'setting_count', 'helper_file_count', 'file_count', 'total_bytes',
        'fingerprint', 'appearance',
    }
    if set(metadata) != required:
        raise SkinAdapterError('invalid AF3 snapshot metadata fields')
    if (metadata.get('adapter_id') != ADAPTER_ID
            or metadata.get('adapter_version') != ADAPTER_VERSION
            or metadata.get('skin_id') != AF3_ID
            or metadata.get('helper_id') != SKIN_VARIABLES_ID):
        raise SkinAdapterError('unsupported AF3 snapshot adapter')
    for field in ('skin_version', 'helper_version', 'source_device',
                  'source_profile'):
        _metadata_text(metadata.get(field), field.replace('_', ' '))
    for field, maximum in (('setting_count', MAX_SETTING_COUNT),
                           ('helper_file_count', MAX_FILE_COUNT - 1),
                           ('file_count', MAX_FILE_COUNT),
                           ('total_bytes', MAX_TOTAL_BYTES)):
        value = metadata.get(field)
        if (not isinstance(value, int) or isinstance(value, bool)
                or value < 0 or value > maximum):
            raise SkinAdapterError('invalid AF3 snapshot ' + field)
    fingerprint = metadata.get('fingerprint')
    if not isinstance(fingerprint, str) or not _SHA256.fullmatch(fingerprint):
        raise SkinAdapterError('invalid AF3 snapshot fingerprint')
    appearance = checked_appearance(metadata.get('appearance'))

    paths = []
    total_bytes = 0
    for item in files:
        if not isinstance(item, dict):
            raise SkinAdapterError('invalid AF3 snapshot file record')
        path = item.get('path')
        size = item.get('size')
        checksum = item.get('sha256')
        if (set(item) != {'path', 'size', 'sha256'}
                or not isinstance(path, str)
                or not isinstance(size, int) or isinstance(size, bool)
                or size < 0 or size > MAX_FILE_BYTES
                or not isinstance(checksum, str)
                or not _SHA256.fullmatch(checksum)):
            raise SkinAdapterError('invalid AF3 snapshot file record')
        paths.append(path)
        total_bytes += size
    managed_source_paths(dict.fromkeys(paths, b''), AF3_ID)
    if _settings_path(AF3_ID) not in paths:
        raise SkinAdapterError('AF3 snapshot is missing live settings')
    if (metadata['file_count'] != len(files)
            or metadata['helper_file_count'] != len(files) - 1
            or metadata['total_bytes'] != total_bytes):
        raise SkinAdapterError('AF3 snapshot metadata does not match its files')
    result = dict(metadata)
    result['appearance'] = appearance
    return result


def _metadata_text(value, name):
    if value is None:
        return ''
    if not isinstance(value, str) or len(value) > 256 or '\x00' in value:
        raise SkinAdapterError('invalid ' + name)
    return value


def capture_af3_snapshot(profile_path, rpc_call, source_device='',
                         source_profile='', skin_version='', helper_version='',
                         settle=None, appearance_call=None):
    """Capture one stable AF3 configuration snapshot without mutating Kodi."""
    if not callable(rpc_call):
        raise SkinAdapterError('Kodi JSON-RPC reader is unavailable')
    first = live_skin_setting_values(rpc_call('Settings.GetSkinSettings'), AF3_ID)
    first_appearance = checked_appearance(
        appearance_call() if appearance_call is not None else {})
    first_helpers = collect_helper_files(profile_path, AF3_ID)
    first_helper_fingerprint = snapshot_fingerprint(first_helpers)
    if settle is not None:
        if not callable(settle):
            raise SkinAdapterError('skin capture settle callback is invalid')
        settle()
    helpers = collect_helper_files(profile_path, AF3_ID)
    current = live_skin_setting_values(rpc_call('Settings.GetSkinSettings'), AF3_ID)
    appearance = checked_appearance(
        appearance_call() if appearance_call is not None else {})
    if (not skin_settings_equal(first, current)
            or first_helper_fingerprint != snapshot_fingerprint(helpers)
            or first_appearance != appearance):
        raise SkinAdapterError('skin configuration changed while being collected')

    files = dict(helpers)
    files[_settings_path(AF3_ID)] = skin_settings_document(current)
    if len(files) > MAX_FILE_COUNT:
        raise SkinAdapterError('skin adapter snapshot contains too many files')
    total_bytes = sum(len(value) for value in files.values())
    if total_bytes > MAX_TOTAL_BYTES:
        raise SkinAdapterError('skin adapter snapshot exceeds the total size limit')
    metadata = {
        'adapter_id': ADAPTER_ID,
        'adapter_version': ADAPTER_VERSION,
        'skin_id': AF3_ID,
        'skin_version': _metadata_text(skin_version, 'skin version'),
        'helper_id': SKIN_VARIABLES_ID,
        'helper_version': _metadata_text(helper_version, 'helper version'),
        'source_device': _metadata_text(source_device, 'source device'),
        'source_profile': _metadata_text(source_profile, 'source profile'),
        'setting_count': len(current),
        'helper_file_count': len(helpers),
        'file_count': len(files),
        'total_bytes': total_bytes,
        'fingerprint': snapshot_fingerprint(files),
        'appearance': appearance,
    }
    return {'metadata': metadata, 'settings': current, 'files': files}
