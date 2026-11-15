"""Version and protocol compatibility between two LAN Launcher PCs (WP-D).

Rules:
- Different protocol numbers -> 'block': the two builds cannot talk to each other.
- Invalid protocol numbers -> 'block': the Host has not established compatibility.
- Same protocol and exact release version -> 'ok'.
- Different or missing release versions -> 'block'.

A beacon with no 'proto' field is treated as protocol 1 (older builds).
Messages are plain English for the person at the keyboard.
"""
from __future__ import annotations

import re

from .._version import __proto__ as PROTO_VERSION

OK, WARN, BLOCK = "ok", "warn", "block"

_VERSION = re.compile(r"^\s*[Vv]?(\d+)\.(\d+)")


def parse_version(text) -> tuple | None:
    """(major, minor) from strings like '1.6.0', 'V1.6' or '1.6.0-beta'; None if unreadable."""
    if not isinstance(text, str):
        return None
    match = _VERSION.match(text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def proto_of(beacon) -> int | None:
    """Protocol number of a parsed beacon dict. Missing field = 1. None if the value is unusable."""
    if not isinstance(beacon, dict) or "proto" not in beacon:
        return 1
    value = beacon["proto"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def check(host_version, host_proto, my_version, my_proto) -> tuple:
    """Return (ok, level, message, hint). ok is False only when level is 'block'."""
    if host_proto is None or my_proto is None:
        return (False, BLOCK,
                "Could not verify the Host's connection protocol. This room cannot be joined.",
                "Make sure both PCs run the same LAN Launcher release.")

    if host_proto != my_proto:
        if host_proto > my_proto:
            which = "This PC is running an older LAN Launcher than the Host."
            fix = "Update LAN Launcher to join this room. Install the Host's release."
        else:
            which = "The Host is running an older LAN Launcher than this PC."
            fix = "Ask the Host to update LAN Launcher to the release on this PC."
        return (False, BLOCK,
                f"This PC and the Host use different connection protocols (Host {host_proto}, "
                f"this PC {my_proto}), so they cannot join each other. {which}",
                fix)

    def release(value):
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"[Vv]?(\d+\.\d+\.\d+)", value.strip())
        return match.group(1) if match else None
    host_release, local_release = release(host_version), release(my_version)
    if not host_release or not local_release or host_release != local_release:
        return (False, BLOCK,
                f"Launcher version mismatch: Host {host_version}, this PC {my_version}. This room cannot be joined.",
                "Install the same LAN Launcher release on both PCs.")
    return (True, OK, f"Both PCs run the same version ({my_version}).", "")



__all__ = ["check", "parse_version", "proto_of", "PROTO_VERSION", "OK", "WARN", "BLOCK"]
