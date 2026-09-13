from __future__ import unicode_literals

import re


TMDB_HELPER_ID = 'plugin.video.themoviedb.helper'
TMDB_HELPER_CACHE_DIRS = (
    'blur_v3',
    'crop_v2',
    'desaturate_v2',
    'colors_v2',
)

SQLITE_SIDECAR_SUFFIXES = ('-shm', '-wal', '-journal')
SQLITE_SIDECAR_EXCLUSION = {
    'adapter': 'sqlite',
    'reason': 'Volatile SQLite sidecar file',
}

_SCHEME = re.compile(r'^([A-Za-z][A-Za-z0-9+.-]*://)(.*)$')


class PlanningError(ValueError):
    pass


def normalize_path(path):
    """Return a stable slash-separated path without resolving its root."""
    if not isinstance(path, str) or not path.strip():
        raise PlanningError('path must be a non-empty string')
    if '\x00' in path:
        raise PlanningError('path contains a null byte')

    value = path.strip().replace('\\', '/')
    scheme = ''
    match = _SCHEME.match(value)
    if match:
        scheme, value = match.groups()

    absolute = value.startswith('/')
    parts = []
    for part in value.split('/'):
        if not part or part == '.':
            continue
        if part == '..':
            raise PlanningError('parent traversal is not allowed')
        parts.append(part)

    normalized = '/'.join(parts)
    if scheme:
        return scheme.lower() + normalized
    if absolute:
        return '/' + normalized if normalized else '/'
    return normalized


def join_path(root, child):
    child_path = normalize_path(child)
    if '://' in child_path or child_path.startswith('/'):
        raise PlanningError('child path must be relative')
    base = normalize_path(root)
    if base.endswith('://'):
        return normalize_path(base + child_path)
    return normalize_path(base.rstrip('/') + '/' + child_path)


def path_is_within(path, root, case_sensitive=True):
    """Match an exact path or descendant using a component boundary."""
    candidate = normalize_path(path)
    parent = normalize_path(root)
    if not case_sensitive:
        candidate = candidate.casefold()
        parent = parent.casefold()
    if parent == '/':
        return candidate.startswith('/')
    return candidate == parent or candidate.startswith(parent.rstrip('/') + '/')


def relative_path(path, root, case_sensitive=True):
    candidate = normalize_path(path)
    parent = normalize_path(root)
    if not path_is_within(candidate, parent, case_sensitive=case_sensitive):
        raise PlanningError('%s is outside %s' % (candidate, parent))
    if candidate == parent or (
            not case_sensitive and candidate.casefold() == parent.casefold()):
        return ''
    return candidate[len(parent.rstrip('/')) + 1:]


def tmdb_helper_cache_exclusions(image_location):
    base = normalize_path(image_location)
    return [
        {
            'type': 'exclude',
            'path': join_path(base, directory),
            'adapter': TMDB_HELPER_ID,
            'reason': 'Regenerable TMDb Helper image cache',
        }
        for directory in TMDB_HELPER_CACHE_DIRS
    ]


