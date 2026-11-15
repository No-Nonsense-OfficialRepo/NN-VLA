"""Background approval loop for a room hosted by the local controller."""
from __future__ import annotations

import threading
from typing import Callable, Optional

from .errors import TunnelError


class AutoAuthorizer(threading.Thread):
    """Approve members requesting access to the host's locally controlled room."""

    def __init__(self, client, nwid: str, host_node_id: str, host_ip: str,
                 interval: float = 3.0, on_approved: Optional[Callable[[str], None]] = None,
                 policy: Optional[Callable[[str], bool]] = None):
        super().__init__(daemon=True, name="silent-tunnel-authorizer")
        self.client = client
        self.nwid = nwid
        self.host_node_id = (host_node_id or "").lower()
        self.host_ip = host_ip
        self.interval = interval
        self.on_approved = on_approved
        self.policy = policy              # F13: node_id -> approve? None = auto-approve everyone
        self.denied: set = set()
        self.last_error = ""
        self.approved: set = set()
        self._stop_evt = threading.Event()

    def poll_once(self) -> list:
        newly = []
        try:
            for member in self.client.controller_members(self.nwid):
                node = str(member.get("nodeId") or "").lower()
                if not node or (member.get("config") or {}).get("authorized"):
                    continue
                if node != self.host_node_id and self.policy is not None and not self.policy(node):
                    self.denied.add(node)
                    continue
                self.client.authorize_member(
                    self.nwid, node, self.host_ip if node == self.host_node_id else None
                )
                self.approved.add(node)
                newly.append(node)
                if self.on_approved:
                    self.on_approved(node)
            self.last_error = ""
        except TunnelError as e:
            self.last_error = str(e)
        return newly

    def run(self) -> None:
        while not self._stop_evt.is_set():
            self.poll_once()
            self._stop_evt.wait(self.interval)

    def stop(self) -> None:
        self._stop_evt.set()
