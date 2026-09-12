"""Whitelist-based packager for the script.backup.pro Kodi add-on.

Builds a distributable ZIP containing exactly the runtime files a Kodi
installation needs - never the worktree wholesale. Everything not on
ADDON_WHITELIST (development tooling, tests, docs, git/agent state,
disposable Kodi test profiles, compiled bytecode, OS metadata) is
excluded by construction: a whitelist only ever adds surface area when
someone deliberately extends it, unlike a blacklist that silently leaks
new development files into a release the moment someone adds one.
"""

import argparse
import os
import re
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every top-level path a running Kodi installation actually needs.
# Adding a new runtime file/directory requires deliberately extending
# this list - that is the point.
ADDON_WHITELIST = (
    'addon.xml',
    'default.py',
    'service.py',
    'LICENSE.txt',
    'resources',
)

# Patterns excluded even from whitelisted directories - compiled
# bytecode caches and OS metadata are never runtime inputs.
EXCLUDED_NAMES = re.compile(r'^(__pycache__|\.DS_Store)$')
EXCLUDED_SUFFIXES = ('.pyc', '.pyo')


def _addon_id_and_version(addon_xml_path):
    with open(addon_xml_path, 'r', encoding='utf-8') as handle:
        contents = handle.read()
    id_match = re.search(r'<addon\b[^>]*\bid="([^"]+)"', contents)
    version_match = re.search(r'<addon\b[^>]*\bversion="([^"]+)"', contents)
    if not id_match or not version_match:
        raise ValueError('could not read addon id/version from addon.xml')
    return id_match.group(1), version_match.group(1)


def _copy_filtered(source, destination):
    if os.path.isfile(source):
        shutil.copy2(source, destination)
        return
    for dirpath, dirnames, filenames in os.walk(source):
        dirnames[:] = [d for d in dirnames if not EXCLUDED_NAMES.match(d)]
        relative = os.path.relpath(dirpath, source)
        target_dir = (destination if relative == '.'
                      else os.path.join(destination, relative))
        os.makedirs(target_dir, exist_ok=True)
        for name in filenames:
            if EXCLUDED_NAMES.match(name) or name.endswith(EXCLUDED_SUFFIXES):
                continue
            shutil.copy2(os.path.join(dirpath, name),
                         os.path.join(target_dir, name))


def build_package(root=ROOT, output_dir=None):
    """Stage the whitelisted addon files and zip them.

    Returns (staged_addon_dir, zip_path). Raises FileNotFoundError if a
    whitelisted path is missing (fail closed rather than ship a
    silently incomplete package).
    """
    addon_id, version = _addon_id_and_version(
        os.path.join(root, 'addon.xml'))
    output_dir = output_dir or os.path.join(root, 'dist')
    staging_root = os.path.join(output_dir, '_staging')
    if os.path.isdir(staging_root):
        shutil.rmtree(staging_root)
    addon_dir = os.path.join(staging_root, addon_id)
    os.makedirs(addon_dir)

    for relative in ADDON_WHITELIST:
        source = os.path.join(root, relative)
        if not os.path.exists(source):
            raise FileNotFoundError(
                'whitelisted addon path is missing: ' + relative)
        _copy_filtered(source, os.path.join(addon_dir, relative))

    zip_name = '{}-{}.zip'.format(addon_id, version)
    zip_path = os.path.join(output_dir, zip_name)
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for dirpath, _dirnames, filenames in os.walk(addon_dir):
            for name in filenames:
                full_path = os.path.join(dirpath, name)
                arcname = os.path.relpath(full_path, staging_root)
                archive.write(full_path, arcname)

    return addon_dir, zip_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default=None,
                         help='directory to write dist output into '
                              '(default: <repo root>/dist)')
    args = parser.parse_args(argv)
    addon_dir, zip_path = build_package(output_dir=args.output_dir)
    print('staged: {}'.format(addon_dir))
    print('packaged: {}'.format(zip_path))
    return 0


if __name__ == '__main__':
    sys.exit(main())
