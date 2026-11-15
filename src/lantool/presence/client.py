"""Presence client: sends events, receives aggregated counts. Reconnects, and can be re-pointed (F3)."""
from __future__ import annotations
import asyncio, json
from .state import HEARTBEAT_S, MESSAGE_LIMIT
from .._version import __version__, __proto__
from ..netcore import compat

DWELL_S = 1.0


class PresenceClient:
    def __init__(self, player_id, name, host, port=47800, on_counts=None, on_players=None, on_seeds=None, avatar="",
                 *, access=None, on_kicked=None):
        self.player_id, self.name = player_id, name
        self.avatar = avatar
        self.host_profile = {}
        self.access = access
        self.on_kicked = on_kicked
        self._kicked = False
        self.host, self.port = host, port
        self.on_counts, self.on_players, self.on_seeds = on_counts, on_players, on_seeds
        self.counts: dict = {"games": {}, "players": 0}
        self.connected = asyncio.Event()
        self._writer = None
        self._runner = None
        self._hover_task = None
        self._hovering = False
        self._playing_game = None
        self._subscribe = False
        self._seed_games: list[str] = []
        self._seed_port = 0
        # player_id -> (avatar_hash, avatar): the server sends each avatar once, later rosters only carry the hash
        self._avatar_cache: dict[str, tuple] = {}

    def _with_avatars(self, players):
        """Fill in each player's avatar from the cache when the roster only carried its hash."""
        cache, resolved, live = self._avatar_cache, [], set()
        for p in players:
            pid, h = p.get("player_id"), p.get("avatar_hash")
            entry = dict(p)
            if isinstance(p.get("avatar"), str):
                if p["avatar"]:
                    cache[pid] = (h, p["avatar"])
                else:
                    cache.pop(pid, None)
            else:
                hit = cache.get(pid)
                entry["avatar"] = hit[1] if hit and h and hit[0] == h else ""
            entry.pop("avatar_hash", None)
            live.add(pid)
            resolved.append(entry)
        for pid in [k for k in cache if k not in live]:
            del cache[pid]
        return resolved

    def start(self):
        self._kicked = False
        self._runner = asyncio.create_task(self._run())

    async def stop(self):
        if self._runner:
            self._runner.cancel()
        self._close()

    def repoint(self, host, port=None):
        """Move presence to a new host address (Ethernet -> ZeroTier) and reconnect."""
        self.host = host
        if port:
            self.port = port
        self._close()  # _run loop reconnects to the new address

    def _close(self):
        self.connected.clear()
        if self._writer:
            self._writer.close()
            self._writer = None

    async def _run(self):
        while not self._kicked:
            try:
                connect = self.access.connect if self.access else asyncio.open_connection
                reader, writer = await connect(self.host, self.port, limit=MESSAGE_LIMIT)
                self._writer = writer
                hello = {"type": "hello", "player_id": self.player_id,
                         "name": self.name, "avatar": self.avatar, "version": 1,
                         "launcher_version": __version__, "proto": __proto__}
                if self.access:
                    hello["admission_token"] = self.access.token
                await self._send(hello)
                if self._playing_game is not None:
                    await self._send({'type': 'launch', 'game': self._playing_game})
                if self._subscribe:
                    await self._send({"type": "subscribe_players"})
                if self._seed_port:
                    await self._send({"type": "seed_update", "port": self._seed_port,
                                      "games": self._seed_games})
                hb = asyncio.create_task(self._heartbeat())
                try:
                    while True:
                        line = await reader.readline()
                        if not line:
                            break
                        msg = json.loads(line)
                        if not isinstance(msg, dict):
                            continue
                        if msg.get("type") == "welcome":
                            if not compat.check(msg.get('launcher_version'), compat.proto_of(msg), __version__, __proto__)[0]:
                                break
                            self.connected.set()
                        elif msg.get("type") == "error":
                            break
                        elif msg.get("type") == "kicked":
                            self._kicked = True
                            if self.on_kicked:
                                self.on_kicked()
                            break
                        elif msg.get("type") == "ping":
                            await self._send({"type": "pong", "id": msg.get("id")})
                        elif msg.get("type") == "counts":
                            self.counts = {"games": msg["games"], "players": msg["players"]}
                            if self.on_counts:
                                self.on_counts(self.counts)
                        elif msg.get("type") == "players":
                            profile = msg.get("host_profile")
                            self.host_profile = profile if isinstance(profile, dict) else {}
                            players = self._with_avatars(msg["players"])  # always, so the cache stays in step with the server
                            if self.on_players:
                                self.on_players(players)
                        elif msg.get("type") == "seeds" and self.on_seeds:
                            self.on_seeds(msg.get("peers", []))
                finally:
                    hb.cancel()
            except (OSError, ValueError):
                pass
            self._close()
            if not self._kicked:
                await asyncio.sleep(1)

    async def _send(self, obj):
        if self._writer:
            try:
                self._writer.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())
                await self._writer.drain()
            except OSError:
                pass

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await self._send({"type": "heartbeat"})

    async def subscribe_players(self):
        self._subscribe = True
        await self._send({"type": "subscribe_players"})

    async def push_profile(self):
        await self._send({"type": "profile", "name": self.name, "avatar": self.avatar})

    async def publish_seeds(self, games, port):
        self._seed_games = sorted(set(str(g) for g in games))
        self._seed_port = int(port)
        await self._send({"type": "seed_update", "port": self._seed_port,
                          "games": self._seed_games})

    # --- events -------------------------------------------------
    def hover_start(self, game):
        """Call on mouse enter. Counts only after a 1 s dwell."""
        self.hover_end_local()
        self._hover_task = asyncio.create_task(self._dwell(game))

    async def _dwell(self, game):
        await asyncio.sleep(DWELL_S)
        self._hovering = True
        await self._send({"type": "hover", "game": game})

    def hover_end_local(self):
        if self._hover_task:
            self._hover_task.cancel()
            self._hover_task = None
        if self._hovering:
            self._hovering = False
            asyncio.create_task(self._send({"type": "hover_end"}))

    async def launch(self, game):
        self._playing_game = game
        await self._send({"type": "launch", "game": game})

    async def exit(self, game):
        self._playing_game = None
        await self._send({"type": "exit", "game": game})
