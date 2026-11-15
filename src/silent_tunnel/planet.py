"""Install a custom planet file so the local controller works with no internet (offline LAN).

VLA reads `<home>/planet`. Replacing it needs write access to VLA's data folder and a
service restart, so on Windows this uses one elevated PowerShell call (one UAC prompt).
The caller must have verified the file's signature first (lantool.trust.planet_transport).
"""
from __future__ import annotations
import hashlib, os
from pathlib import Path
from typing import Callable, Optional

from .errors import InstallError
from .local import default_home
from .installer import is_windows
from .admin_actions import run_elevated


def current_planet_hash(home: Optional[Path] = None) -> str:
    """sha256 hex of the installed planet file, or '' if none (matches beacon `planet_hash`)."""
    try:
        return hashlib.sha256((Path(home or default_home()) / "planet").read_bytes()).hexdigest()
    except OSError:
        return ""


def install_planet(data: bytes, home: Optional[Path] = None, restart: bool = True,
                   elevated_copy: Optional[Callable[[Path, Path], None]] = None) -> None:
    """Replace the planet file (keeping `planet.orig` once) and restart the VLA service."""
    home = Path(home or default_home())
    target, orig = home / "planet", home / "planet.orig"
    staging = home / "planet.new"
    try:
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.write_bytes(data)
        if target.exists() and not orig.exists():
            orig.write_bytes(target.read_bytes())
        os.replace(staging, target)
    except PermissionError:
        if elevated_copy:
            elevated_copy(data, target)
        else:
            _elevated_copy(data, target, restart=restart)
    except OSError as e:
        raise InstallError(f"Couldn't update the room's network file ({e.strerror}).") from None
    if restart:
        _restart_service()


def _elevated_copy(data: bytes, target: Path, restart: bool = True) -> None:
    if not is_windows():
        raise InstallError("Silent Tunnel needs administrator rights to update the network file.")
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".planet") as f:
        f.write(data)
        tmp = f.name
    try:
        try:
            run_elevated("install_room_file", kind="planet", source=tmp, restart=restart)
        except (OSError, RuntimeError) as exc:
            raise InstallError(f"Couldn't install the room network file: {exc}") from None
    finally:
        os.unlink(tmp)


def _restart_service() -> None:
    if not is_windows():
        return  # Linux/macOS: caller restarts zerotier-one themselves
    try:
        run_elevated("restart_service")
    except (OSError, RuntimeError) as exc:
        raise InstallError(f"Couldn't restart VLA after updating the network file: {exc}") from None
