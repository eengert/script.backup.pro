# Kodi Distribution Workflow

This document records the existing personal Kodi repository workflow for
publishing add-ons such as Backup Pro. It is operational documentation, not
authorization to publish. Agents must never publish, commit in the distribution
repository, or push unless the user explicitly requests that action.

## Repository and delivery path

- Local repository: `/Users/example/Documents/Kodi/repository.eengert`
- Git branch used for publication: `main`
- Remote mechanism: HTTPS Git remote to the private/user-controlled GitHub
  repository; local pushes use the machine's existing Git credential helper or
  GitHub authentication. Never record tokens, passwords, cookies, or credential
  contents in project files.
- Public Kodi source: `https://eengert.github.io/repository.eengert/`

The repository packages and indexes the user's maintained Kodi add-ons. Its
relevant layout is:

```text
repository.eengert/
├── addon.xml                         # Eengert Repository add-on descriptor
├── repository.eengert-1.0.0.zip     # install-this-first repository ZIP
├── scripts/build_repository.py       # reusable repository builder
├── tests/test_build_repository.py
└── omega/zips/
    ├── addons.xml                    # aggregate add-on index
    ├── addons.xml.md5                # checksum of addons.xml
    ├── repository.eengert/           # repository add-on metadata and ZIP
    └── <addon-id>/
        ├── addon.xml
        ├── <addon-id>-<version>.zip
        └── resources/...             # copied artwork/metadata assets
```

GitHub Pages serves the files from `main`. Kodi reads
`omega/zips/addons.xml`, verifies it with `addons.xml.md5`, and downloads ZIPs
from `omega/zips/`.

## Reusable tooling

The distribution repository already has a general-purpose builder:

```sh
cd /Users/example/Documents/Kodi/repository.eengert
python3 scripts/build_repository.py \
  --source /absolute/path/to/already-audited-addon.zip \
  --version X.Y.Z
```

An optional `--repository-root` argument is supported. The builder validates a
numeric version and rejects unsafe ZIP paths, duplicates, and symlinks. It then:

1. Builds `omega/zips/<addon-id>/<addon-id>-<version>.zip`.
2. Copies the packaged `addon.xml` and add-on assets beside the ZIP.
3. Regenerates the repository add-on package in
   `omega/zips/repository.eengert/` and the root repository ZIP.
4. Regenerates `omega/zips/addons.xml` and `omega/zips/addons.xml.md5`.

Run its tests with:

```sh
cd /Users/example/Documents/Kodi/repository.eengert
python3 -m unittest discover -s tests -v
```

The existing `.github/workflows/publish.yml` automates TMDb Helper publication
only. It is not a Backup Pro publisher and must not be repurposed casually.

Backup Pro does **not yet have a tracked packaging script**. Do not pass its
source worktree directory directly to `build_repository.py`: the builder's
directory exclusions do not currently cover every coordination file used by
this project (`.agent`, `.ai`, and agent instruction/handoff files), so doing so
could leak development-only files. First create an audited, whitelist-built
Backup Pro ZIP in a later, separately approved development task. The packager
for `/Users/example/Documents/Kodi/service.skinsettings.backup` is a useful
design reference, but is add-on-specific and must not be run as Backup Pro's
packager.

## Publication procedure

Only perform these steps after explicit publication authorization.

1. Confirm the intended Backup Pro source commit, clean/understood Git state,
   add-on ID `script.backup.pro`, and new numeric version.
2. Run Backup Pro's future whitelist packager and its full automated checks.
3. Audit the ZIP before allowing it near the distribution repository:

   ```sh
   unzip -t /absolute/path/to/script.backup.pro-X.Y.Z.zip
   unzip -Z1 /absolute/path/to/script.backup.pro-X.Y.Z.zip
   ```

   It must contain exactly one `script.backup.pro/` top-level directory and no
   tests, Git files, `.agent`, `.ai`, handoff files, bytecode, reports,
   AppleDouble files, or other development artifacts.

4. Test and run the repository builder:

   ```sh
   cd /Users/example/Documents/Kodi/repository.eengert
   python3 -m unittest discover -s tests -v
   python3 scripts/build_repository.py \
     --source /absolute/path/to/script.backup.pro-X.Y.Z.zip \
     --version X.Y.Z
   git status --short
   git diff --stat
   git diff -- omega/zips/addons.xml
   ```

