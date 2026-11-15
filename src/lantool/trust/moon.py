"""Host-side moon generation (F4, replaces the non-existent "offline planet prototype").

A ZeroTier *moon* is a signed list of extra root servers. The host's own ZeroTier node is the moon root
(and also the network controller), so offline clients can find the controller with no internet.
The file is produced by ZeroTier's own `zerotier-idtool initmoon` / `genmoon`; we never touch the crypto.
The host's current LAN address goes into `stableEndpoints`, so the moon is regenerated whenever that changes.
Our Ed25519 signature (signing.sign_planet, served by PlanetServer) is the trust layer on top.
"""
from __future__ import annotations
import json, re, shutil, subprocess, tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ZT_PORT = 9993
_HEX = re.compile(r"^[0-9a-fA-F]{10,16}$")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# runner(cmd: list[str], cwd: Path) -> (returncode, stdout, stderr)
Runner = Callable[[list, Path], tuple]


class MoonError(Exception):
    pass


@dataclass(frozen=True)
class MoonFile:
    moon_id: str      # lowercase hex, 10-16 digits, as in moon.json "id"
    data: bytes

    @property
    def filename(self) -> str:
        return moon_filename(self.moon_id)


def moon_filename(moon_id: str) -> str:
    """ZeroTier loads moons.d/<16 hex digits>.moon."""
    if not _HEX.match(moon_id or ""):
        raise MoonError(f"bad moon id {moon_id!r}")
    return f"{int(moon_id, 16):016x}.moon"


def _default_runner(cmd, cwd):
    p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=60,
                       creationflags=_NO_WINDOW)
    return p.returncode, p.stdout, p.stderr


def find_idtool(home: Path) -> list:
    """Command prefix for zerotier-idtool. On Windows the install ships `zerotier-idtool.bat` in the data
    folder; the service binary also accepts `-i`. (Both UNVERIFIED on a real PC; see HANDOFF.)"""
    home = Path(home)
    for name in ("zerotier-idtool.bat", "zerotier-idtool.exe", "zerotier-idtool"):
        if (home / name).exists():
            return [str(home / name)]
    found = shutil.which("zerotier-idtool")
    if found:
        return [found]
    for name in ("zerotier-one_x64.exe", "zerotier-one"):
        for base in (home, Path(shutil.which(name) or "").parent):
            if (base / name).exists():
                return [str(base / name), "-i"]
    raise MoonError("Couldn't find ZeroTier's idtool. Is ZeroTier installed?")


def build_moon(home: Path, endpoints: list, runner: Optional[Runner] = None,
               idtool: Optional[list] = None) -> MoonFile:
    """endpoints: ["192.168.1.20/9993", ...] (host LAN IPs; ZeroTier's own port). Returns the signed moon."""
    home, run = Path(home), runner or _default_runner
    if not endpoints:
        raise MoonError("no endpoints for the moon")
    ident = home / "identity.public"
    if not ident.exists():
        raise MoonError("ZeroTier has no identity yet (service never started?)")
    tool = idtool or find_idtool(home)
    with tempfile.TemporaryDirectory(prefix="lantool-moon-") as td:
        td = Path(td)
        rc, out, err = run(tool + ["initmoon", str(ident)], td)
        if rc != 0:
            raise MoonError(f"initmoon failed: {err.strip() or rc}")
        try:
            spec = json.loads(out)
            spec["roots"][0]["stableEndpoints"] = list(endpoints)
            moon_id = str(spec["id"]).lower()
        except (ValueError, KeyError, IndexError, TypeError):
            raise MoonError("initmoon gave unexpected output") from None
        moon_filename(moon_id)                                    # validates the id
        (td / "moon.json").write_text(json.dumps(spec))
        rc, out, err = run(tool + ["genmoon", "moon.json"], td)
        if rc != 0:
            raise MoonError(f"genmoon failed: {err.strip() or rc}")
        made = sorted(td.glob("*.moon"))
        if len(made) != 1:
            raise MoonError("genmoon produced no .moon file")
        return MoonFile(moon_id, made[0].read_bytes())
