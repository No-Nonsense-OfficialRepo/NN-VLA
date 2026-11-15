"""Client room controller (F3/F4/F14): beacon -> join -> switch IP -> repoint presence; revert on loss.
ZeroTier access is behind the `zt` interface so silent_tunnel can be plugged in at integration."""
from __future__ import annotations
import asyncio, time
import ipaddress
from enum import Enum
from .timing_log import mark as timing_mark, failure as timing_failure
from .errors import Code, JoinError, Stage, as_join_error


class State(Enum):
    ETHERNET = "ethernet"
    SWITCHING = "switching"
    ZEROTIER = "zerotier"


class RoomController:
    """zt must provide: join(net_id), leave(net_id), wait_ip(net_id)->ip|None, alias(net_id)->str.
    fetch_planet(beacon)->bool handles F4 re-serve; local_planet_hash() gives the current hash."""
    # Wait briefly after the adapter route is changed; the assigned ZeroTier IP
    # has already been confirmed before this point.
    SETTLE_S = 1.0
    HOST_LOSS_S = 15.0

    def __init__(self, zt, adapter, presence, local_planet_hash, fetch_planet, host_zt_ip_for,
                 settle_s=None, *, admit_overlay=None):
        self.zt, self.adapter, self.presence = zt, adapter, presence
        self.local_planet_hash, self.fetch_planet = local_planet_hash, fetch_planet
        self.host_zt_ip_for = host_zt_ip_for      # beacon -> host's ZeroTier IP
        self.state = State.ETHERNET
        self.net_id = None
        self.room_ip = None
        self.last_beacon = 0.0
        self.error = None
        self.join_error = None               # typed twin of .error (stage/code/hint) = None
        self.settle_s = self.SETTLE_S if settle_s is None else settle_s
        self.admit_overlay = admit_overlay
        self.launch_blocked = False

    async def on_beacon(self, b: dict):
        self.last_beacon = time.monotonic()
        if self.state != State.ETHERNET:
            return
        self.state = State.SWITCHING
        self.launch_blocked = True           # F3: no game launches mid-switch
        join_started = time.monotonic()
        stage, code = Stage.PLANET, Code.PLANET_FETCH
        try:
            hash_started = time.monotonic()
            local_hash = self.local_planet_hash()
            moon_needed = bool(b.get("moon_id")) and b.get("planet_hash", "") != local_hash
            timing_mark("moon.local_hash_check", hash_started,
                        "update_needed" if moon_needed else "already_current")
            if moon_needed:
                if not await self.fetch_planet(b):          # F4: verify-or-reject inside fetch_planet
                    owner = getattr(self.fetch_planet, "__self__", None)
                    detail = str(getattr(owner, "last_error", "") or "").strip()
                    raise JoinError(Stage.PLANET, Code.PLANET_FETCH,
                                    detail or "couldn't join room",
                                    "Retry; if it keeps failing, restart the Host room.")
            self.net_id = b["net_id"]
            stage, code = Stage.VLA_JOIN, Code.VLA_JOIN
            phase_started = time.monotonic()
            await self.zt.join(self.net_id)
            timing_mark("zerotier.join_request", phase_started, "accepted")
            stage, code = Stage.ADDRESS, Code.NO_ADDRESS
            phase_started = time.monotonic()
            ip = await self.zt.wait_ip(self.net_id)
            timing_mark("zerotier.wait_for_room_ip", phase_started,
                        "assigned" if ip else "timeout_or_error")
            if not ip:
                failure = getattr(self.zt, "last_failure", "")
                message = (getattr(self.zt, "last_error", "") or
                           "ZeroTier joined the room but did not assign this PC an IP address.")
                if failure == "bad_room":
                    raise JoinError(Stage.ROOM_KEY, Code.ROOM_MISMATCH, message,
                                    "Ask the Host to resend the room key, then join again.")
                if failure == "timeout":
                    raise JoinError(Stage.ADDRESS, Code.TIMEOUT, message,
                                    "Check that the Host approved this PC and that UDP is not blocked, then try again.")
                raise JoinError(Stage.ADDRESS, Code.NO_ADDRESS, message,
                                "Check that the Host approved this PC and try again.")
            try:
                self.room_ip = str(ipaddress.IPv4Address(ip))
            except ipaddress.AddressValueError:
                self.room_ip = None  # IPv6 or malformed adapter address cannot bind the IPv4 peer listener.
            if self.admit_overlay:
                await self.admit_overlay()
            alias = self.zt.alias(self.net_id)
            # Joining the ZeroTier network creates a specific route for the room.
            # Changing the adapter metric is an optional preference and can fail
            # on standard Windows accounts; that must not undo a successful join.
            try:
                phase_started = time.monotonic()
                prefer_for_ip = getattr(self.adapter, "prefer_zerotier_for_ip", None)
                await asyncio.get_running_loop().run_in_executor(
                    None,
                    prefer_for_ip or self.adapter.prefer_zerotier,
                    *((alias, ip) if prefer_for_ip else (alias,)))
                timing_mark("adapter.prefer_zerotier", phase_started, "ok")
                self.adapter_warning = None
            except Exception as exc:
                timing_mark("adapter.prefer_zerotier", phase_started, type(exc).__name__)
                self.adapter_warning = str(exc)
            self.presence.repoint(self.host_zt_ip_for(b), b.get("presence_port"))
            phase_started = time.monotonic()
            await asyncio.sleep(self.settle_s)
            timing_mark("room.settle", phase_started, "complete")
            self.state = State.ZEROTIER
            self.error = None
            self.join_error = None
        except Exception as e:
            self.join_error = as_join_error(e, stage, code)
            timing_mark("room_controller.join", join_started, timing_failure(self.join_error))
            self.error = str(self.join_error)
            try:
                await self.leave()
            except Exception as cleanup_error:
                import logging
                logging.getLogger(__name__).exception('Join rollback was incomplete')
                self.error += f' Cleanup also failed: {cleanup_error}'
        else:
            timing_mark("room_controller.join", join_started, "connected")
        finally:
            self.launch_blocked = False

    def observe_beacon(self, b: dict) -> None:
        """Refresh the liveness timer for the room we are currently joining or in."""
        if (self.state in (State.SWITCHING, State.ZEROTIER) and self.net_id and
                str(b.get("net_id", "")).lower() == str(self.net_id).lower()):
            self.last_beacon = time.monotonic()

    def host_lost(self) -> bool:
        if self.state != State.ZEROTIER:
            return False
        # LAN broadcasts can stop reaching a client after its adapter route is
        # switched to ZeroTier. An open Host presence connection is a stronger
        # liveness signal for an already joined room, so don't tear down a
        # healthy session just because Ethernet beacons are no longer visible.
        connected = getattr(self.presence, "connected", None)
        if connected is not None and connected.is_set():
            self.last_beacon = time.monotonic()
        return time.monotonic() - self.last_beacon > self.HOST_LOSS_S

    async def leave(self, eth_host=None):
        """Room closed / host lost / failure: restore adapter, leave network, fall back to Ethernet."""
        errors = []
        try:
            await asyncio.get_running_loop().run_in_executor(None, self.adapter.restore)
        except Exception as exc:
            errors.append(exc)
        left = True
        if self.net_id:
            try:
                await self.zt.leave(self.net_id)
            except Exception as exc:
                errors.append(exc)
                left = False
        if left:
            self.net_id = None
            self.room_ip = None
            self.state = State.ETHERNET
        if errors:
            raise RuntimeError('Room cleanup incomplete: ' + '; '.join(str(e) for e in errors)) from errors[0]
