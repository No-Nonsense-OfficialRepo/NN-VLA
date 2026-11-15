"""Host side of a room (F2/F4 wiring): build the moon, serve it signed, broadcast the beacon, rebuild if the LAN IP changes."""
from __future__ import annotations
import asyncio
from pathlib import Path
from ..trust import signing as S
from ..trust.moon import build_moon, ZT_PORT
from ..trust.planet_transport import PlanetServer, PLANET_PORT
from .beacon import make_beacon, BeaconSender, BEACON_PORT
from .._version import __proto__ as PROTO_VERSION
SYNC_PORT = 47803  # Legacy beacon field; the tunnel-only app does not serve games.

HOST_DISCOVERY_PORT = 47804


class HostRoom:
    def __init__(self, priv, zt_home, work_dir, net_id, ip_fn, version, presence_port=47800, host_zt=None,
                 build=None, planet_port=PLANET_PORT, beacon_target="255.255.255.255",
                 beacon_port=BEACON_PORT, serve_host="0.0.0.0", recheck_s=5.0,
                 sync_port=SYNC_PORT, host_pub=None, pc_name=None, admission=None, launcher_share=None):
        self.priv, self.zt_home, self.work = priv, Path(zt_home), Path(work_dir)
        self.net_id, self.ip_fn, self.version = net_id, ip_fn, version
        self.presence_port, self.host_zt, self.build = presence_port, host_zt, build
        self.sync_port = sync_port
        self.host_pub, self.pc_name = host_pub, pc_name
        self.admission = admission
        self.launcher_share = launcher_share
        self.recheck_s = recheck_s
        self.server = PlanetServer(priv, self.work / "moon", host=serve_host, port=planet_port)
        self.sender = BeaconSender(self.payload, port=beacon_port, target=beacon_target)
        # A separate signed loopback announcement lets a Client running on
        # this same PC connect even when its copied executable has different
        # Windows Firewall rules from the Host executable.
        self.local_sender = BeaconSender(self.local_payload, port=beacon_port, target="127.0.0.1")
        self.conflict_sender = BeaconSender(self.payload, port=HOST_DISCOVERY_PORT, target=beacon_target)
        self.ip = self.moon_id = None
        self.moon_hash = ""
        self._task = None
        self.responder = None
        from .discovery import source_ip_for
        self.server.room_payload = lambda peer: self._payload_for(source_ip_for(peer))

    def refresh(self) -> bool:
        """(Re)build the moon if the LAN IP changed. Returns True if it was rebuilt."""
        ip = self.ip_fn()
        if not ip:
            return False
        if ip == self.ip:
            return False
        if self.build is None:
            self.ip, self.moon_id, self.moon_hash = ip, None, ""
            return True
        moon = self.build(self.zt_home, [f"{ip}/{ZT_PORT}"])
        self.work.mkdir(parents=True, exist_ok=True)
        tmp = self.work / "moon.tmp"
        tmp.write_bytes(moon.data)
        tmp.replace(self.work / "moon")          # server reads this per request; swap is atomic
        self.ip, self.moon_id, self.moon_hash = ip, moon.moon_id, S.sha256_hex(moon.data)
        return True

    def payload(self) -> bytes:
        return self._payload_for(self.ip)

    def local_payload(self) -> bytes:
        return self._payload_for("127.0.0.1")

    def _payload_for(self, address) -> bytes:
        return make_beacon(self.priv, self.net_id, address, self.presence_port, self.moon_hash, self.version,
                           host_zt=self.host_zt, planet_port=self.server.port, moon_id=self.moon_id,
                           sync_port=self.sync_port, host_pub=self.host_pub, pc_name=self.pc_name,
                           proto=PROTO_VERSION,
                           admission=self.admission.metadata if self.admission else None,
                           launcher_share={**self.launcher_share, 'host': self.ip_fn()} if self.launcher_share else None)

    async def start(self):
        if not self.refresh():
            raise RuntimeError("no LAN address to open a room on")
        await self.server.start()
        from .discovery import DiscoveryResponder
        try:
            self.responder = await DiscoveryResponder.start(self._payload_for)
        except OSError:
            await self.server.stop()
            raise RuntimeError('Host discovery port 47806 is in use. Close any other Host app and retry.')
        self.sender.start()
        self.local_sender.start()
        self.conflict_sender.start()
        self._task = asyncio.create_task(self._watch())

    async def _watch(self):
        while True:
            await asyncio.sleep(self.recheck_s)
            try:
                self.refresh()
            except Exception:
                pass                              # keep the old moon; next tick retries

    async def stop(self):
        if self.responder:
            self.responder.close()
            self.responder = None
        if self._task:
            self._task.cancel()
        self.sender.stop()
        self.local_sender.stop()
        self.conflict_sender.stop()
        await self.server.stop()
