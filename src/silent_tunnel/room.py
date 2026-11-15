"""Room codes. A room is a ZeroTier network; its code is the 16-hex network ID."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .errors import RoomError

_SEPARATORS = re.compile(r"[\s\-_:.]")
_NWID = re.compile(r"^[0-9a-f]{16}$")


def parse_room_code(text: str) -> str:
    """Return the bare 16-hex network ID from whatever a person typed or pasted."""
    code = _SEPARATORS.sub("", text or "").lower()
    if not _NWID.match(code):
        raise RoomError(
            "That room code doesn't look right. It should be 16 letters and "
            "numbers, like 8056-c2e2-1c00-0001."
        )
    return code


def format_room_code(network_id: str) -> str:
    """8056c2e21c000001 -> 8056-c2e2-1c00-0001 (easier to read out or retype)."""
    code = parse_room_code(network_id)
    return "-".join(code[i:i + 4] for i in range(0, 16, 4))


@dataclass(frozen=True)
class Room:
    network_id: str
    name: str
    host_ip: str   # the host always gets .1 in the room's subnet
    subnet: str

    @property
    def code(self) -> str:
        return format_room_code(self.network_id)
