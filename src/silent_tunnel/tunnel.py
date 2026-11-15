"""The Tunnel facade: the one class a host app needs.

    from silent_tunnel import Tunnel
    t = Tunnel()
    room = t.open_room()            # host: creates tonight's room
    status = t.join("8056-c2e2-1c00-0001")   # guest: joins it
    t.cleanup()                     # on exit

All methods block; call them from a worker thread in a GUI app.
"""
from __future__ import annotations

import threading
import time
import random
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Callable, Optional

from . import installer as _default_installer
from .authorizer import AutoAuthorizer
from .errors import RoomError, ServiceError, TunnelError
from .local import LocalClient
from .quarantine import RoomOverlayAdmission, protected_network_config
from .room import Room, parse_room_code
from .store import data_dir, load_settings, load_state, save_obj

# Status states
CONNECTED = "connected"
CONNECTING = "connecting"
WAITING_APPROVAL = "waiting_approval"
BAD_ROOM = "bad_room"
NOT_JOINED = "not_joined"
NOT_INSTALLED = "not_installed"
SERVICE_DOWN = "service_down"
ERROR = "error"


@dataclass(frozen=True)
class TunnelStatus:
    state: str
    message: str
    network_id: str = ""
    ip: str = ""         # this PC's address inside the room
    host_ip: str = ""    # the host's address (always .1): point game clients here

    @property
    def connected(self) -> bool:
        return self.state == CONNECTED


def _host_of(ip: str) -> str:
    parts = ip.split(".")
    return ".".join(parts[:3] + ["1"]) if len(parts) == 4 else ""


def classify(net: Optional[dict], nwid: str) -> TunnelStatus:
    """Turn one VLA network record into a plain-language status."""
    if not net:
        return TunnelStatus(NOT_JOINED, "Not in a room.", nwid)
    st = str(net.get("status", "")).upper()
    ips = [a.split("/")[0] for a in (net.get("assignedAddresses") or []) if "." in a and ":" not in a]
    if st == "OK":
        if ips:
            return TunnelStatus(CONNECTED, f"Connected ({ips[0]})", nwid, ips[0], _host_of(ips[0]))
        return TunnelStatus(CONNECTING, "Connected, waiting for an address…", nwid)
    if st == "ACCESS_DENIED":
        return TunnelStatus(WAITING_APPROVAL, "Waiting for the host to let you in…", nwid)
    if st == "NOT_FOUND":
        return TunnelStatus(BAD_ROOM, "That room doesn't exist (or the host has closed it).", nwid)
    if st == "REQUESTING_CONFIGURATION":
        return TunnelStatus(CONNECTING, "Connecting…", nwid)
    if st == "CLIENT_TOO_OLD":
        return TunnelStatus(ERROR, "VLA is too old for this room. Update it and try again.", nwid)
    return TunnelStatus(ERROR, f"VLA reported a problem ({st or 'unknown'}).", nwid)


