"""Install a verified moon on a client: drop it in ZeroTier's `moons.d` and restart the service.

Same trust rule as planet.py: the caller must have verified our signature first (lantool.trust.planet_transport).
Moons are additive (the default planet stays), so a bad file can't lock the client out of normal ZeroTier.
"""
from __future__ import annotations
import hashlib, os
from pathlib import Path
from typing import Callable, Optional

from .errors import InstallError
from .local import default_home
from .installer import is_windows
from .admin_actions import run_elevated
from .planet import _restart_service


def moon_path(moon_id: str, home: Optional[Path] = None) -> Path:
    from lantool.trust.moon import moon_filename   # filename rule lives in one place
    return Path(home or default_home()) / "moons.d" / moon_filename(moon_id)


def current_moon_hash(moon_id: str, home: Optional[Path] = None) -> str:
    """sha256 hex of the installed moon, or '' (matches beacon `planet_hash`)."""
    try:
        return hashlib.sha256(moon_path(moon_id, home).read_bytes()).hexdigest()
    except OSError:
        return ""


def install_moon(moon_id: str, data: bytes, home: Optional[Path] = None, restart: bool = True,
                 elevated_copy: Optional[Callable[[bytes, Path], None]] = None) -> None:
    target = moon_path(moon_id, home)
    restarted = False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(".new")
        staging.write_bytes(data)
        os.replace(staging, target)
    except PermissionError:
        if elevated_copy:
            elevated_copy(data, target)
        else:
            _elevated_copy(data, target, restart=restart)
            restarted = restart
    except OSError as e:
        raise InstallError(f"Couldn't update the room's network file ({e.strerror}).") from None
    if restart and not restarted:
        _restart_service()


def _elevated_copy(data: bytes, target: Path, restart: bool = False) -> None:
    if not is_windows():
        raise InstallError("Silent Tunnel needs administrator rights to update the network file.")
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=".moon") as f:
        f.write(data)
        tmp = f.name
    try:
        try:
            run_elevated("install_room_file", kind="moon", source=tmp,
                         filename=target.name, restart=restart)
        except (OSError, RuntimeError) as exc:
            raise InstallError(f"Couldn't install the room network file: {exc}") from None
    finally:
        os.unlink(tmp)
