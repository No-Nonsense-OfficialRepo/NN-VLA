"""Exceptions raised by Silent Tunnel.

Every message is written for a non-technical user, so a host app can show
``str(error)`` directly in a dialog.
"""
from __future__ import annotations


class TunnelError(Exception):
    """Base class for everything Silent Tunnel raises on purpose."""


class InstallError(TunnelError):
    """ZeroTier could not be downloaded, verified, installed or removed."""


class ServiceError(TunnelError):
    """The local ZeroTier service is missing, stopped, or refused us."""


class RoomError(TunnelError):
    """A room code is malformed, or the room can't be joined/hosted."""


