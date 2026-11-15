"""Where the launcher keeps its state: %LOCALAPPDATA%\\LanTool, or beside the app in portable mode.

Portable mode is on when a file named ``portable.flag`` sits next to the app (the folder of the
frozen .exe, or the Source folder when run from source). Then all launcher state lives in
``LanTool-data`` beside the app, so the whole folder can move between PCs or run from a USB stick.
The machine-wide VLA service/driver is not affected: it is installed once per PC by Prepare this PC.
Stdlib only, so it can be imported from anywhere without a cycle.
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
import tempfile
from pathlib import Path

FLAG_NAME = "portable.flag"
PORTABLE_DIR = "LanTool-data"
MIN_FREE_MB = 50   # below this the data folder is reported as (nearly) full
# State items the VLA wipe removes (relative to the data folder). Single source for wipe.py.
VLA_STATE_ITEMS = ("vla-state", "vla-installed-by-lantool", "vla-adapter-state.json")


def app_root() -> Path:
    """Folder that holds the app: the .exe folder when frozen, else the Source folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def is_portable(root=None) -> bool:
    return (Path(root) if root else app_root()).joinpath(FLAG_NAME).is_file()


def default_app_dir(root=None, environ=None) -> Path:
    """Launcher state folder (not created here)."""
    env = os.environ if environ is None else environ
    base = Path(root) if root else app_root()
    if is_portable(base):
        return base / PORTABLE_DIR
    return Path(env.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "LanTool"


# --- health of the data folder (never raises) ------------------------------------------------

def _nearest_existing(path: Path) -> Path | None:
    """The folder itself, or its closest existing ancestor (where it would be created)."""
    cur = Path(path)
    for _ in range(64):
        try:
            if cur.is_dir():
                return cur
        except OSError:
            return None
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def _probe_write(folder: Path) -> str | None:
    """Create, write and delete a tiny temp file in ``folder``.

    Returns None when it works, "full" when the disk is out of space, otherwise "denied".
    (os.access is unreliable on Windows, so we really write.)
    """
    try:
        with tempfile.NamedTemporaryFile(dir=str(folder), prefix=".lantool-probe-", delete=True) as probe:
            probe.write(b"x" * 512)
            probe.flush()
    except OSError as exc:
        if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
            return "full"
        return "denied"
    return None


def _free_mb(folder: Path) -> int | None:
    try:
        return int(shutil.disk_usage(str(folder)).free // (1024 * 1024))
    except (OSError, ValueError):
        return None


def _inspect(root=None, environ=None) -> dict:
    base = Path(root) if root else app_root()
    data = default_app_dir(base, environ)
    info = {"portable": is_portable(base), "path": data, "is_file": False,
            "writable": False, "free_mb": None, "failure": None}
    try:
        info["is_file"] = data.is_file()
    except OSError:
        pass
    if info["is_file"]:
        return info
    probe_dir = _nearest_existing(data)
    if probe_dir is None:
        info["failure"] = "denied"
        return info
    info["failure"] = _probe_write(probe_dir)
    info["writable"] = info["failure"] is None
    info["free_mb"] = _free_mb(probe_dir)
    return info


def describe(root=None, environ=None) -> dict:
    """``{portable, data_dir, writable, free_mb}``; never raises.

    ``writable`` is a real write probe (in the nearest existing folder if the data folder is not
    created yet; the probe file is removed again and the data folder is NOT created).
    ``free_mb`` is None when the free space cannot be read.
    """
    try:
        info = _inspect(root, environ)
    except Exception:   # defensive: describe() feeds diagnostics and must not crash the UI
        base = Path(root) if root else app_root()
        return {"portable": False, "data_dir": str(default_app_dir(base, environ)),
                "writable": False, "free_mb": None}
    return {"portable": info["portable"], "data_dir": str(info["path"]),
            "writable": info["writable"], "free_mb": info["free_mb"]}


def data_dir_problem(root=None, environ=None, min_free_mb: int = MIN_FREE_MB) -> str | None:
    """A plain-English reason the data folder cannot be used, or None when it is fine. Never raises."""
    try:
        info = _inspect(root, environ)
    except Exception as exc:
        return f"Could not check the LAN Launcher data folder: {exc}"
    where = str(info["path"])
    fix = ("Move the app to a writable folder or drive, or delete portable.flag to keep data on this PC instead."
           if info["portable"] else
           "Check the folder's permissions or the disk, then restart LAN Launcher.")
    if info["is_file"]:
        return f"The data folder path ({where}) is a file, not a folder. Rename or remove that file."
    if info["failure"] == "full":
        return f"The disk holding the data folder ({where}) is full. Free up space and restart LAN Launcher."
    if not info["writable"]:
        return f"The data folder ({where}) is read-only or not writable. {fix}"
    free = info["free_mb"]
    if free is not None and free < min_free_mb:
        return (f"Only {free} MB is free on the disk holding the data folder ({where}). "
                f"Free up space to avoid failed saves.")
    return None


def wipe_targets(root=None, environ=None) -> dict:
    """What the VLA wipe may delete in launcher state.

    Portable: only ``{portable data dir}\\<VLA_STATE_ITEMS>``; nothing under any user profile.
    Not portable: ``AppData\\Local\\LanTool\\<VLA_STATE_ITEMS>`` for every profile (wipe.py does the
    profile walk, because it may run elevated as another user).
    """
    base = Path(root) if root else app_root()
    if is_portable(base):
        return {"portable": True, "data_dir": str(base / PORTABLE_DIR), "items": list(VLA_STATE_ITEMS)}
    return {"portable": False, "data_dir": None, "items": list(VLA_STATE_ITEMS)}
