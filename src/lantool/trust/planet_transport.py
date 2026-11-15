"""F4 transport: host serves the signed planet file; client fetches and verifies (no deps, asyncio HTTP/1.0)."""
from __future__ import annotations
import asyncio, base64, json
from pathlib import Path
from .signing import sign_planet, verify_planet, sha256_hex

PLANET_PORT = 47802
MAX_PLANET_BYTES = 8 * 1024 * 1024


class PlanetServer:
    """GET /planet -> {"sha256","sig","data"(b64)}. Signature is made once at start from the host private key."""
    def __init__(self, priv_b64: str, planet_path, host="0.0.0.0", port=PLANET_PORT):
        self.priv, self.path, self.host, self.port = priv_b64, Path(planet_path), host, port
        self._server = None
        self.room_payload = None

    def _payload(self) -> bytes:
        data = self.path.read_bytes()
        pkg = sign_planet(self.priv, data)
        return json.dumps({**pkg, "data": base64.b64encode(data).decode()}).encode()

    async def start(self):
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        self.port = self._server.sockets[0].getsockname()[1]  # supports port=0 in tests

    async def stop(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            while (await asyncio.wait_for(reader.readline(), 5)) not in (b"\r\n", b"\n", b""):
                pass
            request = line.split()[:2]
            if request == [b'GET', b'/planet'] or (request == [b'GET', b'/room'] and self.room_payload):
                peer = writer.get_extra_info('peername')
                body = self.room_payload(peer[0]) if request[1] == b'/room' else self._payload()
                head = b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(body)
                writer.write(head + body)
            else:
                writer.write(b"HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
        except (OSError, asyncio.TimeoutError, FileNotFoundError):
            pass
        finally:
            writer.close()


async def fetch_planet(host, port, pub_b64, expected_sha256, dest_path, timeout=10.0) -> bool:
    """Download, verify signature AND that the hash matches the beacon (blocks rollback to an older signed file).
    Writes dest_path atomically only on success. Any failure returns False and leaves the old file alone."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        try:
            async def response():
                writer.write(b"GET /planet HTTP/1.0\r\n\r\n")
                await writer.drain()
                head = await reader.readuntil(b"\r\n\r\n")
                status = head.split(b"\r\n", 1)[0].split()
                if len(status) < 2 or not status[0].startswith(b'HTTP/') or status[1] != b'200':
                    raise ValueError('Moon request failed')
                lengths = [line.split(b':', 1)[1].strip() for line in head.split(b'\r\n')
                           if line.lower().startswith(b'content-length:')]
                if len(lengths) != 1:
                    raise ValueError('Missing or ambiguous moon response size')
                length = int(lengths[0])
                if not 0 < length <= MAX_PLANET_BYTES * 2:
                    raise ValueError('Moon response is too large')
                return await reader.readexactly(length)
            body = await asyncio.wait_for(response(), timeout)
        finally:
            writer.close()
        pkg = json.loads(body)
        data = base64.b64decode(pkg["data"], validate=True)
        if len(data) > MAX_PLANET_BYTES or sha256_hex(data) != expected_sha256:
            return False
        if not verify_planet(pub_b64, data, pkg):
            return False
        dest = Path(dest_path)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except (OSError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError,
            ValueError, KeyError, TypeError):
        return False
