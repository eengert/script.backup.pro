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
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / ".kodi-test"
HOME = ROOT / "home"
KODI_APPDATA_DIR = HOME / "Library" / "Application Support" / "Kodi"
KODI_USERDATA_DIR = KODI_APPDATA_DIR / "userdata"
KODI_ADDONS_DIR = KODI_APPDATA_DIR / "addons"
KODI_LOG_FILE = HOME / "Library" / "Logs" / "kodi.log"
PID_FILE = ROOT / "kodi.pid"
KODI = Path("/Applications/Kodi.app/Contents/MacOS/Kodi")
ADDON_ID = "script.backup.pro"

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


def stop() -> None:
    info = status()
    if not info["running"]:
        return
    os.kill(int(info["pid"]), signal.SIGTERM)
    for _ in range(50):
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


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("verify", "status", "init", "reset", "launch", "stop", "restart"):
        sub.add_parser(name)
    p = sub.add_parser("install")
    p.add_argument("source", nargs="?", type=Path, default=PROJECT)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify": result = verify_isolation()
        elif args.command == "status": result = status()
        elif args.command == "init": reset(); result = status()
        elif args.command == "reset": reset(); result = status()
        elif args.command == "launch": launch(); result = status()
        elif args.command == "stop": stop(); result = status()
        elif args.command == "restart": stop(); launch(); result = status()
        else: install(args.source); result = status()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"kodi-test: refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
