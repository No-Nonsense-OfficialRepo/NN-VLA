"""Typed join failures: every room-join error carries a stage and a stable code.

str(JoinError) is the user-facing message, so existing handlers that show
str(exc) keep working unchanged; new handlers can also read .stage/.code/.hint.
"""
from __future__ import annotations
import time
from enum import Enum


class Stage(str, Enum):
    ADAPTER = "adapter"          # VLA/ZeroTier driver or adapter not usable
    PLANET = "planet"            # moon/planet sync from the Host
    VLA_JOIN = "vla_join"        # network join request to ZeroTier
    ADDRESS = "address"          # room IP never assigned to this PC
    BEACON = "beacon"            # reaching/verifying the Host's signed room info
    ROOM_KEY = "room_key"        # typed key does not match the Host's room
    HOST_SETUP = "host_setup"    # Host-side setup after the network came up
    UNKNOWN = "unknown"


class Code:
    VLA_NOT_CONNECTED = "E_VLA_NOT_CONNECTED"
    PLANET_FETCH = "E_PLANET_FETCH"
    VLA_JOIN = "E_VLA_JOIN"
    NO_ADDRESS = "E_NO_ADDRESS"
    BEACON_UNREACHABLE = "E_BEACON_UNREACHABLE"
    BEACON_INVALID = "E_BEACON_INVALID"
    ROOM_MISMATCH = "E_ROOM_MISMATCH"
    HOST_SETUP = "E_HOST_SETUP"
    TIMEOUT = "E_TIMEOUT"
    UNKNOWN = "E_UNKNOWN"


class JoinError(RuntimeError):
    def __init__(self, stage: Stage, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.stage = Stage(stage)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        return {"stage": self.stage.value, "code": self.code,
                "message": self.message, "hint": self.hint}


class InvalidBeaconError(JoinError, ValueError):
    """Host answered, but its room info was malformed/unsigned/stale.

    Also a ValueError so existing `except ValueError` paths (readiness no-retry,
    LAN discovery fallback) keep treating it as non-retryable metadata failure.
    """


class JoinBudget:
    """One overall deadline shared by every wait in a join.

    Without it the VLA-connect wait and the Host-metadata wait stacked
    (60s + 60s + controller waits). Each phase gets min(its own cap, what is
    left), but never less than `floor`, so a slow first phase cannot starve the
    next one completely. Worst case is total + floor, not the sum of the caps.
    """

    def __init__(self, total: float = 90.0, floor: float = 15.0, clock=time.monotonic):
        self.total = float(total)
        self.floor = float(floor)
        self._clock = clock
        self._deadline = clock() + self.total

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def elapsed(self) -> float:
        return self.total - self.remaining()

    def slice(self, cap: float) -> float:
        """Timeout to hand the next phase."""
        return max(self.floor, min(float(cap), self.remaining()))


def as_join_error(exc: BaseException, stage: Stage = Stage.UNKNOWN,
                  code: str = Code.UNKNOWN) -> JoinError:
    """Return exc unchanged if already typed; otherwise wrap it, keeping the cause."""
    if isinstance(exc, JoinError):
        return exc
    if isinstance(exc, TimeoutError):
        message = str(exc).strip() or "The room connection timed out. Keep the Host room open and retry."
        wrapped = JoinError(stage, Code.TIMEOUT, message)
    else:
        message = str(exc).strip() or f"Room connection failed ({type(exc).__name__})."
        wrapped = JoinError(stage, code, message)
    wrapped.__cause__ = exc
    return wrapped
