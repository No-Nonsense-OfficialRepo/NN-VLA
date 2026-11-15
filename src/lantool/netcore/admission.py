"""Room-scoped password admission over a TLS endpoint pinned by the signed beacon."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
from pathlib import Path
import secrets
import ssl
import tempfile
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from ..trust import signing
from silent_tunnel.errors import TunnelError
from silent_tunnel.quarantine import ADMISSION_PORT


PENDING_SECONDS = 120
MAX_PASSWORD_BYTES = 256
MAX_CLIENTS = 1024
MAX_MESSAGE = 8192


def _server_certificate():
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LAN Launcher room")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=7))
            .sign(key, hashes.SHA256()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    with tempfile.TemporaryDirectory(prefix="lan-room-") as folder:
        cert_file = Path(folder) / "room.pem"
        key_file = Path(folder) / "room.key"
        cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
        context.load_cert_chain(cert_file, key_file)
    return context, cert.fingerprint(hashes.SHA256()).hex()


async def _read_message(reader, timeout=5):
    raw = await asyncio.wait_for(reader.readline(), timeout)
    if not raw or len(raw) > MAX_MESSAGE:
        raise ValueError("Invalid admission message")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("Invalid admission message")
    return obj


async def _send_message(writer, obj, timeout=5):
    writer.write(json.dumps(obj, separators=(",", ":")).encode() + b"\n")
    await asyncio.wait_for(writer.drain(), timeout)


@dataclass
class _Request:
    request_id: str
    player_id: str
    identity: str
    name: str
    ip: str
    expires_at: float
    status: str = "pending"

    def summary(self):
        return {"id": self.request_id, "name": self.name, "ip": self.ip}


@dataclass
class _Grant:
    player_id: str
    identity: str
    ip: str
    overlay_ip: str = ""
    node: str = ""
    revoked: bool = False


class RoomAdmission:
    """Single-event-loop state for a protected room. Destroy it when the room closes."""

    def __init__(self, password, on_change=None, *, port=ADMISSION_PORT, overlay=None):
        if not isinstance(password, str) or not 0 < len(password.encode()) <= MAX_PASSWORD_BYTES:
            raise ValueError("Enter a room password of 1–256 UTF-8 bytes.")
        self._secret = hashlib.sha256(password.encode()).digest()
        self._on_change = on_change
        self._overlay = overlay
        self._overlay_lock = asyncio.Lock()
        self._revocations = set()
        self._error = ""
        self.port = port
        self.ssl_context = None
        self._fingerprint = None
        self._server = None
        self._cleanup = None
        self._requests = {}
        self._blacklist = {}
        self._failures = {}
        self._last_guess = {}
        self._grants = {}
        self._writers = set()

    @property
    def metadata(self):
        if not self._fingerprint:
            raise RuntimeError("Room admission has not started.")
        return {"port": self.port, "fingerprint": self._fingerprint, "required": True}

    def listener_options(self):
        if not self.ssl_context:
            raise RuntimeError("Room admission has not started.")
        return {"ssl": self.ssl_context, "ssl_handshake_timeout": 5}

    async def start(self):
        self.ssl_context, self._fingerprint = await asyncio.to_thread(_server_certificate)
        self._server = await asyncio.start_server(self._handle, "0.0.0.0", self.port,
                                                  ssl=self.ssl_context, limit=MAX_MESSAGE,
                                                  ssl_handshake_timeout=5)
        self.port = self._server.sockets[0].getsockname()[1]
        self._cleanup = asyncio.create_task(self._expire())

    async def stop(self):
        self._secret = b""
        if self._cleanup:
            self._cleanup.cancel()
        if self._server:
            self._server.close()
            for writer in tuple(self._writers):
                writer.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), 5)
            except asyncio.TimeoutError:
                pass
        if self._revocations:
            await asyncio.gather(*tuple(self._revocations), return_exceptions=True)
        self._requests.clear()
        self._blacklist.clear()
        self._failures.clear()
        self._last_guess.clear()
        self._grants.clear()
        self._changed()

    def _changed(self):
        if self._on_change:
            self._on_change(self.snapshot())

    def snapshot(self):
        return {
            "pending": [item.summary() for item in self._requests.values()
                        if item.status == "pending"],
            "blacklist": [{**item.summary(), "blocked_at": blocked_at}
                          for item, blocked_at in self._blacklist.values()],
            **({"error": self._error} if self._error else {}),
        }

    def decide(self, request_id, allow):
        item = self._requests.get(request_id)
        if not item or item.status != "pending" or item.expires_at <= time.monotonic():
            return False
        if allow:
            item.status = "allowed"
        else:
            self._blacklist[request_id] = item, int(time.time())
            for key, request in tuple(self._requests.items()):
                if request.identity == item.identity or request.ip == item.ip:
                    del self._requests[key]
            nodes = set()
            for grant in self._grants.values():
                if grant.identity == item.identity or item.ip in (grant.ip, grant.overlay_ip):
                    grant.revoked = True
                    if grant.node:
                        nodes.add(grant.node)
            if self._overlay and nodes:
                task = asyncio.create_task(self._revoke(nodes))
                self._revocations.add(task)
                task.add_done_callback(self._revocations.discard)
        self._changed()
        return True

    def remove(self, entry_id):
        entry = self._blacklist.pop(entry_id, None)
        if not entry:
            return False
        item, _blocked_at = entry
        if self._overlay:
            nodes = {grant.node for grant in self._grants.values()
                     if grant.node and (grant.identity == item.identity
                                        or item.ip in (grant.ip, grant.overlay_ip))}
            if nodes:
                task = asyncio.create_task(self._restore_bootstrap(nodes))
                self._revocations.add(task)
                task.add_done_callback(self._revocations.discard)
        self._changed()
        return True

    def block_active(self, player_id, name, ip):
        """Block a connected member when the Host chooses Kick and blacklist."""
        grant = next((g for g in self._grants.values() if g.player_id == player_id), None)
        if not grant:
            return False
        request_id = secrets.token_urlsafe(24)
        item = _Request(request_id, player_id, grant.identity, name, ip,
                        time.monotonic() + PENDING_SECONDS, "blocked")
        self._blacklist[request_id] = item, int(time.time())
        nodes = set()
        for active in self._grants.values():
            if active.identity == grant.identity or ip in (active.ip, active.overlay_ip):
                active.revoked = True
                if active.node:
                    nodes.add(active.node)
        if self._overlay and nodes:
            task = asyncio.create_task(self._revoke(nodes))
            self._revocations.add(task)
            task.add_done_callback(self._revocations.discard)
        self._changed()
        return True

    def _blocked(self, identity, ip):
        return any(item.identity == identity or item.ip == ip
                   for item, _blocked_at in self._blacklist.values())

    def _grant_blocked(self, grant):
        return self._blocked(grant.identity, grant.ip) or self._blocked(grant.identity, grant.overlay_ip)

    def permits(self, token, player_id=None, *, ip=None):
        if not isinstance(token, str):
            return False
        grant = self._grants.get(token)
        return bool(self._secret and grant and not grant.revoked
                    and (player_id is None or grant.player_id == player_id)
                    and (ip is None or ip in (grant.ip, grant.overlay_ip))
                    and not self._grant_blocked(grant))

    def _allow(self, player_id, identity, ip):
        for token, grant in tuple(self._grants.items()):
            if grant.player_id == player_id and grant.identity != identity:
                return {"status": "invalid"}
            if grant.identity == identity:
                if grant.player_id != player_id or self._grant_blocked(grant):
                    return {"status": "denied"}
                if grant.revoked:
                    del self._grants[token]
                    break
                self._clear_failures(identity, ip)
                # _allow is reached only after a correct password or explicit
                # Host approval. Rebind authenticated returns after DHCP or
                # interface changes, invalidating the old physical address.
                grant.ip = ip
                return {"status": "allowed", "token": token}
        if len(self._grants) >= MAX_CLIENTS:
            return {"status": "busy"}
        token = secrets.token_urlsafe(32)
        self._grants[token] = _Grant(player_id, identity, ip)
        self._clear_failures(identity, ip)
        return {"status": "allowed", "token": token}

    def _clear_failures(self, identity, ip):
        self._failures.pop(identity, None)
        self._failures.pop(ip, None)

    async def _revoke(self, nodes):
        async with self._overlay_lock:
            for node in nodes:
                try:
                    await asyncio.to_thread(self._overlay.block, node)
                except TunnelError:
                    logging.getLogger(__name__).exception("VLA could not revoke a blocked room member")
                    self._error = "VLA could not revoke a blocked member. Close this protected room."
                    self._changed()

    async def _restore_bootstrap(self, nodes):
        async with self._overlay_lock:
            for node in nodes:
                if not any(grant.node == node and self._grant_blocked(grant)
                           for grant in self._grants.values()):
                    await asyncio.to_thread(self._overlay.unblock, node)

    async def _bind(self, data, source, destination):
        from .._version import __version__, __proto__
        from . import compat
        if not compat.check(__version__, __proto__, data.get('launcher_version'), compat.proto_of(data))[0]:
            return {'status': 'version_mismatch', 'version': __version__}
        if not self._overlay or destination != self._overlay.host_ip:
            return {"status": "invalid"}
        token = data.get("token")
        grant = self._grants.get(token) if isinstance(token, str) else None
        if (not grant or grant.identity != data["identity"] or grant.player_id != data.get("player_id")
                or not self.permits(token) or self._blocked(grant.identity, source)):
            return {"status": "denied"}
        async with self._overlay_lock:
            try:
                node = await asyncio.to_thread(self._overlay.node_for_ip, source)
                if not node:
                    return {"status": "busy"}
                if (not self.permits(token) or self._blocked(grant.identity, source)
                        or (grant.node and grant.node != node)):
                    return {"status": "denied"}
                grant.node, grant.overlay_ip = node, source
                await asyncio.to_thread(self._overlay.admit, node)
                return {"status": "allowed"} if self.permits(token) else {"status": "denied"}
            except TunnelError:
                return {"status": "busy"}

    def _process(self, data, ip):
        from .._version import __version__, __proto__
        from . import compat
        if not compat.check(__version__, __proto__, data.get('launcher_version'), compat.proto_of(data))[0]:
            return {'status': 'version_mismatch', 'version': __version__}
        if not self._secret:
            return {"status": "expired"}
        identity = data["identity"]
        if self._blocked(identity, ip):
            return {"status": "denied"}
        player_id = data.get("player_id")
        name = data.get("name")
        if not isinstance(player_id, str) or not 0 < len(player_id) <= 64 or not isinstance(name, str):
            return {"status": "invalid"}
        name = "".join(char for char in name[:32] if char.isprintable()).strip() or "Player"
        action = data.get("action")
        now = time.monotonic()
        if action in ("poll", "cancel"):
            request_id = data.get("request_id")
            item = self._requests.get(request_id) if isinstance(request_id, str) else None
            if not item or item.identity != identity or item.player_id != player_id or item.expires_at <= now:
                return {"status": "expired"}
            if action == "cancel":
                del self._requests[item.request_id]
                self._changed()
                return {"status": "cancelled"}
            if item.status == "allowed":
                del self._requests[item.request_id]
                return self._allow(player_id, identity, item.ip)
            return {"status": item.status, "request_id": item.request_id}
        if action != "join":
            return {"status": "invalid"}
        for item in self._requests.values():
            if item.identity == identity and item.expires_at > now:
                return {"status": "pending", "request_id": item.request_id}
        password = data.get("password")
        if password is None:
            return {"status": "password_required"}
        if not isinstance(password, str) or len(password.encode()) > MAX_PASSWORD_BYTES:
            return {"status": "invalid"}
        if ip not in self._last_guess and len(self._last_guess) >= MAX_CLIENTS:
            return {"status": "busy"}
        if now - self._last_guess.get(ip, -1) < 0.5:
            return {"status": "rate_limited"}
        self._last_guess[ip] = now
        attempts = max(self._failures.get(ip, 0), self._failures.get(identity, 0))
        if not hmac.compare_digest(hashlib.sha256(password.encode()).digest(), self._secret):
            if identity not in self._failures and len(self._failures) >= 2 * MAX_CLIENTS:
                return {"status": "busy"}
            self._failures[identity] = self._failures[ip] = attempts + 1
            return {"status": "wrong_password"}
        if attempts < 3:
            return self._allow(player_id, identity, ip)
        if len(self._requests) >= MAX_CLIENTS:
            return {"status": "busy"}
        request_id = secrets.token_urlsafe(24)
        self._requests[request_id] = _Request(request_id, player_id, identity, name, ip,
                                               now + PENDING_SECONDS)
        self._changed()
        return {"status": "pending", "request_id": request_id}

    async def _expire(self):
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            expired = [key for key, item in self._requests.items() if item.expires_at <= now]
            for key in expired:
                del self._requests[key]
            if expired:
                self._changed()

    async def _handle(self, reader, writer):
        if len(self._writers) >= 64:
            writer.close()
            return
        self._writers.add(writer)
        try:
            ip = writer.get_extra_info("peername")[0]
            nonce = secrets.token_urlsafe(32)
            await _send_message(writer, {"nonce": nonce})
            data = await _read_message(reader)
            identity = data.get("identity")
            if (not isinstance(identity, str) or len(identity) != 44 or data.get("nonce") != nonce
                    or not signing.verify_obj(identity, data)):
                return
            if data.get("action") == "bind":
                reply = await self._bind(data, ip, writer.get_extra_info("sockname")[0])
            else:
                reply = self._process(data, ip)
            await _send_message(writer, reply)
        except (OSError, ValueError, asyncio.TimeoutError):
            pass
        finally:
            self._writers.discard(writer)
            writer.close()


@dataclass(frozen=True)
class AdmissionAccess:
    fingerprint: str
    token: str = field(repr=False, default="")

    @classmethod
    def from_result(cls, beacon, result):
        return cls(beacon["admission"]["fingerprint"], result["token"])

    @property
    def authorization_header(self):
        return f"Authorization: Bearer {self.token}\r\n".encode("ascii")

    async def connect(self, host, port, timeout=10, *, limit=65536):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=context, limit=limit), timeout)
        cert = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        if not cert or not hmac.compare_digest(hashlib.sha256(cert).hexdigest(), self.fingerprint):
            writer.close()
            raise OSError("Room security certificate did not match the signed announcement.")
        return reader, writer


async def request_admission(beacon, player_id, name, *, identity, password=None, request_id=None,
                            action="join", token=None):
    """Send one signed client action over the verified room TLS connection."""
    meta = beacon["admission"]
    access = AdmissionAccess(meta["fingerprint"])
    reader, writer = await access.connect(beacon["host"], meta["port"], limit=MAX_MESSAGE)
    try:
        hello = await _read_message(reader)
        private, public = identity
        from .._version import __version__, __proto__
        body = {"nonce": hello["nonce"], "identity": public, "player_id": player_id, "name": name,
                "launcher_version": __version__, "proto": __proto__,
                "action": action, "password": password, "request_id": request_id, "token": token}
        await _send_message(writer, signing.sign_obj(private, body))
        return await _read_message(reader)
    finally:
        writer.close()
