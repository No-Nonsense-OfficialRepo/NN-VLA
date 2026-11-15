"""Active LAN discovery with directed broadcasts and signed unicast replies."""
from __future__ import annotations
import asyncio
import ipaddress
import json
import os
import socket
import subprocess
import time

from .errors import Code, InvalidBeaconError, JoinError, Stage

QUERY_PORT = 47806
QUERY = b'GAMER-UTILITY-DISCOVER/1'


def broadcast_targets():
    targets = {'255.255.255.255', '127.0.0.1'}
    if os.name == 'nt':
        try:
            result = subprocess.run(['powershell.exe', '-NoProfile', '-Command',
                'Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike "127.*" '
                '-and $_.IPAddress -notlike "169.254.*" } | Select-Object IPAddress,PrefixLength | ConvertTo-Json -Compress'],
                capture_output=True, text=True, timeout=5,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            rows = json.loads(result.stdout or '[]')
            for row in ([rows] if isinstance(rows, dict) else rows):
                net = ipaddress.ip_interface(f"{row['IPAddress']}/{row['PrefixLength']}").network
                if net.prefixlen < 31:
                    targets.add(str(net.broadcast_address))
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            pass
    return sorted(targets)


def source_ip_for(peer):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect((peer, 9))
        return sock.getsockname()[0]


class DiscoveryResponder(asyncio.DatagramProtocol):
    def __init__(self, payload_for):
        self.payload_for = payload_for
        self.transport = None
        self.last_reply = {}

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data != QUERY:
            return
        now = time.monotonic()
        if now-self.last_reply.get(addr[0], -10) < 1:
            return
        self.last_reply = {ip: stamp for ip, stamp in self.last_reply.items() if now-stamp < 5}
        if len(self.last_reply) >= 128:
            return
        self.last_reply[addr[0]] = now
        try:
            self.transport.sendto(self.payload_for(source_ip_for(addr[0])), addr)
        except (OSError, ValueError):
            pass

    @classmethod
    async def start(cls, payload_for, host='0.0.0.0', port=QUERY_PORT):
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: cls(payload_for), local_addr=(host, port))
        return transport


async def fetch_room(address, port=47802, timeout=5):
    """Explicit-IP fallback uses the same signature verification as UDP."""
    from .beacon import parse_beacon
    ipaddress.IPv4Address(address)
    reader, writer = await asyncio.wait_for(asyncio.open_connection(address, port), timeout)
    try:
        writer.write(b'GET /room HTTP/1.0\r\n\r\n')
        await writer.drain()
        # A single read can return only part of a TCP response.
        raw = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout)
        length = next((int(line.split(b':', 1)[1]) for line in raw.split(b'\r\n')
                       if line.lower().startswith(b'content-length:')), 0)
        if not 0 < length <= 65536:
            raise InvalidBeaconError(
                Stage.BEACON, Code.BEACON_INVALID,
                'The host returned an invalid room response size.',
                'Make sure the Host is running the same LAN Launcher version.')
        raw += await asyncio.wait_for(reader.readexactly(length), timeout)
        head, _, body = raw.partition(b'\r\n\r\n')
        beacon = parse_beacon(body) if head.startswith(b'HTTP/1.0 200 ') else None
        if not beacon:
            raise InvalidBeaconError(
                Stage.BEACON, Code.BEACON_INVALID,
                'The host did not return a valid signed room. Check its IP and PC clock.',
                'Check the Host IP and that both PCs have the correct date and time.')
        return beacon
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except (OSError, asyncio.TimeoutError):
            pass


async def fetch_room_when_ready(address, port=47802, timeout=60, retry_interval=1):
    """Keep the joined overlay intact while its signed Host endpoint warms up.

    Retry transport failures only. Invalid signatures, stale announcements and
    malformed room responses remain fatal; they must not become trusted by retrying.
    Cancellation propagates so shutdown can stop an in-flight readiness check.
    """
    from .timing_log import mark as timing_mark
    if timeout <= 0 or retry_interval < 0:
        raise ValueError('Host readiness timeout must be positive and retry interval nonnegative.')
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_error = None
    while loop.time() < deadline:
        attempt_started = time.monotonic()
        attempt_timeout = min(5, deadline - loop.time())
        if attempt_timeout <= 0:
            break
        try:
            beacon = await asyncio.wait_for(
                fetch_room(address, port=port, timeout=attempt_timeout), attempt_timeout)
        except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            last_error = exc
            timing_mark('room_key.metadata_attempt', attempt_started,
                        f'{address}:{port} {type(exc).__name__}')
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(retry_interval, remaining))
        else:
            timing_mark('room_key.metadata_attempt', attempt_started, 'verified')
            return beacon
    reason = str(last_error).strip() or type(last_error).__name__
    raise JoinError(
        Stage.BEACON, Code.BEACON_UNREACHABLE,
        f'VLA connected, but room details could not be received from the Host at '
        f'{address}:{port} within {timeout:g} seconds. Keep the Host room open and '
        f'check LAN access on the Host PC. Last connection error: {reason}.',
        'Keep the Host room open. On the Host PC, allow Host.exe inbound TCP 47802 '
        'through Windows Firewall and any third-party firewall, including on Public networks. '
        'Then run Check this Host in Diagnostics.') from last_error
