#!/usr/bin/env python3
"""Safe disposable Kodi runner and allowlisted development installer.

Isolation model (verified against an actual Kodi 21.1 launch on this Mac,
2026-09-10 - see docs/MAC_KODI_VALIDATION.md): Kodi resolves its profile
from the process's HOME environment variable, not from any profile-path
flag (there is none; `--portable` uses an install-relative directory
instead). Pointing HOME at a project-local, disposable directory makes
Kodi resolve macOS-style paths beneath it:

    special://home/    -> $HOME/Library/Application Support/Kodi
    special://profile/ -> $HOME/Library/Application Support/Kodi/userdata
    special://logpath/ -> $HOME/Library/Logs
    special://temp/    -> $HOME/.kodi/temp

Everything this module writes to, resets, or reports as the disposable
Kodi location is anchored under KODI_APPDATA_DIR/KODI_LOG_FILE, matching
that observed behavior - not a `.kodi/` layout, which real Kodi 21.1 does
not use for its profile or logs on macOS.

Non-interactive script triggering (needed for Phase 9a steps 5+, e.g.
"trigger a Backup Pro backup" without a human clicking the main menu):
`special://profile/autoexec.py`, the legacy XBMC/Kodi startup-script
hook, does NOT exist in this Kodi 21.1 macOS build - confirmed
empirically on 2026-09-10 (a marker-file-writing autoexec.py never ran
across several real launches, and the string "autoexec" does not occur
anywhere in the Kodi binary or bundled system resources). Do not rely on
autoexec.py here.

JSON-RPC (`configure_webserver()` + `execute_addon()`) is proven instead
(2026-09-10, real launch on this Mac): the disposable webserver comes up
on the configured port (confirmed via `CWebserver[<port>]: Started` in
the log), `JSONRPC.Ping` returns `pong` over HTTP Basic Auth, and
`Addons.ExecuteAddon` genuinely invokes the add-on's `default.py`/
`service.py` inside the running Kodi process (confirmed via a real
Python traceback naming those exact files/line numbers in the log) -
but only *after* first calling `Addons.SetAddonEnabled`, since a freshly
`install()`-ed add-on is not enabled by default. Separately discovered:
Backup Pro's declared `addon.xml` dependencies
(`script.module.dateutil`, `script.module.future`,
`script.module.dropbox`, `script.module.pyqrcode`) - and their own
further transitive dependencies (`script.module.dropbox` alone needs
`six`, `requests`, `certifi`, `chardet`, `idna`, `urllib3`) - are not
present in a disposable profile that only ran `install()` (which
copies Backup Pro's own files only); `install_dependencies()` resolves
and copies the full transitive closure from the real profile to fix
this.

With dependencies resolved, a triggered `mode=backup` run (2026-09-10)
got all the way to `XbmcBackup._createValidationFile()` before failing
- proving the trigger mechanism works end-to-end, not just that Kodi's
API accepted the call. The failure was a real Backup Pro production
bug, not a harness or dependency issue:
`resources/lib/archive.py::sha256_reader()` assumed `read_chunk()`
returns `bytes` or `str` and called `.encode('utf-8')` on anything
else, but this Kodi 21.1 build's `xbmcvfs.File.read()` returns
`bytearray`, which has no `.encode()` method. Fixed (commit
`22c914c`); a triggered backup now completes and its manifest was
inspected directly (correct addon/Kodi version, correct exclusions,
hashed files) - Phase 9a step 5 is proven done.

`install_skin()` (built on the same `_copy_addon_closure()` used by
`install_dependencies()`) copies a skin add-on - default Arctic Fuse 3
- into the disposable profile for step 4+: Backup Pro's AF3 capture is
gated on `xbmc.getSkinDir() == AF3_ID`, so AF3 must actually be the
*active* skin, not merely present. `configure_webserver()`'s
`extra_settings` lets a caller set `lookandfeel.skin` in the same
pre-launch write as the webserver settings (it can only be called once
per fresh profile).

AF3 activation itself needed two more fixes before it worked
(2026-09-10, all confirmed against real launches, no network access
used): (1) `_copy_addon_closure()` only ever checked the real profile's
`addons/` directory, so it wrongly reported `script.module.pil` (needed
transitively by two of AF3's own dependencies) as missing - it's
actually bundled inside Kodi.app itself (`KODI_SYSTEM_ADDONS_DIR`),
visible to every profile automatically; fixed to check there first and
skip copying anything already available that way. (2) every freshly
copied add-on - AF3 itself and its whole dependency closure, not just
Backup Pro - starts disabled, and Kodi's boot-time skin loader silently
falls back to Estuary if any of a skin's hard dependencies are
disabled; `enable_addons()` batch-enables them, followed by `restart()`
for a clean boot (a live `Settings.SetSettingValue` skin switch while
already running does not trigger a real reload - confirmed empirically,
only stop()+launch() does). With all of that, AF3 activates
successfully and a triggered backup with `backup_skin_config=true`
produces a real `skin_config/` capture with correct skin/helper
id/version and real appearance settings - Phase 9a step 4 is proven
done, no network install was actually needed. See
docs/MAC_KODI_VALIDATION.md → "Evidence and automation boundary" for
the full picture.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from xml.sax.saxutils import escape as _xml_escape

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / ".kodi-test"
HOME = ROOT / "home"
KODI_APPDATA_DIR = HOME / "Library" / "Application Support" / "Kodi"
KODI_USERDATA_DIR = KODI_APPDATA_DIR / "userdata"
KODI_ADDONS_DIR = KODI_APPDATA_DIR / "addons"
KODI_LOG_FILE = HOME / "Library" / "Logs" / "kodi.log"
KODI_GUISETTINGS_FILE = KODI_USERDATA_DIR / "guisettings.xml"
PID_FILE = ROOT / "kodi.pid"
KODI = Path("/Applications/Kodi.app/Contents/MacOS/Kodi")
# add-ons bundled inside the Kodi.app installation itself (special://xbmc/addons/)
# - visible to every profile automatically, including a brand-new disposable
# one, with no per-profile copying at all. Confirmed empirically 2026-09-10:
# script.module.pil and repository.xbmc.org both live here, not in any
# per-profile addons/ directory.
KODI_SYSTEM_ADDONS_DIR = KODI.parent.parent / "Resources" / "Kodi" / "addons"
ADDON_ID = "script.backup.pro"
AF3_SKIN_ID = "skin.arctic.fuse.3"
WEBSERVER_PORT = 8899
WEBSERVER_USERNAME = "kodi-test"
WEBSERVER_PASSWORD = "kodi-test-only"

# the real, normal Kodi profile location this harness must never overlap
NORMAL_APPDATA_DIR = Path.home() / "Library" / "Application Support" / "Kodi"
NORMAL_LOG_FILE = Path.home() / "Library" / "Logs" / "kodi.log"


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or _inside(a, b) or _inside(b, a)


def verify_isolation() -> dict[str, str]:
    if not KODI.is_file() or not os.access(KODI, os.X_OK):
        raise RuntimeError(f"Kodi executable is unavailable: {KODI}")
    if (ROOT == Path("/") or not _inside(HOME, ROOT)
            or not _inside(KODI_APPDATA_DIR, HOME)
            or not _inside(KODI_LOG_FILE.parent, HOME)):
        raise RuntimeError("unsafe disposable path configuration")
    if _overlaps(KODI_APPDATA_DIR, NORMAL_APPDATA_DIR):
        raise RuntimeError(
            "disposable Kodi profile overlaps the normal Kodi profile")
    if _overlaps(KODI_LOG_FILE, NORMAL_LOG_FILE):
        raise RuntimeError(
            "disposable Kodi log overlaps the normal Kodi log")
    return {
        "root": str(ROOT),
        "home": str(HOME),
        "appdata": str(KODI_APPDATA_DIR),
        "userdata": str(KODI_USERDATA_DIR),
        "addons": str(KODI_ADDONS_DIR),
        "log": str(KODI_LOG_FILE),
        "executable": str(KODI),
    }


def _env() -> dict[str, str]:
    verify_isolation()
    env = os.environ.copy()
    env["HOME"] = str(HOME)
    env.pop("KODI_HOME", None)
    return env


def reset() -> None:
    verify_isolation()
    if PID_FILE.exists():
        raise RuntimeError("Kodi appears active; stop it before reset")
    if ROOT.exists():
        if ROOT.resolve() != ROOT or not _inside(ROOT, PROJECT):
            raise RuntimeError("refusing to reset unexpected disposable path")
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    HOME.mkdir()


def status() -> dict[str, object]:
    info = verify_isolation()
    pid = None
    running = False
    if PID_FILE.exists():
        pid = int(PID_FILE.read_text().strip())
        try:
            os.kill(pid, 0)
            running = True
        except (OSError, ProcessLookupError):
            PID_FILE.unlink(missing_ok=True)
    info.update({"pid": pid, "running": running})
    return info


def launch() -> None:
    verify_isolation()
    if status()["running"]:
        raise RuntimeError("disposable Kodi is already running")
    ROOT.mkdir(parents=True, exist_ok=True)
    HOME.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen([str(KODI)], env=_env(), cwd=str(ROOT),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    PID_FILE.write_text(f"{proc.pid}\n")
    time.sleep(1)
    if proc.poll() is not None:
        PID_FILE.unlink(missing_ok=True)
        raise RuntimeError(f"Kodi exited during launch (status {proc.returncode})")


def stop(timeout_seconds: float = 15.0) -> None:
    info = status()
    if not info["running"]:
        return
    os.kill(int(info["pid"]), signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not status()["running"]:
            return
        time.sleep(0.1)
    raise RuntimeError("Kodi did not stop safely; inspect it before retrying")


def install(source: Path) -> None:
    verify_isolation()
    source = source.resolve()
    if not source.is_dir() or not _inside(source, PROJECT):
        raise RuntimeError("source must be inside the Backup Pro project")
    required = [source / "addon.xml", source / "default.py"]
    if any(not p.is_file() for p in required):
        raise RuntimeError("source is not a complete Backup Pro add-on")
    destination = KODI_ADDONS_DIR / ADDON_ID
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    allow = {"addon.xml", "default.py", "service.py", "icon.png", "fanart.jpg", "resources"}
    for name in allow:
        src = source / name
        if not src.exists():
            continue
        if src.is_symlink():
            raise RuntimeError(f"symlink is not allowed: {src}")
        dst = destination / name
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("tests", "__pycache__"))
        else:
            shutil.copy2(src, dst)


def _declared_dependencies(source: Path = PROJECT) -> list[str]:
    """The real add-on ids source/addon.xml declares as <requires>,
    excluding virtual xbmc.* platform dependencies (e.g. xbmc.python,
    xbmc.gui - Kodi's own built-in interfaces, not real installable
    add-ons; confirmed empirically 2026-09-10 against
    skin.arctic.fuse.3's own addon.xml, which declares xbmc.gui)."""
    import xml.etree.ElementTree as ET
    tree = ET.parse(source / "addon.xml")
    return [el.get("addon") for el in tree.getroot().findall("./requires/import")
            if not el.get("addon", "").startswith("xbmc.")]


def _copy_addon_closure(seed_ids: list[str]) -> list[str]:
    """Copy each of seed_ids - and every add-on any of them declares as
    a dependency, transitively - from the real, normal Kodi profile
    into the disposable profile: read-only from the real profile,
    confined write to the disposable profile only. An add-on already
    bundled inside the Kodi.app installation itself
    (KODI_SYSTEM_ADDONS_DIR) is skipped entirely - it's already visible
    to every profile, including a fresh disposable one, with no copying
    needed (confirmed empirically 2026-09-10: script.module.pil lives
    there, not in any per-profile addons/ directory - the earlier
    "missing dependency" finding was this function only ever checking
    the profile location). Refuses an add-on found in neither location,
    or a symlinked profile source. Shared by install_dependencies() and
    install_skin(); both need the same "make this and everything it
    transitively needs available" behavior."""
    verify_isolation()
    installed = []
    seen: set[str] = set()
    pending = list(seed_ids)
    while pending:
        addon_id = pending.pop(0)
        if addon_id in seen:
            continue
        seen.add(addon_id)
        system_source = KODI_SYSTEM_ADDONS_DIR / addon_id
        if system_source.is_dir():
            # already bundled with Kodi itself - nothing to copy, but
            # still walk its own declared dependencies in case one of
            # those needs to come from the real profile instead
            pending.extend(_declared_dependencies(system_source))
            continue
        real_source = NORMAL_APPDATA_DIR / "addons" / addon_id
        if not real_source.is_dir():
            raise RuntimeError(
                "add-on not found in the Kodi system bundle or the "
                f"real Kodi profile: {addon_id}")
        if real_source.is_symlink():
            raise RuntimeError(f"symlink is not allowed: {real_source}")
        destination = KODI_ADDONS_DIR / addon_id
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(real_source, destination, ignore=shutil.ignore_patterns("__pycache__"))
        installed.append(addon_id)
        pending.extend(_declared_dependencies(real_source))
    return installed


def install_dependencies(source: Path = PROJECT) -> list[str]:
    """Copy every add-on Backup Pro's addon.xml declares as a
    dependency - and every dependency of those dependencies,
    transitively - from the real, normal Kodi profile into the
    disposable profile. install() only copies Backup Pro's own files;
    without this, a triggered run fails with ModuleNotFoundError, first
    on Backup Pro's own direct dependencies and then, once those are
    present, on a transitive one (script.module.dropbox needs
    script.module.requests, which is not declared in Backup Pro's own
    addon.xml) - both confirmed empirically 2026-09-10, see
    docs/MAC_KODI_VALIDATION.md."""
    return _copy_addon_closure(_declared_dependencies(source))


def install_skin(skin_id: str = AF3_SKIN_ID) -> list[str]:
    """Copy a skin add-on (default: Arctic Fuse 3) and its own
    transitive dependencies from the real, normal Kodi profile into the
    disposable profile. Needed for Phase 9a step 4+: Backup Pro's AF3
    capture is gated on xbmc.getSkinDir() == AF3_ID (see
    resources/lib/backup.py), so AF3 must actually be present - and, via
    configure_webserver()'s extra_settings, made the active skin - not
    merely installed alongside it."""
    return _copy_addon_closure([skin_id])


def configure(addon_id: str, values: dict[str, str]) -> None:
    """Pre-seed disposable per-profile add-on settings before launch (a
    scripted, reversible edit confined to the disposable profile - not a
    live UI action). Any setting not listed here still resolves to that
    add-on's own declared default, exactly like a real, untouched
    install."""
    verify_isolation()
    if not values:
        raise RuntimeError("no settings given")
    settings_dir = KODI_USERDATA_DIR / "addon_data" / addon_id
    settings_dir.mkdir(parents=True, exist_ok=True)
    lines = ['<settings version="2">']
    for key, value in values.items():
        lines.append(f'    <setting id="{_xml_escape(key)}">{_xml_escape(str(value))}</setting>')
    lines.append('</settings>\n')
    (settings_dir / "settings.xml").write_text("\n".join(lines), encoding="utf-8")


def configure_webserver(port: int = WEBSERVER_PORT, username: str = WEBSERVER_USERNAME,
                         password: str = WEBSERVER_PASSWORD,
                         extra_settings: dict[str, str] | None = None) -> None:
    """Enable Kodi's built-in webserver (needed for JSON-RPC over HTTP)
    inside the disposable profile only, with authentication always on.
    extra_settings lets a caller fold in other core (guisettings.xml)
    settings in the SAME write - e.g. {"lookandfeel.skin": AF3_SKIN_ID}
    to make a skin active - since this only supports a fresh profile
    and cannot be called twice. Fails closed: refuses if guisettings.xml
    already exists, since a safe partial merge of an already-populated
    core-settings file isn't implemented here - always call this
    immediately after init()/reset(), before the disposable profile's
    first launch."""
    verify_isolation()
    if not password:
        raise RuntimeError("a webserver password is required")
    if KODI_GUISETTINGS_FILE.exists():
        raise RuntimeError(
            "guisettings.xml already exists; configure_webserver() only "
            "supports a fresh disposable profile - call it right after "
            "init()/reset(), before the first launch")
    KODI_USERDATA_DIR.mkdir(parents=True, exist_ok=True)
    values = {
        "services.webserver": "true",
        "services.webserverport": str(int(port)),
        "services.webserverauthentication": "true",
        "services.webserverusername": username,
        "services.webserverpassword": password,
    }
    if extra_settings:
        values.update({key: str(value) for key, value in extra_settings.items()})
    lines = ['<settings version="2">']
    for key, value in values.items():
        lines.append(f'    <setting id="{_xml_escape(key)}">{_xml_escape(str(value))}</setting>')
    lines.append('</settings>\n')
    KODI_GUISETTINGS_FILE.write_text("\n".join(lines), encoding="utf-8")


def jsonrpc(method: str, params: dict | None = None, port: int = WEBSERVER_PORT,
            username: str = WEBSERVER_USERNAME, password: str = WEBSERVER_PASSWORD,
            timeout: float = 10.0) -> dict:
    """POST a JSON-RPC 2.0 request to the disposable Kodi instance's
    webserver. Always targets 127.0.0.1 (loopback) on the given port -
    this harness has no concept of, and never accepts, a remote host."""
    payload: dict = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{int(port)}/jsonrpc", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request.add_header("Authorization", f"Basic {credentials}")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"JSON-RPC request failed: {exc}") from exc


def enable_addon(addon_id: str, **jsonrpc_kwargs) -> dict:
    """Enable an installed add-on via JSON-RPC. A freshly install()-ed
    add-on is not enabled by default, and Addons.ExecuteAddon fails
    against a disabled add-on (confirmed empirically 2026-09-10) - call
    this before execute_addon() for a just-installed add-on."""
    return jsonrpc("Addons.SetAddonEnabled",
                    {"addonid": addon_id, "enabled": True}, **jsonrpc_kwargs)


def enable_addons(addon_ids: list[str], **jsonrpc_kwargs) -> None:
    """enable_addon() for each of addon_ids. A skin has the same
    disabled-by-default problem as any other freshly copied add-on, and
    additionally will not actually load while any of its own hard
    dependencies are still disabled too - confirmed empirically
    2026-09-10 activating skin.arctic.fuse.3, whose install_skin() copy
    (and every add-on it depends on) starts disabled. Enabling them
    live does not retroactively fix an already-failed boot-time skin
    load; follow this with restart() for a clean load with everything
    already enabled (confirmed empirically: a live
    Settings.SetSettingValue afterward did not trigger a real skin
    reload, but stopping and relaunching did)."""
    for addon_id in addon_ids:
        enable_addon(addon_id, **jsonrpc_kwargs)


def execute_addon(addon_id: str, params: object = None, **jsonrpc_kwargs) -> dict:
    """Invoke Addons.ExecuteAddon for addon_id via JSON-RPC - the
    documented way to trigger a Program add-on non-interactively,
    without a human selecting it from the main menu."""
    rpc_params: dict = {"addonid": addon_id}
    if params is not None:
        rpc_params["params"] = params
    return jsonrpc("Addons.ExecuteAddon", rpc_params, **jsonrpc_kwargs)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "status", "init", "reset", "launch", "stop", "restart"):
        sub.add_parser(name)
    p = sub.add_parser("install")
    p.add_argument("source", nargs="?", type=Path, default=PROJECT)
    p = sub.add_parser("install-dependencies")
    p.add_argument("source", nargs="?", type=Path, default=PROJECT)
    p = sub.add_parser("install-skin")
    p.add_argument("skin_id", nargs="?", default=AF3_SKIN_ID)
    p = sub.add_parser("configure")
    p.add_argument("addon_id")
    p.add_argument("settings", nargs="+", help="key=value pairs")
    p = sub.add_parser("enable-webserver")
    p.add_argument("--skin", default=None,
                    help="also set this skin as active (lookandfeel.skin)")
    p = sub.add_parser("jsonrpc")
    p.add_argument("method")
    p.add_argument("params", nargs="?", type=json.loads, default=None,
                    help="JSON object, e.g. '{\"addonid\":\"script.backup.pro\"}'")
    p = sub.add_parser("enable-addon")
    p.add_argument("addon_id", nargs="?", default=ADDON_ID)
    p = sub.add_parser("enable-addons")
    p.add_argument("addon_ids", nargs="+")
    p = sub.add_parser("execute-addon")
    p.add_argument("addon_id", nargs="?", default=ADDON_ID)
    p.add_argument("params", nargs="*", help="key=value pairs forwarded as sys.argv")
    args = parser.parse_args(argv)
    try:
        if args.command == "verify": result = verify_isolation()
        elif args.command == "status": result = status()
        elif args.command == "init": reset(); result = status()
        elif args.command == "reset": reset(); result = status()
        elif args.command == "launch": launch(); result = status()
        elif args.command == "stop": stop(); result = status()
        elif args.command == "restart": stop(); launch(); result = status()
        elif args.command == "install": install(args.source); result = status()
        elif args.command == "install-dependencies":
            result = {"installed": install_dependencies(args.source)}
        elif args.command == "install-skin":
            result = {"installed": install_skin(args.skin_id)}
        elif args.command == "configure":
            values = dict(item.split("=", 1) for item in args.settings)
            configure(args.addon_id, values)
            result = status()
        elif args.command == "enable-webserver":
            extra = {"lookandfeel.skin": args.skin} if args.skin else None
            configure_webserver(extra_settings=extra)
            result = status()
        elif args.command == "jsonrpc":
            result = jsonrpc(args.method, args.params)
        elif args.command == "enable-addon":
            result = enable_addon(args.addon_id)
        elif args.command == "enable-addons":
            enable_addons(args.addon_ids)
            result = {"enabled": args.addon_ids}
        else:
            result = execute_addon(args.addon_id, args.params or None)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"kodi-test: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
