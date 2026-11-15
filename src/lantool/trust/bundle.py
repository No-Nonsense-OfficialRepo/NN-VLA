"""Portable host trust bundle used to configure LAN discovery on a new client."""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

from . import signing as S

_NET_ID = re.compile(r"^[0-9a-f]{16}$")
_MOON_ID = re.compile(r"^[0-9a-f]{10,16}$")


def create_bundle(priv_b64: str, pub_b64: str, net_id: str, moon_id: str,
                  moon_data: bytes, version: str) -> dict:
    """Build the client package. The private key never leaves the host."""
    if not _NET_ID.fullmatch(net_id.lower()):
        raise ValueError("invalid network id")
    if not _MOON_ID.fullmatch(moon_id.lower()):
        raise ValueError("invalid moon id")
    return {
        "format": "lantool-client-bundle-v1",
        "host_pub": pub_b64,
        "net_id": net_id.lower(),
        "moon_id": moon_id.lower(),
        "moon_sha256": S.sha256_hex(moon_data),
        "moon_sig": S.sign_planet(priv_b64, moon_data)["sig"],
        "moon_data": base64.b64encode(moon_data).decode("ascii"),
        "library_version": version,
    }


def load_bundle(path) -> tuple[dict, bytes]:
    """Read and verify a bundle before any network or privileged operation."""
    source = Path(path)
    if source.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("client bundle is too large")
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("format") != "lantool-client-bundle-v1":
        raise ValueError("unsupported client bundle")
    net_id, moon_id = str(raw.get("net_id", "")).lower(), str(raw.get("moon_id", "")).lower()
    if not _NET_ID.fullmatch(net_id) or not _MOON_ID.fullmatch(moon_id):
        raise ValueError("client bundle has invalid room or moon id")
    try:
        data = base64.b64decode(raw["moon_data"], validate=True)
    except (KeyError, ValueError, TypeError):
        raise ValueError("client bundle moon data is invalid") from None
    pkg = {"sha256": raw.get("moon_sha256"), "sig": raw.get("moon_sig")}
    if not S.verify_planet(raw.get("host_pub", ""), data, pkg):
        raise ValueError("client bundle signature or moon hash is invalid")
    return raw, data