5. Verify all generated artifacts as described below.
6. When explicitly authorized to publish, the current manual Git workflow is:

   ```sh
   git add omega/zips repository.eengert-*.zip
   git commit -m "Publish Backup Pro X.Y.Z"
   git push origin main
   ```

   Prefer narrower explicit paths when practical. Review the staged diff before
   committing. A publication therefore requires ZIP generation, repository
   metadata/checksum regeneration, `git add`, `git commit`, and `git push`.
   Merely copying a ZIP is insufficient.

## Device installation and updates

On a device, add `https://eengert.github.io/repository.eengert/` as a Kodi File
Manager source, install `repository.eengert-1.0.0.zip` with **Install from zip
file**, then use **Install from repository → Eengert Repository → Program
add-ons → Backup Pro**. Once installed, Kodi discovers later releases through
the repository index. Every published update must use a higher add-on version;
metadata and CDN/Kodi caches can otherwise leave devices on stale content.

Installing an exact Backup Pro ZIP manually can be useful for isolated Mac
testing, but it is not the normal multi-device distribution path.

## Verification

Before commit or push:

```sh
cd /Users/example/Documents/Kodi/repository.eengert
python3 -m unittest discover -s tests -v
md5 -q omega/zips/addons.xml
cat omega/zips/addons.xml.md5
unzip -t omega/zips/script.backup.pro/script.backup.pro-X.Y.Z.zip
unzip -Z1 omega/zips/script.backup.pro/script.backup.pro-X.Y.Z.zip
shasum -a 256 omega/zips/script.backup.pro/script.backup.pro-X.Y.Z.zip
```

Also parse/inspect the aggregate and packaged `addon.xml` files and confirm the
ID and version. The computed MD5 must exactly equal `addons.xml.md5`.

After an authorized push, fetch the public files with a cache-busting query
string. Confirm that public `addons.xml` contains the intended version, its
public MD5 matches, and the public ZIP's SHA-256 equals the locally generated
ZIP. Run `unzip -t` on the downloaded public ZIP and verify its internal
`addon.xml`. Finally refresh the Eengert Repository in Kodi and confirm that
Backup Pro appears under Program add-ons at the intended version.

## Safety constraints

- Never publish or push without explicit user authorization. Project agents
  must obey the persistent workflow's standing `never push` rule unless the
  user deliberately changes it for a publication task.
- Do not write to any Apple TV during current development. Publication does not
  itself authorize installation or testing on a device.
- Never copy secrets into documentation, commands, logs, commits, or packages.
- Preserve every unrelated add-on directory and release in the distribution
  repository. Review the builder's diff before staging.
- Do not edit `repository.eengert/addon.xml` or change the repository add-on's
  own version unless intentionally releasing the repository add-on.
- Never package Backup Pro from an unaudited or dirty source directory. Use the
  future whitelist packager and inspect the resulting ZIP.
- Do not force-push, reset, clean, or broadly restore either repository.
- Keep public verification cache-busted and compare checksums, not filenames
  alone.

## Safe recovery

If the builder fails, stop and inspect `git status` and `git diff`. Its input
validation normally occurs before replacement of generated output, but do not
assume the tree is unchanged. If generated files were altered, restore only the
confirmed publication targets from Git, for example:

```sh
git restore -- \
  omega/zips/script.backup.pro \
  omega/zips/addons.xml \
  omega/zips/addons.xml.md5 \
  omega/zips/repository.eengert \
  repository.eengert-1.0.0.zip
```

Use that command only after verifying those are the complete intended targets
and that none contains wanted pre-existing work. Never use `git reset --hard`
or `git clean`.

If a bad commit exists locally but was not pushed, prefer a corrective commit
or `git revert <bad-commit>` so history stays understandable. If a bad release
was pushed, make and publish a revert/correction only with explicit authority,
then issue the corrected add-on with a higher version to defeat Kodi and Pages
caches. Preserve the failed artifact, hashes, and relevant logs until the cause
is understood.
