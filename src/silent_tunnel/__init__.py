"""Silent Tunnel: zero-hassle LAN tunnelling over ZeroTier, for embedding in other Python apps."""
from ._version import __version__
from .errors import InstallError, RoomError, ServiceError, TunnelError
from .room import Room, format_room_code, parse_room_code
from .tunnel import (BAD_ROOM, CONNECTED, CONNECTING, ERROR, NOT_INSTALLED, NOT_JOINED,
                     SERVICE_DOWN, WAITING_APPROVAL, Tunnel, TunnelStatus)

__all__ = [
    "__version__", "Tunnel", "TunnelStatus", "Room", "format_room_code", "parse_room_code",
    "TunnelError", "InstallError", "ServiceError", "RoomError",
    "CONNECTED", "CONNECTING", "WAITING_APPROVAL", "BAD_ROOM", "NOT_JOINED",
    "NOT_INSTALLED", "SERVICE_DOWN", "ERROR",
]
