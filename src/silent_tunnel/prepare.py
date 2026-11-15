"""'Prepare this PC': one UAC prompt, works offline, then a 'PC ready' indicator.

Unprivileged side only. The elevated work is the allow-listed `prepare_pc` action in
admin_actions (VLA service/driver from the bundled verified package, launcher firewall
rules, optional Public -> Private profile). Nothing here downloads anything.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from . import backend, firewall, installer
from .admin_actions import run_elevated, operation_directory, ElevatedOperationTimeout
from .errors import InstallError

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
UNBLOCK_SKIP = ("Game Directory", "LanTool-data")


def _powershell_json(script: str):
    """Seam for tests. Returns parsed JSON (list or dict) or None."""
    if os.name != "nt":
        return None
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW)
    if result.returncode or not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def public_profiles() -> list:
    """Public network profiles as [{'name', 'index'}]."""
    data = _powershell_json(
        "Get-NetConnectionProfile | Where-Object NetworkCategory -eq 'Public' | "
        "Select-Object @{n='name';e={[string]$_.Name}},@{n='index';e={[int]$_.InterfaceIndex}} | ConvertTo-Json -Compress")
    if data is None:
        return []
    rows = data if isinstance(data, list) else [data]
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("index"), int)]


def firewall_rules_present() -> Optional[bool]:
    """True/False for this launcher's two inbound rules; None when not applicable (source runs)."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return None
    names = ",".join("'" + n + "'" for n in firewall.rule_names())
    data = _powershell_json(
        f"@(Get-NetFirewallRule -Name {names} -ErrorAction SilentlyContinue | "
        "Where-Object {$_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow'}).Count "
        "| ConvertTo-Json -Compress")
    return data == 2


def stale_firewall_rules() -> list:
    """Our rules that point at another path, as path_drift.stale_rules entries. [] when not applicable
    (source runs, not Windows) or unreadable. Read-only, no admin."""
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return []
    try:
        from lantool.diagnostics import path_drift
        return path_drift.stale_rules(firewall.list_our_rules() or [], sys.executable)
    except Exception:
        return []


def _vla_installed() -> bool:
    try:
        return bool(installer.is_installed()) and not installer.installation_pending()
    except Exception:
        return False


def pc_status() -> dict:
    """{'vla','firewall','network': True/False/None(not applicable), 'ready': bool, 'missing': [str]}."""
    vla = _vla_installed()
    fw = firewall_rules_present()
    public = public_profiles()
    stale = stale_firewall_rules()
    network = not public
    missing = []
    if not vla:
        missing.append("VLA service and driver")
    if fw is False:
        missing.append("LAN firewall rules")
    if stale:
        missing.append("Old firewall rules from another folder (optional cleanup)")
    required_ok = vla and fw is not False
    return {"vla": vla, "firewall": fw, "network": network, "public": public,
            "stale_rules": [s.get("name") for s in stale],
            "ready": required_ok, "missing": missing}


def unblock_folder(root, skip=UNBLOCK_SKIP, remover: Optional[Callable[[str], None]] = None) -> int:
    """Remove the Mark-of-the-Web (Zone.Identifier stream) from files under `root`.

    Per-user, no admin. Only the app folder is touched; game and data folders are skipped.
    Returns the number of files that had the flag."""
    if remover is None:
        if os.name != "nt":
            return 0
        remover = os.remove
    root = Path(root)
    if not root.is_dir():
        return 0
    skipped = {s.casefold() for s in skip}
    cleared = 0
    for current, dirs, files in os.walk(root):
        if Path(current) == root:
            dirs[:] = [d for d in dirs if d.casefold() not in skipped]
        for name in files:
            try:
                remover(str(Path(current) / name) + ":Zone.Identifier")
                cleared += 1
            except OSError:
                pass            # no stream on this file (the usual case) or not removable
    return cleared


def app_root() -> Optional[Path]:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else None


def prepare_pc(download_dir, progress=None, *, make_private: bool = False,
               elevate: Optional[Callable] = None, root: Optional[Path] = None) -> dict:
    """Run the whole flow. Returns {'status', 'steps', 'unblocked', 'stale_before', 'stale_after'}.

    Idempotent: the elevated `prepare_pc` action removes our firewall rules that point at another path
    (moved folder, older release, renamed exe) and adds rules for the current exe. The only path it
    ever receives is the validated `caller_program`; nothing path-like is passed from here."""
    def say(message):
        if progress:
            progress(message)

    elevate = elevate or run_elevated
    root = root if root is not None else app_root()

    say("Checking this PC…")
    before = pc_status()
    unblocked = unblock_folder(root) if root else 0

    staging_parent = Path(download_dir)
    arguments = {}
    package = None
    if not before["vla"] or installer.installation_pending():
        package = installer.bundled_package_path()
        if package is None:
            raise InstallError("This launcher does not include the verified VLA runtime, so this PC cannot be "
                               "prepared for hosting or joining rooms.")
        say("Checking the VLA service and driver…")
        manifest = installer.validate_package(package)
        if installer._service_exists() and not installer.installation_pending():
            raise InstallError("A VLA service is already registered but needs repair. Use Wipe VLA, then prepare again.")
        installer._check_shared_driver_compatibility()
        if not installer._service_exists():
            installer._check_port_available()
        arguments["user_sid"] = installer._current_user_sid()
    if make_private and before["public"]:
        arguments["interface_indexes"] = [int(p["index"]) for p in before["public"]][:8]

    def run(staged: Optional[Path]):
        if staged is not None:
            arguments["path"] = str(staged)
        say("Applying changes… click Yes if Windows asks (one prompt).")
        try:
            return elevate("prepare_pc", **arguments)
        except ElevatedOperationTimeout:
            raise
        except (OSError, RuntimeError) as exc:
            raise InstallError(f"Preparing this PC failed: {exc}") from None

    if package is not None:
        installer._no_links(staging_parent)
        staging_parent.mkdir(parents=True, exist_ok=True)
        with operation_directory(prefix="vla-package-", dir=staging_parent) as staging:
            local = Path(staging)
            for name in [backend.MANIFEST_NAME, *manifest["files"]]:
                target = local / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(package / name, target)
            installer.validate_package(local)
            result = run(local)
    else:
        result = run(None)

    # The elevated operation journals each profile before changing it. Recording
    # afterwards here loses recovery information if the caller exits mid-action.

    say("Checking again…")
    after = pc_status()
    return {"status": after, "steps": (result or {}).get("steps", {}), "unblocked": unblocked,
            "stale_before": list(before.get("stale_rules") or []),
            "stale_after": list(after.get("stale_rules") or [])}
