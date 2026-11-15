"""VLA rules and member operations for a password-protected launcher room."""
from __future__ import annotations

import ipaddress
import threading

from .errors import ServiceError


ADMISSION_PORT = 47807
ROOM_METADATA_PORT = 47802
ADMISSION_TAG = 1


def protected_network_config(host_node):
    authenticated_ip = {"type": "MATCH_CHARACTERISTICS", "mask": "1000000000000000"}
    accept = {"type": "ACTION_ACCEPT"}
    ipv4_tcp = [{"type": "MATCH_ETHERTYPE", "etherType": 0x0800},
                {"type": "MATCH_IP_PROTOCOL", "ipProtocol": 6}, authenticated_ip]
    rules = [{"type": "MATCH_ETHERTYPE", "etherType": 0x0806}, authenticated_ip, accept]
    for port in (ROOM_METADATA_PORT, ADMISSION_PORT):
        for direction, port_direction in (("DEST", "DEST"), ("SOURCE", "SOURCE")):
            rules.extend([*ipv4_tcp,
                          {"type": f"MATCH_{direction}_ZEROTIER_ADDRESS", "zt": host_node},
                          {"type": f"MATCH_IP_{port_direction}_PORT_RANGE", "start": port, "end": port},
                          accept])
    # Tags are signed by the controller. Both endpoints must be admitted.
    rules.extend([{"type": "MATCH_TAG_SENDER", "id": ADMISSION_TAG, "value": 1},
                  {"type": "MATCH_TAG_RECEIVER", "id": ADMISSION_TAG, "value": 1},
                  authenticated_ip, accept, {"type": "ACTION_DROP"}])
    return {"rules": rules, "tags": [{"id": ADMISSION_TAG, "default": 0}]}


class RoomOverlayAdmission:
    def __init__(self, local, network_id, host_ip):
        self.local, self.network_id, self.host_ip = local, network_id, host_ip
        self.host_node = network_id[:10]
        self._blocked = set()
        self._lock = threading.Lock()

    def node_for_ip(self, ip):
        if ip == self.host_ip:
            return self.host_node
        ip = ipaddress.ip_address(ip)
        for member in self.local.controller_members(self.network_id):
            config = member.get("config") or {}
            if not config.get("authorized"):
                continue
            for assigned in config.get("ipAssignments", []):
                if ip == ipaddress.ip_interface(assigned).ip:
                    return str(member.get("nodeId") or "").lower()
        return ""

    def permits_bootstrap(self, node):
        with self._lock:
            return node not in self._blocked

    def admit(self, node):
        if node == self.host_node:
            return
        with self._lock:
            if node in self._blocked:
                raise ServiceError("The client is blocked from this room.")
            self.local.set_admission_tag(self.network_id, node, True)

    def block(self, node):
        if not node or node == self.host_node:
            return
        with self._lock:
            self._blocked.add(node)
            self.local.set_admission_tag(self.network_id, node, False)

    def unblock(self, node):
        with self._lock:
            self._blocked.discard(node)
