"""Signed LAN beacon (F2). Pure helpers + asyncio UDP sender/listener."""
from __future__ import annotations
import asyncio, json, re, socket, time, ipaddress
from .._version import __proto__ as PROTO_VERSION
from ..trust.signing import sign_obj, verify_obj

BEACON_PORT = 47801
REPLAY_WINDOW_S = 30


def make_beacon(priv_b64, net_id, host, presence_port, planet_hash, version, now=None,
                host_zt=None, planet_port=None, moon_id=None, sync_port=None,
                host_pub=None, pc_name=None, proto=None, admission=None, launcher_share=None) -> bytes:
    body = {"v": 1, "net_id": net_id, "host": host, "presence_port": presence_port,
            "planet_hash": planet_hash, "version": version, "ts": int(now or time.time()),
            # 'proto' is signed with the rest; beacons without it count as proto 1.
            "proto": PROTO_VERSION if proto is None else int(proto)}
    if host_zt:
        body["host_zt"] = host_zt            # host's ZeroTier IP (presence moves here after the switch)
    if planet_port:
        body["planet_port"] = planet_port
    if moon_id:
        body["moon_id"] = moon_id            # `planet_hash` then hashes the moon file with this id
    if sync_port:
        body["sync_port"] = sync_port
    if host_pub:
        body["host_pub"] = host_pub
    if pc_name:
        body["pc_name"] = str(pc_name)[:64]
    if admission is not None:
        body["admission"] = admission
    if launcher_share is not None:
        body['launcher_share'] = launcher_share
    return json.dumps(sign_obj(priv_b64, body)).encode()


def parse_beacon(data: bytes, pub_b64: str | None = None, now=None):
    """Return beacon dict if signature + freshness valid, else None."""
    try:
        obj = json.loads(data)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    signer = pub_b64 or obj.get("host_pub")
    if not isinstance(signer, str) or not verify_obj(signer, obj):
        return None
    if 'launcher_share' in obj:
        meta = obj['launcher_share']
        if (not isinstance(meta, dict) or type(meta.get('protocol')) is not int or meta.get('protocol') != 1 or type(meta.get('port')) is not int
                or not 1 <= meta['port'] <= 65535 or not isinstance(meta.get('fingerprint'), str)
                or not re.fullmatch(r'[0-9a-f]{64}', meta['fingerprint'])):
            return None
        if 'host' in meta:
            try:
                ipaddress.ip_address(meta['host'])
            except (ValueError, TypeError):
                return None
    if "admission" in obj:
        admission = obj["admission"]
        if (not isinstance(admission, dict) or admission.get("required") is not True
                or type(admission.get("port")) is not int or not 1 <= admission["port"] <= 65535
                or not isinstance(admission.get("fingerprint"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", admission["fingerprint"])):
            return None
    try:
        if abs((now or time.time()) - obj.get("ts", 0)) > REPLAY_WINDOW_S:
            return None
    except TypeError:
        return None
    return obj


class BeaconSender:
    def __init__(self, payload_fn, port=BEACON_PORT, interval=1.0, target="255.255.255.255"):
        self.payload_fn, self.port, self.interval, self.target = payload_fn, port, interval, target
        self._task = None

    def start(self):
        self._task = asyncio.create_task(self._run())

    def stop(self):
        if self._task:
            self._task.cancel()

    async def _run(self):
        from .discovery import broadcast_targets
        targets = [self.target]
        refresh = None
        refresh_at = 0
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.setblocking(False)
        try:
            while True:
                if self.target == '255.255.255.255':
                    if refresh is None and time.monotonic() >= refresh_at:
                        refresh = asyncio.get_running_loop().run_in_executor(None, broadcast_targets)
                    if refresh is not None and refresh.done():
                        try:
                            targets = refresh.result()
                        except Exception:
                            pass
                        refresh, refresh_at = None, time.monotonic() + 15
                payload = self.payload_fn()
                for target in targets:
                    try:
                        s.sendto(payload, (target, self.port))
                    except OSError:
                        continue
                await asyncio.sleep(self.interval)
        finally:
            s.close()


class BeaconListener(asyncio.DatagramProtocol):
    """on_room(beacon_dict) called for every valid beacon."""
    def __init__(self, pub_b64, on_room):
        self.pub, self.on_room = pub_b64, on_room
        self._probe_task = None

    def connection_made(self, transport):
        self._probe_task = asyncio.create_task(self._probe(transport))

    def connection_lost(self, exc):
        if self._probe_task:
            self._probe_task.cancel()

    async def _probe(self, transport):
        from .discovery import QUERY, QUERY_PORT, broadcast_targets
        local = transport.get_extra_info('sockname')
        loopback_only = bool(local and str(local[0]).startswith('127.'))
        targets = ['127.0.0.1'] if loopback_only else ['255.255.255.255', '127.0.0.1']
        refresh = None
        refresh_at = 0
        started = time.monotonic()
        # Windows reports ICMP port-unreachable errors to the sending UDP socket.
        # Probe on a separate socket so an unanswered query cannot disrupt the
        # listener that receives signed room announcements.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            while not transport.is_closing():
                if not loopback_only and refresh is None and time.monotonic() >= refresh_at:
                    refresh = asyncio.get_running_loop().run_in_executor(None, broadcast_targets)
                if refresh is not None and refresh.done():
                    try:
                        targets = refresh.result()
                    except Exception:
                        pass
                    refresh, refresh_at = None, time.monotonic() + 15
                for target in targets:
                    try:
                        probe.sendto(QUERY, (target, QUERY_PORT))
                    except OSError:
                        continue
                await asyncio.sleep(0.5 if time.monotonic() - started < 10 else 2)
        finally:
            probe.close()

    def datagram_received(self, data, addr):
        b = parse_beacon(data, self.pub)
        if b:
            self.on_room(b)

    @classmethod
    async def listen(cls, pub_b64, on_room, port=BEACON_PORT, host="0.0.0.0"):
        return await cls._listen(pub_b64, on_room, port, host)

    @classmethod
    async def listen_discovered(cls, on_room, port=BEACON_PORT, host="0.0.0.0"):
        """Listen for host announcements that carry their self-authenticating key."""
        return await cls._listen(None, on_room, port, host)

    @classmethod
    async def _listen(cls, pub_b64, on_room, port, host):
        loop = asyncio.get_running_loop()
        try:
            transport, proto = await loop.create_datagram_endpoint(
                lambda: cls(pub_b64, on_room), local_addr=(host, port),
                allow_broadcast=True, reuse_port=None)
        except OSError:
            # Active queries receive unicast replies on this socket, so a
            # second client can discover rooms even if the beacon port is busy.
            if port != BEACON_PORT:
                raise
            transport, proto = await loop.create_datagram_endpoint(
                lambda: cls(pub_b64, on_room), local_addr=(host, 0), allow_broadcast=True)
        return transport
