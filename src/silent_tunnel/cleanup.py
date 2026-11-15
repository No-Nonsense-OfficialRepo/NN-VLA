"""'Clean up this PC': undo what 'Prepare this PC' added, and nothing else.

Removes: this launcher's firewall rules (GamerUtility-<hash>-TCP/UDP, matched by exact pattern),
the VLA service, but only when its registration is ours (installer._service_is_owned), and the
Private-profile change, but only when a record of it was saved (none is written yet; see WP-C.md).

Never removed: the shared ZeroTier network driver (the VLA installer borrows it and keeps it
installed for other apps), an ordinary ZeroTier install, and VLA data folders (WP-A's wipe paths
are listed for the user, not deleted here).

`plan()` is read-only and needs no admin. `run()` asks once through the elevation helper.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Callable, Optional

from . import firewall
from .admin_actions import run_elevated

ZEROTIER_SERVICE = "ZeroTierOneService"
PRIVATE_RECORD_NAME = "prepare-private-record.json"


def _zerotier_service_present() -> bool:
    """Read-only: is an ordinary ZeroTier service registered? False when not Windows."""
    if os.name != "nt":
        return False
    try:
        from .installer import _system_tool
        result = subprocess.run([_system_tool("sc.exe"), "query", ZEROTIER_SERVICE],
                                capture_output=True, text=True, timeout=10,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _vla_registered() -> bool:
    try:
        from . import installer
        return bool(installer._service_exists())
    except Exception:
        return False


def _vla_owned() -> bool:
    try:
        from . import installer
        return bool(installer._service_is_owned())
    except Exception:
        return False


def _private_record() -> Optional[dict]:
    """The Private-profile change we recorded, or None. Never created by a dry run."""
    try:
        from .backend import default_home
        path = Path(default_home()) / PRIVATE_RECORD_NAME
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("changed") and (data.get("indexes") or data.get('profiles')):
            return data
    except (OSError, ValueError):
        return None
    return None


def inventory(rules: Optional[list] = None) -> dict:
    """Read-only summary of what cleanup could touch. Pass `rules` in tests (dicts as list_our_rules)."""
    if rules is None:
        rules = firewall.list_our_rules()
    our_rules = []
    unreadable = rules is None
    for row in rules or []:
        name = row.get("name") if isinstance(row, dict) else None
        if isinstance(name, str) and firewall._OUR_RULE.match(name):
            our_rules.append(name)
    zt = _zerotier_service_present()
    vla_registered = _vla_registered()
    vla_owned = vla_registered and _vla_owned()
    record = _private_record()
    return {
        "rules": sorted(set(our_rules)),
        "rules_unreadable": unreadable,
        "vla_registered": vla_registered,
        "vla_owned": vla_owned,
        "vla_foreign": vla_registered and not vla_owned,
        "zerotier_present": zt,
        "private_record": record,
    }


def plan(rules: Optional[list] = None) -> list[str]:
    """Dry run: lines describing exactly what run() would remove, and what it would leave. No admin."""
    inv = inventory(rules)
    removes, leaves = [], []
    if inv["rules_unreadable"]:
        leaves.append("Firewall rules: could not be read, so none were listed")
    for name in inv["rules"]:
        removes.append(f"Remove firewall rule {name}")
    if not inv["rules"] and not inv["rules_unreadable"]:
        leaves.append("Firewall rules: none of ours found")
    if inv["vla_owned"]:
        removes.append("Stop and remove the VLA service (LanLauncherVLA) installed by this launcher")
    elif inv["vla_foreign"]:
        leaves.append("VLA service: registered but not installed by this launcher; left alone")
    else:
        leaves.append("VLA service: not installed")
    leaves.append("VLA network driver (shared ZeroTier driver): kept, other apps may use it")
    if inv["zerotier_present"]:
        leaves.append("ZeroTier (ordinary install): left alone")
    if inv["private_record"]:
        removes.append("Set the network profile back to Public (recorded change)")
    else:
        leaves.append("Network profile: no recorded change, left as it is")
    leaves.append("VLA data folders and room files: kept (use Wipe VLA to remove them)")
    return ["Would remove:"] + [f"  - {r}" for r in removes] + ["Would leave:"] + [f"  - {l}" for l in leaves]


def run(rules: Optional[list] = None, elevate: Optional[Callable] = None) -> dict:
    """Remove what Prepare this PC added. One elevated call. Returns {'removed', 'left', 'ran'}."""
    inv = inventory(rules)
    elevate = elevate or run_elevated
    lines = plan(rules)
    left = [l.strip()[2:] for l in lines[lines.index("Would leave:") + 1:]]
    removable = bool(inv["rules"]) or inv["vla_owned"] or bool(inv["private_record"])
    if not removable:
        return {"removed": [], "left": left, "ran": False}
    result = elevate("cleanup_pc", remove_service=bool(inv["vla_owned"]),
                     remove_private=bool(inv["private_record"]))
    removed = list((result or {}).get("removed", []))
    return {"removed": removed, "left": left, "ran": True}


def elevated_cleanup(remove_service: bool, remove_private: bool) -> dict:
    """Runs only inside the elevated child (admin_actions `cleanup_pc`). Re-checks everything itself:
    the caller passes only two flags, never a name, path or command."""
    removed = []
    errors = []
    # Firewall: our exact-pattern rules only, re-listed here.
    names = [r.get("name") for r in (firewall.list_our_rules() or []) if isinstance(r, dict)]
    for name in sorted({n for n in names if isinstance(n, str) and firewall._OUR_RULE.match(n)}):
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                                 f"Remove-NetFirewallRule -Name '{name}' -ErrorAction Stop"],
                                capture_output=True, text=True, timeout=30,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:
            removed.append(f"firewall rule {name}")
        else:
            errors.append(f'Windows could not remove firewall rule {name}.')
    if remove_service and _vla_owned():
        from . import installer
        sc = installer._system_tool("sc.exe")
        flags = {"capture_output": True, "timeout": 30,
                 "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        subprocess.run([sc, "stop", installer.SERVICE_NAME], **flags)
        result = subprocess.run([sc, "delete", installer.SERVICE_NAME], **flags)
        if result.returncode in (0, 1060, 1072):
            removed.append("VLA service")
        else:
            errors.append(f'Windows could not remove the VLA service (code {result.returncode}).')
    if remove_private:
        # Recorded change only; the record is written by whoever changes the profile.
        record = _private_record()
        if record:
            from .profile_records import restore
            try:
                restore()
                removed.append("network profile back to Public")
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
    if errors:
        raise RuntimeError('PC cleanup was incomplete: ' + '; '.join(errors))
    return {"removed": removed}
