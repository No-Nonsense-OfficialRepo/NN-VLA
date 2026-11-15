"""Client for the launcher's isolated VLA service (127.0.0.1:47993).

Using the local HTTP API instead of the zerotier-cli executable means we don't
have to hunt for the CLI on disk, and the same code works on every OS.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from ._http import HttpError, request_json
from .errors import ServiceError, TunnelError
from .backend import DEFAULT_PORT, PORT_FILE, default_home


class LocalClient:
    def __init__(self, home: Optional[Path] = None, token: Optional[str] = None,
                 port: Optional[int] = None, host: str = "127.0.0.1", timeout: float = 5.0):
        self.home = Path(home) if home else default_home()
        self._token = token
        self._port = port
        self.host = host
        self.timeout = timeout

    # -- credentials -------------------------------------------------------
    def _get_token(self) -> str:
        if self._token:
            return self._token
        denied = False
        # Never fall back to the user's ordinary ZeroTier credentials.
        for path in (self.home / "authtoken.secret",):
            try:
                value = path.read_text("utf-8").strip()
            except FileNotFoundError:
                continue
            except PermissionError:
                denied = True
                continue
            except OSError:
                continue
            if value:
                return value
        if denied:
            raise ServiceError(
                "The launcher isn't allowed to talk to VLA. "
                "Use Prepare VLA to check the networking installation."
            )
        raise ServiceError("VLA isn't ready yet (it hasn't finished setting itself up).")

    def _get_port(self) -> int:
        if self._port is not None:
            if not isinstance(self._port, int) or not 1 <= self._port <= 65535:
                raise ServiceError("The VLA API port is invalid.")
            return self._port
        try:
            port = int((self.home / PORT_FILE).read_text("utf-8").strip())
        except FileNotFoundError:
            return DEFAULT_PORT
        except (OSError, ValueError):
            raise ServiceError("VLA's API port file is unreadable. Repair the VLA installation.") from None
        if port != DEFAULT_PORT:
            raise ServiceError("VLA is using an unexpected API port. Repair the VLA installation.")
        return port

    # -- transport ---------------------------------------------------------
    def _call(self, method: str, path: str, body=None, *, timeout=None):
        token = self._get_token()
        url = f"http://{self.host}:{self._get_port()}{path}"
        try:
            return request_json(method, url, {"X-ZT1-Auth": token}, body,
                                self.timeout if timeout is None else timeout)
        except HttpError as e:
            if e.status in (401, 403):
                raise ServiceError(
                    "VLA refused the launcher's access key. "
                    "Restarting the PC (or the VLA service) usually fixes this."
                ) from None
            raise ServiceError(f"VLA returned an unexpected error ({e.status}).") from None
        except OSError as exc:
            reason = getattr(exc, "reason", exc)
            raise ServiceError(f"Cannot reach VLA on {self.host}:{self._get_port()}: {reason}") from None
        except ValueError:
            raise ServiceError("VLA gave an answer the launcher couldn't read.") from None

    # -- API ---------------------------------------------------------------
    def status(self) -> dict:
        return self._call("GET", "/status") or {}

    def networks(self) -> list:
        return self._call("GET", "/network") or []

    def network(self, nwid: str):
        nwid = nwid.lower()
        for net in self.networks():
            if str(net.get("id") or net.get("nwid") or "").lower() == nwid:
                return net
        return None

    def join(self, nwid: str) -> None:
        # First join creates the Windows virtual adapter; measured at >10 s.
        # Keep ordinary status calls fast, but allow driver operations to finish.
        self._call("POST", f"/network/{nwid.lower()}", {}, timeout=60.0)

    def leave(self, nwid: str) -> None:
        self._call("DELETE", f"/network/{nwid.lower()}", timeout=30.0)

    # -- locally hosted network controller --------------------------------
    def create_network(self, node_id: str) -> dict:
        """Create a network controlled by this ZeroTier node."""
        node_id = (node_id or "").lower()
        if len(node_id) != 10 or any(c not in "0123456789abcdef" for c in node_id):
            raise ServiceError("ZeroTier returned an invalid device ID.")
        return self._call("POST", f"/controller/network/{node_id}______", {}) or {}

    def configure_network(self, nwid: str, config: dict) -> dict:
        return self._call("POST", f"/controller/network/{nwid.lower()}", config) or {}

    def controller_members(self, nwid: str) -> list:
        members = self._call("GET", f"/controller/network/{nwid.lower()}/member") or []
        # ZeroTier versions can return either member records or a list of node IDs.
        # Normalize both forms so callers can safely inspect nodeId/config.
        normalized = []
        for member in members:
            if isinstance(member, str):
                try:
                    detail = self.controller_member(nwid, member)
                except TunnelError:
                    detail = {}
                if isinstance(detail, dict):
                    detail.setdefault("nodeId", member)
                    normalized.append(detail)
                else:
                    normalized.append({"nodeId": member, "config": {}})
            elif isinstance(member, dict):
                if not member.get("nodeId") and member.get("id"):
                    member = {**member, "nodeId": member["id"]}
                if "config" not in member:
                    member = {**member, "config": dict(member)}
                normalized.append(member)
        return normalized

    def controller_member(self, nwid: str, node_id: str) -> dict:
        member = self._call("GET", f"/controller/network/{nwid.lower()}/member/{node_id.lower()}") or {}
        # Local controller fields are flat; Central-style responses nest them.
        if isinstance(member, dict) and "config" not in member:
            member = {**member, "config": dict(member)}
        return member

    def authorize_member(self, nwid: str, node_id: str, ip: Optional[str] = None, *, admission_tag=None) -> None:
        config = {"authorized": True}
        if ip:
            config["ipAssignments"] = [ip]
        if admission_tag is not None:
            from .quarantine import ADMISSION_TAG
            config["tags"] = [[ADMISSION_TAG, int(admission_tag)]]
        self._call("POST", f"/controller/network/{nwid.lower()}/member/{node_id.lower()}", config)

    def set_admission_tag(self, nwid: str, node_id: str, allowed: bool) -> None:
        from .quarantine import ADMISSION_TAG
        self._call("POST", f"/controller/network/{nwid.lower()}/member/{node_id.lower()}",
                   {"authorized": allowed, "tags": [[ADMISSION_TAG, int(allowed)]]})

    def delete_network(self, nwid: str) -> None:
        self._call("DELETE", f"/controller/network/{nwid.lower()}")
