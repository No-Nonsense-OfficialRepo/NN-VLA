"""Pure presence state machine (no I/O) so it is easy to test."""
from __future__ import annotations
import time
from dataclasses import dataclass, field

def clean_avatar(value) -> str:
    """Only small inline images are relayed to other clients."""
    text = value if isinstance(value, str) else ""
    return text if (text.startswith("data:image/") and text.isascii() and text.isprintable()
                    and '"' not in text and '\\' not in text and len(text) <= 16000) else ""


TIMEOUT_S = 15.0
HEARTBEAT_S = 5.0
# Bound the full initial roster, including every inline thumbnail (~16 MB).
# Keep the room limit aligned with protected admission's MAX_CLIENTS.
MAX_PLAYERS = 1024
MESSAGE_LIMIT = 32 * 1024 * 1024


@dataclass
class Player:
    player_id: str
    name: str
    state: str = "browsing"  # browsing | hovering | playing
    game: str | None = None
    avatar: str = ""
    last_seen: float = field(default_factory=time.monotonic)


class PresenceState:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.players: dict[str, Player] = {}

    def hello(self, player_id: str, name: str, avatar="") -> Player:
        p = Player(player_id, name, avatar=clean_avatar(avatar), last_seen=self.clock())
        self.players[player_id] = p
        return p

    def apply(self, player_id: str, msg: dict) -> bool:
        """Apply an event. Returns True if visible state changed."""
        if not isinstance(msg, dict):
            return False
        p = self.players.get(player_id)
        if p is None:
            return False
        p.last_seen = self.clock()
        t = msg.get("type")
        if t in ('hover', 'launch'):
            game = msg.get('game')
            if (not isinstance(game, str) or not 0 < len(game) <= 64
                    or not all(c.isascii() and (c.isalnum() or c in '_-') for c in game)):
                return False
        if t == "profile":
            p.name = str(msg.get("name") or p.name)[:32]
            p.avatar = clean_avatar(msg.get("avatar"))
            return True
        before = (p.state, p.game)
        if t == "hover":
            if p.state != "playing":
                p.state, p.game = "hovering", msg.get("game")
        elif t == "hover_end":
            if p.state == "hovering":
                p.state, p.game = "browsing", None
        elif t == "launch":
            p.state, p.game = "playing", msg.get("game")
        elif t == "exit":
            p.state, p.game = "browsing", None
        return (p.state, p.game) != before

    def drop(self, player_id: str) -> bool:
        return self.players.pop(player_id, None) is not None

    def expire(self) -> list[str]:
        now = self.clock()
        dead = [pid for pid, p in self.players.items() if now - p.last_seen > TIMEOUT_S]
        for pid in dead:
            del self.players[pid]
        return dead

    def counts(self) -> dict:
        games: dict[str, dict[str, int]] = {}
        for p in self.players.values():
            if p.game and p.state in ("hovering", "playing"):
                g = games.setdefault(p.game, {"hovering": 0, "playing": 0})
                g["hovering" if p.state == "hovering" else "playing"] += 1
        return {"games": games, "players": len(self.players)}

    def roster(self) -> list[dict]:
        return [{"player_id": p.player_id, "name": p.name, "state": p.state, "game": p.game, "avatar": p.avatar}
                for p in self.players.values()]
