"""Game-level transfer activity shown on the Downloads page: who we send to, and who we receive from."""
from __future__ import annotations

import time

ACTIVE_SECONDS = 10.0   # a transfer counts as "now" if bytes moved this recently
FORGET_SECONDS = 900.0  # entries idle this long are dropped from the list


class ActivityTracker:
    """Bytes moved per (counterpart, game). Counterpart is an IP for uploads and a source key for downloads."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._rows: dict[tuple[str, str], dict] = {}

    def record(self, who, game_id, nbytes):
        who, game_id = str(who or ""), str(game_id or "")
        if not who or not game_id or nbytes <= 0:
            return
        row = self._rows.setdefault((who, game_id), {"bytes": 0, "last": 0.0})
        row["bytes"] += int(nbytes)
        row["last"] = self._clock()

    def snapshot(self, game_id=None):
        """Rows as dicts {who, game, bytes, active}; idle ones past FORGET_SECONDS are pruned first."""
        now = self._clock()
        for key in [k for k, r in self._rows.items() if now - r["last"] > FORGET_SECONDS]:
            del self._rows[key]
        return [{"who": who, "game": game, "bytes": row["bytes"], "active": now - row["last"] <= ACTIVE_SECONDS}
                for (who, game), row in self._rows.items() if game_id is None or game == game_id]

    def clear(self):
        self._rows.clear()

    def clear_game(self, game_id):
        for key in [key for key in self._rows if key[1] == game_id]:
            del self._rows[key]
