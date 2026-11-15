"""Identity and filesystem contract for the launcher's isolated native backend.

These values describe a native VLA build, not an alias for an installed ZeroTier
product. Keep them in sync with the native build and release package manifest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ID = "lan-launcher-vla"
SERVICE_NAME = "LanLauncherVLA"
SERVICE_DISPLAY_NAME = "VLA - Virtual LAN Adaptor"
SERVICE_EXECUTABLE = "vla-service.exe"
# The signed kernel package is shared and borrowed, never owned by VLA.
# Its original files and identity must remain unchanged for catalog validity.
DRIVER_MODE = "shared-signed"
DRIVER_SERVICE = "zttap300"
DRIVER_COMPONENT = "zttap300"
DRIVER_PROVIDER = "ZeroTier"
DRIVER_INF = "zttap300.inf"
DRIVER_CAT = "zttap300.cat"
DRIVER_SYS = "zttap300.sys"
DRIVER_HARDWARE_IDS = [r"root\zttap300", "zttap300"]
DRIVER_SOURCE_MSI_SHA256 = "42514072b0fe44b8f66e0395bcd23a0b1d1642c28ed00831f1527b2f41b14670"
SHARED_DRIVER_SHA256 = {
    DRIVER_INF: "4afb5e5a2897350926704cdcb427fd69bd2c9f0da271941d1484896b54d294ae",
    DRIVER_CAT: "38c0bbbca2e32509a32150990dbebf98be4ffe2c141a23e878cbeb6c7d483365",
    DRIVER_SYS: "4221486a0d36ef96ab1ed2a537747388655c4c2e470911646f4102d0b88fbdc6",
}
SERVICE_PROVENANCE = "service-provenance.json"
NATIVE_SOURCE_COMMIT = "185a3a2c76e6bf1b1c0415871f43076638eb007c"
DEFAULT_PORT = 47993
PORT_FILE = "zerotier-one.port"  # Upstream protocol filename, in VLA's own home.
MANIFEST_NAME = "manifest.json"


def default_home() -> Path:
    """Private system state, including identity, API token and controller data."""
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "LAN Launcher" / "VLA"
    if sys.platform == "darwin":
        return Path("/Library/Application Support/LAN Launcher/VLA")
    return Path("/var/lib/lan-launcher/vla")


def install_home() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", r"C:\Program Files")) / "LAN Launcher" / "VLA"
    return Path("/opt/lan-launcher/vla")


def service_command() -> list[str]:
    """Fixed command accepted by the VLA native Windows service build."""
    # Native SCM entry uses compiled defaults; foreground CLI overrides do not
    # affect the Windows service's home or primary port.
    return [str(install_home() / SERVICE_EXECUTABLE)]
