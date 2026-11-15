"""Client glue for F4 with moons: plugs into RoomController(local_planet_hash=..., fetch_planet=...).

Flow: beacon hash != local hash -> fetch from host (signature + beacon hash checked) -> install into moons.d
-> restart ZeroTier. The beacon's `planet_hash` is the hash of the host's moon file; `moon_id` names it.
"""
from __future__ import annotations
import asyncio, json, tempfile
from pathlib import Path
from ..trust.planet_transport import fetch_planet, PLANET_PORT
from ..trust.moon import moon_filename, MoonError
from silent_tunnel.moon import current_moon_hash, install_moon


class MoonSync:
    def __init__(self, pub_b64, home=None, state_file=None, installer=install_moon, fetch=fetch_planet):
        self.pub, self.home, self.installer, self.fetch = pub_b64, home, installer, fetch
        self.state_file = Path(state_file) if state_file else None
        self.moon_id = self._load()
        self.last_error = ""

    def _load(self):
        try:
            return json.loads(self.state_file.read_text()).get("moon_id") if self.state_file else None
        except (OSError, ValueError, AttributeError):
            return None

    def _save(self):
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({"moon_id": self.moon_id}))

    def local_hash(self) -> str:
        return current_moon_hash(self.moon_id, self.home) if self.moon_id else ""

    async def fetch_and_install(self, beacon: dict) -> bool:
        self.last_error = ""
        try:
            moon_id = beacon["moon_id"]
            moon_filename(moon_id)                       # reject junk ids before touching disk
        except (KeyError, MoonError) as exc:
            self.last_error = str(exc) or "The Host announcement is missing its room network file ID."
            return False
        with tempfile.TemporaryDirectory(prefix="lantool-fetch-") as td:
            dest = Path(td) / "moon"
            ok = await self.fetch(beacon["host"], beacon.get("planet_port", PLANET_PORT), self.pub,
                                  beacon["planet_hash"], dest)
            if not ok:
                self.last_error = ("Couldn't download or verify the Host's signed room network file. "
                                   "Confirm the Host is open and its connection allows TCP port 47802.")
                return False
            data = dest.read_bytes()
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: self.installer(moon_id, data, self.home))
        except Exception as exc:
            self.last_error = str(exc) or "Windows couldn't install the room network file."
            return False
        self.moon_id = moon_id.lower()
        self._save()
        return True