class FilePlanner:
    """Enumerate included files and measured exclusions against a VFS."""

    def __init__(self, vfs, translate=None, validate=None, logger=None,
                 verbose=False, case_sensitive=True):
        self.vfs = vfs
        self.translate = translate or (lambda value: value)
        self.validate = validate or (lambda value: value)
        self.logger = logger or (lambda message: None)
        self.verbose = verbose
        self.case_sensitive = case_sensitive
        self.fileArray = []
        self.exclude_rules = []
        self.root_dirs = []
        self.totalSize = 0.0
        self.excluded = []
        self._excluded_seen = set()

    def _path(self, value):
        return normalize_path(self.validate(self.translate(value)))

    def _join(self, root, name):
        return self._path(join_path(root, name))

    def addDir(self, metadata):
        if metadata['type'] == 'include':
            self.root_dirs.append({
                'path': self._path(metadata['path']),
                'recurse': bool(metadata.get('recurse', True)),
            })
            return

        self.excludeFile(
            metadata['path'],
            adapter=metadata.get('adapter', 'custom'),
            reason=metadata.get('reason', 'Excluded by backup configuration'),
        )

    def excludeFile(self, filename, adapter='custom',
                    reason='Excluded by backup configuration'):
        path = self._path(filename)
        self.logger('Exclude path: ' + path)
        self.exclude_rules.append({
            'path': path,
            'adapter': adapter,
            'reason': reason,
        })

    def _matching_rule(self, path):
        for rule in self.exclude_rules:
            if path_is_within(path, rule['path'],
                              case_sensitive=self.case_sensitive):
                return rule
        return None

    @staticmethod
    def _sqlite_sidecar_rule(path):
        """Return the shared exclusion rule for transient SQLite companions."""
        filename = path.rsplit('/', 1)[-1].casefold()
        if filename.endswith(SQLITE_SIDECAR_SUFFIXES):
            return SQLITE_SIDECAR_EXCLUSION
        return None

    def walk(self):
        for root in self.root_dirs:
            rule = self._matching_rule(root['path'])
            if rule:
                self._record_excluded(root['path'], rule)
                continue
            self.addFile(root['path'], True)
            self.walkTree(root['path'], root['recurse'])

    def walkTree(self, directory, recurse=True):
        directory = self._path(directory)
        if self.verbose:
            self.logger('Walking %s, recurse: %s' % (directory, recurse))
        if not self.vfs.exists(directory + '/'):
            return

        dirs, files = self.vfs.listdir(directory)
        if recurse:
            for name in sorted(dirs):
                path = self._join(directory, name)
                rule = self._matching_rule(path)
                if rule:
                    self._record_excluded(path, rule)
                    continue
                self.addFile(path, True)
                self.walkTree(path, True)

        for name in sorted(files):
            path = self._join(directory, name)
            rule = self._matching_rule(path) or self._sqlite_sidecar_rule(path)
            if rule:
                self._record_excluded(path, rule, is_file=True)
                continue
            self.addFile(path)

    def addFile(self, filename, is_dir=False):
        path = self._path(filename)
        if self.verbose:
            self.logger('Add file: ' + path)
        size = 0.0 if is_dir else float(self.vfs.fileSize(path))
        self.totalSize += size
        self.fileArray.append({
            'file': path,
            'size': size,
            'is_dir': bool(is_dir),
        })

    def _record_excluded(self, path, rule, is_file=False):
        normalized = self._path(path)
        key = normalized if self.case_sensitive else normalized.casefold()
        if key in self._excluded_seen:
            return
        self._excluded_seen.add(key)
        if is_file:
            size, count = float(self.vfs.fileSize(normalized)), 1
        else:
            size, count = self._measure_tree(normalized, set())
        self.excluded.append({
            'path': normalized,
            'size_kib': size,
            'file_count': count,
            'adapter': rule['adapter'],
            'reason': rule['reason'],
        })

    def _measure_tree(self, directory, visited):
        normalized = normalize_path(directory)
        key = normalized if self.case_sensitive else normalized.casefold()
        if key in visited or not self.vfs.exists(directory + '/'):
            return 0.0, 0
        visited.add(key)
        dirs, files = self.vfs.listdir(directory)
        size = 0.0
        count = 0
        for name in files:
            size += float(self.vfs.fileSize(self._join(directory, name)))
            count += 1
        for name in dirs:
            child_size, child_count = self._measure_tree(
                self._join(directory, name), visited)
            size += child_size
            count += child_count
        return size, count

    def summary(self):
        return {
            'included_kib': self.totalSize,
            'included_files': sum(
                1 for item in self.fileArray if not item['is_dir']),
            'excluded_kib': sum(item['size_kib'] for item in self.excluded),
            'excluded_files': sum(item['file_count'] for item in self.excluded),
            'exclusions': list(self.excluded),
        }

    def getFiles(self):
        result = self.fileArray
        self.fileArray = []
        self.root_dirs = []
        self.exclude_rules = []
        self.excluded = []
        self._excluded_seen = set()
        self.totalSize = 0.0
        return result

    def totalFiles(self):
        return len(self.fileArray)

    def fileSize(self):
        return self.totalSize


def summarize_file_groups(groups, largest_limit=10):
    totals = []
    directories = []
    total_kib = 0.0
    total_files = 0
    excluded_kib = 0.0
    excluded_files = 0
    exclusions = []

    for group in groups:
        summary = group.get('summary') or {}
        group_size = float(summary.get('included_kib', 0.0))
        group_files = int(summary.get('included_files', 0))
        total_kib += group_size
        total_files += group_files
        excluded_kib += float(summary.get('excluded_kib', 0.0))
        excluded_files += int(summary.get('excluded_files', 0))
        exclusions.extend(summary.get('exclusions', []))
        totals.append({
            'name': group['name'],
            'size_kib': group_size,
            'file_count': group_files,
        })

        root = normalize_path(group.get('plan_root', group['source']))
        buckets = {}
        for item in group.get('files', []):
            if item.get('is_dir'):
                continue
            relative = relative_path(item['file'], root)
            first = relative.split('/', 1)[0] if '/' in relative else '(root)'
            key = join_path(root, first) if first != '(root)' else root
            bucket = buckets.setdefault(key, {'size_kib': 0.0, 'file_count': 0})
            bucket['size_kib'] += float(item.get('size', 0.0))
            bucket['file_count'] += 1
        directories.extend({
            'group': group['name'],
            'path': path,
            'size_kib': values['size_kib'],
            'file_count': values['file_count'],
        } for path, values in buckets.items())

    totals.sort(key=lambda item: (-item['size_kib'], item['name']))
    directories.sort(key=lambda item: (-item['size_kib'], item['path']))
    return {
        'total_kib': total_kib,
        'file_count': total_files,
        'excluded_kib': excluded_kib,
        'excluded_files': excluded_files,
        'groups': totals,
        'largest_directories': directories[:largest_limit],
        'exclusions': exclusions,
    }


def describe_backup_plan(plan):
    """Reduce a summarize_file_groups() plan to the values a completion
    summary needs, breaking exclusions down by adapter so the caller can
    explain *why* something was excluded (regenerable cache vs. a
    separately-managed source) rather than only reporting a raw count.
    Kept Kodi-independent and pure so it is directly unit-testable.
    """
    tmdb_excluded_kib = 0.0
    tmdb_excluded_files = 0
    for exclusion in plan.get('exclusions', []):
        if exclusion.get('adapter') == TMDB_HELPER_ID:
            tmdb_excluded_kib += float(exclusion.get('size_kib', 0.0))
            tmdb_excluded_files += int(exclusion.get('file_count', 0))

    return {
        'included_files': int(plan.get('file_count', 0)),
        'included_kib': float(plan.get('total_kib', 0.0)),
        'excluded_files': int(plan.get('excluded_files', 0)),
        'excluded_kib': float(plan.get('excluded_kib', 0.0)),
        'tmdb_cache_excluded_files': tmdb_excluded_files,
        'tmdb_cache_excluded_kib': tmdb_excluded_kib,
    }
