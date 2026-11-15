"""Bridges RoomController's `zt` interface (async join/leave/wait_ip/alias) to silent_tunnel.Tunnel (blocking).
Blocking calls run in a worker thread. The Windows interface alias is taken from ZeroTier's own network record
(`portDeviceName`); fallback is the usual "ZeroTier One [<nwid>]" and is UNVERIFIED on a real Windows PC."""
from __future__ import annotations
import asyncio, time
from silent_tunnel import Tunnel, CONNECTED, BAD_ROOM, ERROR
from silent_tunnel.errors import ServiceError
from silent_tunnel.tunnel import classify
from .timing_log import mark as timing_mark


class SilentTunnelZT:
    def __init__(self, tunnel: Tunnel, poll_s: float = 0.5):
        self.t, self.poll_s = tunnel, poll_s
        self.last_error = ""
        self.last_failure = ""       # "bad_room" | "error" | "timeout" | "" (set by wait_ip)

    async def _bg(self, fn, *a):
        return await asyncio.get_running_loop().run_in_executor(None, fn, *a)

    async def join(self, net_id: str):
        await self._bg(self.t.begin_join, net_id)

    async def leave(self, net_id: str):
        await self._bg(self.t.leave)

    async def wait_ip(self, net_id: str, timeout: float = 30.0):
        wait_started = time.monotonic()
        end = time.monotonic() + timeout
        self.last_error = ""
        self.last_failure = ""
        while time.monotonic() < end:
            # Waiting for a room address only needs the network record. Tunnel.status()
            # also calls /status first, doubling local API traffic on every poll.
            try:
                network = await self._bg(self.t.local.network, net_id)
            except ServiceError:
                # The service can briefly disappear during startup or a moon reload.
                await asyncio.sleep(self.poll_s)
                continue
            st = classify(network, net_id)
            if st.state == CONNECTED:
                timing_mark("zerotier.ip_poll", wait_started, "assigned")
                return st.ip
            if st.state in (BAD_ROOM, ERROR):
                self.last_error = st.message
                self.last_failure = "bad_room" if st.state == BAD_ROOM else "error"
                timing_mark("zerotier.ip_poll", wait_started, st.state)
                return None
            await asyncio.sleep(self.poll_s)
        self.last_error = st.message if "st" in locals() else "ZeroTier did not respond."
        self.last_failure = "timeout"
        timing_mark("zerotier.ip_poll", wait_started, "timeout")
        return None

    def alias(self, net_id: str) -> str:
        try:
            net = self.t.local.network(net_id) or {}
        except Exception:
            net = {}
        return net.get("portDeviceName") or f"ZeroTier One [{net_id}]"
