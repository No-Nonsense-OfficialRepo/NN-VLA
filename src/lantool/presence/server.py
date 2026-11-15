"""Host presence server (asyncio, newline-delimited JSON over TCP)."""
from __future__ import annotations
import asyncio, hashlib, json, time
from .state import PresenceState, HEARTBEAT_S, TIMEOUT_S, MAX_PLAYERS
from .._version import __version__, __proto__
from ..netcore import compat

DEFAULT_PORT = 47800


class PresenceServer:
    def __init__(self, host="0.0.0.0", port=DEFAULT_PORT, on_change=None, *, admission=None,
                 host_name="", host_avatar=""):
        self.host, self.port = host, port
        self.host_profile = {"name": host_name, "avatar": host_avatar}
        self.state = PresenceState()
        self.writers: dict[str, asyncio.StreamWriter] = {}
        self._credentials: dict[str, tuple[str, str]] = {}
        self.seeders: dict[str, dict] = {}
        self.subscribers: set[str] = set()
        # Per subscriber: {player_id: avatar_hash} already sent, so a roster carries each avatar only once.
        self._avatars_sent: dict[str, dict[str, str]] = {}
        self.pings: dict[str, tuple[str, float]] = {}
        self.latencies: dict[str, tuple[int, float]] = {}
        self.on_change = on_change  # callback(counts, roster) for the host UI
        self.admission = admission
        self._server = None
        self._dirty = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        tls = self.admission.listener_options() if self.admission else {}
        self._server = await asyncio.start_server(self._handle, self.host, self.port, **tls)
        self.port = self._server.sockets[0].getsockname()[1]
        self._tasks = [asyncio.create_task(self._broadcaster()), asyncio.create_task(self._reaper())]

    async def stop(self):
        for t in self._tasks:
            t.cancel()
        for w in list(self.writers.values()):
            w.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def set_host_profile(self, name, avatar):
        self.host_profile = {"name": name, "avatar": avatar}
        self._dirty.set()

    async def kick(self, player_id):
        """Remove one current member and tell its launcher to leave the room."""
        writer = self.writers.get(player_id)
        if not writer:
            return False
        try:
            writer.write(self._enc({'type': 'kicked', 'message': 'The Host removed you from the room.'}))
            await asyncio.wait_for(writer.drain(), 2)
        except (OSError, ConnectionError, asyncio.TimeoutError):
            pass
        writer.close()
        return True

    @staticmethod
    def _enc(obj) -> bytes:
        return (json.dumps(obj, separators=(",", ":")) + "\n").encode()

    async def _handle(self, reader, writer):
        pid = None
        try:
            line = await asyncio.wait_for(reader.readline(), TIMEOUT_S)
            hello = json.loads(line)
            if not isinstance(hello, dict):
                return
            if hello.get("type") != "hello" or not hello.get("player_id"):
                return
            player_id = str(hello["player_id"])[:64]
            token = hello.get("admission_token")
            peername = writer.get_extra_info("peername") or ("", 0)
            peer_ip = str(peername[0])
            if self.admission and not self.admission.permits(token, player_id, ip=peer_ip):
                return
            compatible, _, message, hint = compat.check(__version__, __proto__, hello.get('launcher_version'), compat.proto_of(hello))
            if not compatible:
                writer.write(self._enc({'type': 'error', 'code': 'version_mismatch', 'message': message, 'hint': hint}))
                await writer.drain()
                return
            pid = player_id
            if pid not in self.writers and len(self.writers) >= MAX_PLAYERS:
                return
            old = self.writers.pop(pid, None)
            if old:
                old.close()
            self.state.hello(pid, str(hello.get("name", pid))[:32], avatar=hello.get("avatar", ""))
            self.writers[pid] = writer
            if self.admission:
                self._credentials[pid] = token, peer_ip
            self._avatars_sent.pop(pid, None)  # fresh connection: nothing sent yet
            self.pings.pop(pid, None)
            self.latencies.pop(pid, None)
            self._ping(pid, writer)
            writer.write(self._enc({"type": "welcome", "server_time": time.time(),
                                    "launcher_version": __version__, "proto": __proto__,
                                    "heartbeat_s": HEARTBEAT_S, "timeout_s": TIMEOUT_S}))
            self._dirty.set()
            while True:
                line = await asyncio.wait_for(reader.readline(), TIMEOUT_S)
                if not line or (self.admission and not self.admission.permits(token, pid, ip=peer_ip)):
                    break
                try:
                    msg = json.loads(line)
                    if not isinstance(msg, dict):
                        continue
                except ValueError:
                    continue
                if msg.get("type") == "pong":
                    pending = self.pings.get(pid)
                    if self.writers.get(pid) is writer and pending and msg.get("id") == pending[0]:
                        self.latencies[pid] = (round((time.monotonic() - pending[1]) * 1000), time.monotonic())
                        self.pings.pop(pid, None)
                        self._dirty.set()
                    continue
                if msg.get("type") == "subscribe_players":
                    self.subscribers.add(pid)
                    self._avatars_sent[pid] = {}  # (re)subscribing: this client needs every avatar again
                    self._dirty.set()
                    continue
                if msg.get("type") == "seed_update":
                    # Membership was authenticated during hello and is checked again
                    # above for every message. Admitted room members can seed too.
                    try:
                        port = int(msg.get("port", 0))
                        games = sorted({str(g) for g in msg.get("games", [])
                                        if isinstance(g, str) and g.isascii() and 0 < len(g) <= 64 and
                                        all(c.isalnum() or c in "_-" for c in g)})[:100]
                        if 1 <= port <= 65535 and peer_ip:
                            self.seeders[pid] = {"player_id": pid, "host": peer_ip,
                                                 "port": port, "games": games}
                        else:
                            self.seeders.pop(pid, None)
                    except (TypeError, ValueError):
                        self.seeders.pop(pid, None)
                    self._dirty.set()
                    continue
                if self.state.apply(pid, msg):
                    self._dirty.set()
        except (asyncio.TimeoutError, ConnectionError, ValueError):
            pass
        finally:
            if pid and self.writers.get(pid) is writer:
                del self.writers[pid]
                self._credentials.pop(pid, None)
                self.subscribers.discard(pid)
                self._avatars_sent.pop(pid, None)
                self.seeders.pop(pid, None)
                self.pings.pop(pid, None)
                self.latencies.pop(pid, None)
                if self.state.drop(pid):
                    self._dirty.set()
            writer.close()

    async def _broadcaster(self):
        while True:
            await self._dirty.wait()
            await asyncio.sleep(0.05)  # coalesce bursts
            self._dirty.clear()
            counts = self.state.counts()
            data = self._enc({"type": "counts", **counts})
            players = self.state.roster()
            for player in players:
                peer = self.writers.get(player['player_id'])
                address = peer.get_extra_info('peername') if peer else None
                player['ip'] = str(address[0]) if address else ''
                latency = self.latencies.get(player['player_id'])
                player['ping_ms'] = latency[0] if latency and time.monotonic() - latency[1] < TIMEOUT_S else None
            hashes = {p['player_id']: self._avatar_hash(p['avatar']) for p in players}
            rosters: dict[frozenset, bytes] = {}  # encoded once per distinct set of avatars to include
            seeds = self._enc({"type": "seeds", "peers": list(self.seeders.values())})
            async def send(pid, w):
                if not self._permitted(pid):
                    w.close()
                    return
                try:
                    w.write(data)
                    w.write(seeds)
                    if pid in self.subscribers:
                        w.write(self._roster_for(pid, players, hashes, rosters))
                    await asyncio.wait_for(w.drain(), 2)
                except (OSError, asyncio.TimeoutError, ConnectionError):
                    # A client that cannot consume its room state must not build
                    # an unbounded transport buffer and stall updates to others.
                    w.close()
            await asyncio.gather(*(send(pid, w) for pid, w in list(self.writers.items())),
                                 return_exceptions=True)
            if self.on_change:
                self.on_change(counts, players)

    @staticmethod
    def _avatar_hash(avatar) -> str:
        return hashlib.sha1(avatar.encode()).hexdigest()[:12] if avatar else ""

    def _roster_for(self, pid, players, hashes, rosters) -> bytes:
        """Roster for one subscriber: an avatar is included only when this subscriber hasn't been sent that version."""
        sent = self._avatars_sent.get(pid) or {}
        need = frozenset(p['player_id'] for p in players
                         if p['avatar'] and sent.get(p['player_id']) != hashes[p['player_id']])
        payload = rosters.get(need)
        if payload is None:
            entries = []
            for p in players:
                entry = {k: v for k, v in p.items() if k != 'avatar'}
                entry['avatar_hash'] = hashes[p['player_id']]
                if p['player_id'] in need:
                    entry['avatar'] = p['avatar']
                entries.append(entry)
            payload = rosters[need] = self._enc({"type": "players", "players": entries,
                                                "host_profile": self.host_profile})
        self._avatars_sent[pid] = {p['player_id']: hashes[p['player_id']] for p in players if p['avatar']}
        return payload

    def _ping(self, pid, writer):
        stamp = time.monotonic()
        token = str(time.monotonic_ns())
        self.pings[pid] = (token, stamp)
        writer.write(self._enc({"type": "ping", "id": token}))

    def _permitted(self, pid):
        if self.admission:
            token, ip = self._credentials.get(pid, ("", ""))
            return self.admission.permits(token, pid, ip=ip)
        return True

    async def _reaper(self):
        while True:
            await asyncio.sleep(1)
            for pid, writer in list(self.writers.items()):
                if not self._permitted(pid):
                    writer.close()
                    continue
                pending = self.pings.get(pid)
                last = pending[1] if pending else self.latencies.get(pid, (None, 0))[1]
                if time.monotonic() - last >= HEARTBEAT_S:
                    try:
                        self._ping(pid, writer)
                        self._dirty.set()
                    except OSError:
                        pass
            for pid in self.state.expire():
                w = self.writers.pop(pid, None)
                self._credentials.pop(pid, None)
                if w:
                    w.close()
                self.seeders.pop(pid, None)
                self.pings.pop(pid, None)
                self.latencies.pop(pid, None)
                self.subscribers.discard(pid)
                self._avatars_sent.pop(pid, None)
                self._dirty.set()