class Tunnel:
    def __init__(self, home: Optional[Path] = None, local: Optional[LocalClient] = None,
                 installer=None,
                 progress: Optional[Callable[[str], None]] = None,
                 authorizer_interval: float = 3.0, poll_interval: float = 1.0,
                 approval_policy: Optional[Callable[[str], bool]] = None,
                 ownership_file: Optional[Path] = None,
                 settings_file: Optional[Path] = None):
        self.home = Path(home) if home else data_dir()
        self.local = local or LocalClient()
        self._installer = installer or _default_installer
        self.progress = progress
        self.authorizer_interval = authorizer_interval
        self.poll_interval = poll_interval
        self.approval_policy = approval_policy   # F13 hook; None = auto-approve
        self.settings_file = Path(settings_file) if settings_file else self.home / "settings.json"
        self.settings = load_settings(self.settings_file)
        self.state = load_state(self.home / "state.json")
        self.ownership_file = Path(ownership_file) if ownership_file else None
        self._lock = threading.RLock()
        self._room: Optional[Room] = None
        self.room_overlay: Optional[RoomOverlayAdmission] = None
        self._authorizer: Optional[AutoAuthorizer] = None

    # -- small helpers ---------------------------------------------------------
    @property
    def room(self) -> Optional[Room]:
        return self._room

    @property
    def hosting(self) -> bool:
        return self._room is not None

    def _say(self, msg: str) -> None:
        if self.progress:
            try:
                self.progress(msg)
            except Exception:
                pass

    def save_settings(self) -> None:
        save_obj(self.settings_file, self.settings)

    def _save_state(self) -> None:
        save_obj(self.home / "state.json", self.state)

    def _remember(self, nwid: str) -> None:
        with self._lock:
            if nwid not in self.state.joined:
                self.state.joined.append(nwid)
                self._save_state()

    def _forget(self, nwid: str) -> None:
        with self._lock:
            if nwid in self.state.joined:
                self.state.joined.remove(nwid)
                self._save_state()

    def _current_network(self) -> str:
        if self._room:
            return self._room.network_id
        return self.state.joined[-1] if self.state.joined else ""

    def _leave_quiet(self, nwid: str) -> Optional[str]:
        try:
            self.local.leave(nwid)
        except TunnelError as exc:
            # Keep failed departures on disk so crash recovery can retry.
            return str(exc)
        self._forget(nwid)
        return None

    # -- readiness ---------------------------------------------------------------
    def ensure_ready(self, timeout: float = 90.0) -> None:
        """Install VLA if needed, then wait until its service answers."""
        pending = getattr(self._installer, 'installation_pending', lambda: False)() is True
        if not self._installer.is_installed() or pending:
            self._say("Installing VLA (one-time setup)…")
            self._installer.install(self.home / "downloads", self._say)
            self.state.installed_by_us = True
            self._save_state()
            if self.ownership_file:
                try:
                    self.ownership_file.parent.mkdir(parents=True, exist_ok=True)
                    self.ownership_file.write_text("installed_by_lantool", encoding="utf-8")
                except OSError:
                    pass
        self._say("Starting VLA…")
        start_service = getattr(self._installer, "start_service", None)
        if start_service:
            start_service()
        deadline = time.monotonic() + timeout
        repair_at = time.monotonic() + min(10, timeout / 2)
        repaired = False
        consecutive = 0
        last: Optional[Exception] = None
        while True:
            try:
                if self.local.status().get("address"):
                    consecutive += 1
                    if consecutive >= 2:
                        self._say("VLA is ready. Preparing the room…")
                        return
                else:
                    consecutive = 0
            except ServiceError as e:
                last = e
                consecutive = 0
            if not repaired and consecutive == 0 and time.monotonic() >= repair_at:
                repaired = True
                restart = getattr(self._installer, "restart_service", None)
                if restart:
                    self._say("VLA is not responding. Restarting its service automatically…")
                    restart()
            if time.monotonic() >= deadline:
                raise ServiceError("VLA setup could not reach its local service. "
                                   "Use Open Room to retry. If Windows just installed the driver, a PC restart may be required. "
                                   + (str(last) if last else "No device address was returned."))
            time.sleep(min(self.poll_interval, 0.25))

    # -- status --------------------------------------------------------------------
    def status(self, nwid: Optional[str] = None) -> TunnelStatus:
        nwid = nwid or self._current_network()
        try:
            self.local.status()
            net = self.local.network(nwid) if nwid else None
        except ServiceError as e:
            if not self._installer.is_installed():
                return TunnelStatus(NOT_INSTALLED, "VLA isn't installed yet.")
            return TunnelStatus(SERVICE_DOWN, str(e))
        if not nwid:
            return TunnelStatus(NOT_JOINED, "Not in a room.")
        st = classify(net, nwid)
        if st.state == WAITING_APPROVAL and self._room and self._room.network_id == nwid:
            st = replace(st, message="Setting up your room…")
        return st

    def wait_connected(self, nwid: str, timeout: float = 60.0) -> TunnelStatus:
        deadline = time.monotonic() + timeout
        while True:
            st = self.status(nwid)
            if st.state == CONNECTED:
                return st
            if st.state == BAD_ROOM:
                self._leave_quiet(nwid)
                return st
            if time.monotonic() >= deadline:
                if st.state == WAITING_APPROVAL:
                    if self._room and self._room.network_id == nwid:
                        auth_error = self._authorizer.last_error if self._authorizer else ""
                        detail = "The host PC is still waiting for its own room membership to be approved."
                        if auth_error:
                            detail += f" Controller error: {auth_error}"
                        return replace(st, message=detail)
                    return replace(st, message="The host hasn't let you in yet. "
                                               "Ask them to open the room, then try again.")
                if st.state == CONNECTING:
                    return replace(st, message="Couldn't reach the room. "
                                               "Check your internet connection.")
                return st
            self._say(st.message)
            time.sleep(self.poll_interval)

    # -- guest ---------------------------------------------------------------------
    def join(self, code: str, timeout: float = 60.0) -> TunnelStatus:
        nwid = self.begin_join(code)
        return self.wait_connected(nwid, timeout)

    def begin_join(self, code: str) -> str:
        """Join without waiting for an address; returns the network id. Poll status()/wait_connected()."""
        nwid = parse_room_code(code)
        with self._lock:
            if self._room:
                raise RoomError("You're hosting a room right now. Close it before joining another.")
            for old in list(self.state.joined):
                if old != nwid:
                    self._leave_quiet(old)         # one room at a time
        self.ensure_ready()
        self._say("Joining the room…")
        self.local.join(nwid)
        self._remember(nwid)
        return nwid

    def leave(self) -> None:
        if self._room:
            self.close_room()
            return
        for nwid in list(self.state.joined):
            self.local.leave(nwid)
            self._forget(nwid)

    # -- host ----------------------------------------------------------------------
    def open_room(self, name: Optional[str] = None, *, protected=False) -> Room:
        with self._lock:
            if self._room:
                raise RoomError("You're already hosting a room.")
            for old in list(self.state.joined):
                self._leave_quiet(old)
        self.ensure_ready()
        node_id = str(self.local.status().get("address") or "").lower()
        if not node_id:
            raise ServiceError("VLA didn't return this PC's device ID.")
        octet = random.SystemRandom().randint(10, 250)
        prefix = f"10.147.{octet}"
        room_name = name or f"Silent Tunnel {date.today():%d %b}"
        self._say("Creating a room on this PC…")
        network = self.local.create_network(node_id)
        nwid = str(network.get("id") or "").lower()
        if len(nwid) != 16 or any(c not in "0123456789abcdef" for c in nwid):
            raise ServiceError("The local VLA controller didn't return a valid room code.")
        room = Room(network_id=nwid, name=room_name,
                    host_ip=f"{prefix}.1", subnet=f"{prefix}.0/24")
        overlay = RoomOverlayAdmission(self.local, nwid, room.host_ip) if protected else None
        protected_config = protected_network_config(node_id) if protected else {}
        with self._lock:
            self.state.hosted_network = room.network_id
            self._save_state()
        try:
            configured = self.local.configure_network(nwid, {
                "name": room_name,
                "private": True,
                "enableBroadcast": True,
                "v4AssignMode": "zt",
                "routes": [{"target": room.subnet, "via": None}],
                "ipAssignmentPools": [{"ipRangeStart": f"{prefix}.2",
                                       "ipRangeEnd": f"{prefix}.254"}],
                **protected_config,
            })
            if protected and (len(configured.get("rules", [])) != len(protected_config["rules"])
                              or configured.get("tags") != protected_config["tags"]):
                raise ServiceError("VLA did not install the protected room rules.")
        except Exception:
            try:
                self.local.delete_network(nwid)
                self.state.hosted_network = ""
                self._save_state()
            except TunnelError:
                pass
            raise
        try:
            self._say("Authorizing this PC in its local room…")
            host_tag = {"admission_tag": True} if protected else {}
            self.local.authorize_member(room.network_id, node_id, room.host_ip, **host_tag)
            self._say("Preparing the VLA network adapter… first setup can take up to a minute.")
            self.local.join(room.network_id)
            # The host is also a member of its private network. Approve it
            # explicitly before waiting for its address; the background
            # authorizer is intended for guests and can otherwise race here.
            deadline = time.monotonic() + 20.0
            while True:
                try:
                    members = self.local.controller_members(room.network_id)
                except ServiceError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.25)
                    continue
                host_member = next((m for m in members
                                    if str(m.get("nodeId") or "").lower() == node_id), None)
                if host_member:
                    if not (host_member.get("config") or {}).get("authorized"):
                        self.local.authorize_member(room.network_id, node_id, room.host_ip, **host_tag)
                    break
                if time.monotonic() >= deadline:
                    raise ServiceError("The VLA controller didn't register this PC as a room member.")
                time.sleep(0.5)
        except Exception:
            try:
                self.local.leave(room.network_id)
            except TunnelError:
                pass
            try:
                self.local.delete_network(room.network_id)
            except TunnelError:
                pass
            self.state.hosted_network = ""
            self._save_state()
            raise
        self._remember(room.network_id)
        def permits_bootstrap(node):
            return overlay.permits_bootstrap(node) and (self.approval_policy is None or self.approval_policy(node))

        authorizer = AutoAuthorizer(self.local, room.network_id, node_id, room.host_ip,
                                    interval=self.authorizer_interval,
                                    policy=permits_bootstrap if overlay else self.approval_policy)
        authorizer.start()
        with self._lock:
            self._room, self._authorizer = room, authorizer
            self.room_overlay = overlay
        return room

    def _stop_authorizer(self) -> None:
        auth, self._authorizer = self._authorizer, None
        if auth:
            auth.stop()
            if auth.is_alive() and auth is not threading.current_thread():
                auth.join(timeout=5)

    def close_room(self) -> list:
        """Stop hosting: leave, delete the room from the local controller."""
        notes: list = []
        with self._lock:
            self._stop_authorizer()
            nwid = self._room.network_id if self._room else self.state.hosted_network
            if not nwid:
                return notes
            try:
                self.local.leave(nwid)
            except TunnelError as e:
                notes.append(str(e))
            else:
                self._forget(nwid)
            deleted = False
            try:
                self.local.delete_network(nwid)
                deleted = True
            except TunnelError as e:
                notes.append(str(e))
            if deleted:
                self.state.hosted_network = ""
                self._save_state()
            self._room = None
            self.room_overlay = None
        return notes

    # -- cleanup ---------------------------------------------------------------------
    def uninstall_zerotier(self) -> bool:
        """Remove VLA from this PC now, whoever installed it."""
        for nwid in list(self.state.joined):
            self._leave_quiet(nwid)
        removed = self._installer.uninstall(self._say)
        if removed:
            self.state.installed_by_us = False
            self._save_state()
            if self.ownership_file:
                try:
                    self.ownership_file.unlink()
                except FileNotFoundError:
                    pass
        return bool(removed)

    def cleanup(self, uninstall: Optional[bool] = None) -> list:
        """Call when the launcher closes. Leaves rooms; optionally removes VLA.

        By default VLA is removed only if the setting is on AND Silent Tunnel installed it
        (state.installed_by_us), so a user's own VLA is never uninstalled. Passing
        uninstall=True is an explicit override.
        """
        notes = self.close_room()
        for nwid in list(self.state.joined):
            error = self._leave_quiet(nwid)
            if error:
                notes.append(error)
        if uninstall is None:
            owned = self.state.installed_by_us or bool(
                self.ownership_file and self.ownership_file.is_file())
            want = self.settings.uninstall_on_exit and owned
            if self.settings.uninstall_on_exit and not want:
                notes.append("VLA was already on this PC, so it was left installed.")
        else:
            want = uninstall
        if want:
            try:
                removed = self.uninstall_zerotier()
                notes.append("VLA was removed." if removed else "VLA was not installed.")
            except TunnelError as e:
                notes.append(str(e))
        return notes

    def recover(self) -> list:
        """Call at startup: tidy up after a crash (leftover rooms from last time)."""
        notes: list = []
        try:
            self.local.status()
        except ServiceError:
            return notes
        for nwid in list(self.state.joined):
            error = self._leave_quiet(nwid)
            if error:
                notes.append(error)
        if self.state.hosted_network and not self._room:
            notes += self.close_room()
        return notes
