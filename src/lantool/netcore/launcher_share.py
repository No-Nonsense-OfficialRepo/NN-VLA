"""Room-independent, TLS-pinned launcher handoff. Never grants room access."""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import json
import re
from pathlib import Path
import secrets
import stat
import time
import zipfile

from .admission import _server_certificate, _read_message as _base_read_message, _send_message as _base_send_message, AdmissionAccess
from .._version import __version__
from ..filesync.index import finish_disk_work

PORT = 47808
TTL = 600
CONTROL_TIMEOUT = 30


async def _read_message(reader, timeout=CONTROL_TIMEOUT):
    return await _base_read_message(reader, timeout)


async def _send_message(writer, message, timeout=CONTROL_TIMEOUT):
    return await _base_send_message(writer, message, timeout)
MAX_PACKAGE = 1024 ** 3


def _linked_path(path):
    """Reject symlinks and Windows junctions on supported Python versions."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, 'is_junction', None)
    if is_junction is not None:
        return is_junction()
    try:
        return bool(path.stat(follow_symlinks=False).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return False


def build_package(root, destination):
    """Export only the installed release's manifest-listed binaries/assets."""
    root, destination = Path(root).resolve(), Path(destination)
    release = root / 'Launcher Bin/Release'
    metadata = json.loads((release / 'version.json').read_text('utf-8-sig'))
    if metadata.get('version') != __version__:
        raise ValueError('The installed release metadata does not match this Host.')
    files = []
    for line in (release / 'SHA256SUMS.txt').read_text('utf-8').splitlines():
        expected, relative = line.split(' *', 1)
        relative = relative.replace('\\', '/')
        if '..' in relative.split('/') or ':' in relative or relative.startswith('/') or not re.fullmatch('[0-9a-f]{64}', expected):
            raise ValueError('Invalid installed release manifest path.')
        if not (relative in ('Host.exe', 'Client.exe', 'Quick Start.exe', 'LICENSE') or relative.startswith('Launcher Bin/')):
            continue
        path = root / relative
        if not path.resolve().is_relative_to(root) or any(_linked_path(p) for p in (path, *path.parents) if p != root.parent):
            raise ValueError('Linked release files cannot be shared.')
        with path.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != expected:
            raise ValueError(f'Installed release file changed: {relative}. Extract a clean release before sharing.')
        files.append((path, relative))
    if not {'Host.exe', 'Client.exe', 'Quick Start.exe'}.issubset({name for _, name in files}):
        raise ValueError('The full installed launcher release is unavailable.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path, relative in files:
                archive.write(path, relative)
            if 'Launcher Bin/Release/version.json' not in {name for _, name in files}:
                archive.write(release / 'version.json', 'Launcher Bin/Release/version.json')
            names = {name for _, name in files}
            checksums = [line for line in (release / 'SHA256SUMS.txt').read_text('utf-8').splitlines()
                         if line.split(' *', 1)[1].replace('\\', '/') in names]
            archive.writestr('Launcher Bin/Release/SHA256SUMS.txt', '\n'.join(checksums)+'\n')
            archive.writestr('Game Directory/', b'')
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


class LauncherShare:
    def __init__(self, package_factory, *, on_change=None, host='0.0.0.0', port=PORT):
        self.factory, self.on_change, self.host, self.port = package_factory, on_change, host, port
        self.requests = {}
        self._recent = {}
        self._writers = set()
        self._tasks = set()
        self._preparing = set()
        self._closed = False
        self._package = None
        self._package_lock = asyncio.Lock()

    async def start(self):
        context, fingerprint = await asyncio.to_thread(_server_certificate)
        self._server = await asyncio.start_server(self._handle, self.host, self.port, ssl=context, limit=8192, ssl_handshake_timeout=CONTROL_TIMEOUT)
        self.port = self._server.sockets[0].getsockname()[1]
        self.metadata = dict(protocol=1, port=self.port, fingerprint=fingerprint)
        self._reaper = asyncio.create_task(self._expire_loop())

    async def _expire_loop(self):
        while True:
            await asyncio.sleep(1)
            before = len(self.requests)
            self._expire()
            if before != len(self.requests):
                self._changed()

    async def stop(self):
        self._closed = True
        self._reaper.cancel()
        self.requests.clear()
        self._server.close()
        for writer in tuple(self._writers):
            writer.transport.abort()
        tasks = tuple(self._tasks | self._preparing)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(self._reaper, return_exceptions=True)
        await asyncio.wait_for(self._server.wait_closed(), 5)
        self._changed()

    def _changed(self):
        if self.on_change:
            self.on_change(self.snapshot())

    def snapshot(self):
        self._expire()
        return [dict(id=key, name=r['name'], ip=r['ip'], status=r['status'], pin=r.get('pin', ''), error=r.get('error', ''))
                for key, r in self.requests.items() if r['status'] not in ('denied', 'complete')]

    def _expire(self):
        for key in [k for k, r in self.requests.items() if r['expires'] < time.monotonic()]:
            self.requests.pop(key, None)
        self._recent = {ip: stamps for ip, stamps in self._recent.items() if stamps[-1] + TTL > time.monotonic()}

    async def decide(self, key, allow):
        self._expire()
        r = self.requests.get(key)
        if not r:
            return
        if not allow:
            r['status'] = 'denied'
            r.pop('token', None)
            r.pop('pin', None)
            self._changed()
            return
        if r['status'] != 'pending':
            return
        r['status'] = 'preparing'
        self._changed()
        task = asyncio.current_task()
        self._preparing.add(task)
        try:
            async with self._package_lock:
                if not self._package:
                    path = Path(await finish_disk_work(self.factory))
                    def digest():
                        with path.open('rb') as stream:
                            return hashlib.file_digest(stream, 'sha256').hexdigest()
                    size = path.stat().st_size
                    if not 0 < size <= MAX_PACKAGE:
                        raise ValueError('Launcher package size is invalid.')
                    self._package = (path, size, await asyncio.to_thread(digest))
            if not self._closed and self.requests.get(key) is r and r['expires'] > time.monotonic():
                r.update(status='approved', pin=f'{secrets.randbelow(1000):03}', attempts=0)
        except Exception as exc:
            r.update(status='error', error=str(exc))
        finally:
            self._preparing.discard(task)
            self._changed()

    async def _handle(self, reader, writer):
        if self._closed or len(self._writers) >= 32:
            writer.transport.abort()
            return
        task = asyncio.current_task()
        self._tasks.add(task); self._writers.add(writer)
        try:
            self._expire()
            data = await _read_message(reader, CONTROL_TIMEOUT)
            ip = writer.get_extra_info('peername')[0]
            action, secret = data.get('action'), data.get('secret')
            if not isinstance(secret, str) or not re.fullmatch('[0-9a-f]{64}', secret):
                return
            if action == 'request':
                now = time.monotonic()
                recent = [t for t in self._recent.get(ip, []) if t + TTL > now]
                if len(recent) >= 3 or len(self.requests) >= 16:
                    await _send_message(writer, {'status': 'rate_limited'}); return
                recent.append(now); self._recent[ip] = recent
                key = secrets.token_urlsafe(24)
                name = ''.join(c for c in str(data.get('name', 'Player'))[:32] if c.isprintable())
                self.requests[key] = dict(secret=secret, ip=ip, name=name, status='pending', expires=now+TTL)
                self._changed()
                await _send_message(writer, dict(status='pending', id=key)); return
            key = data.get('id')
            r = self.requests.get(key) if isinstance(key, str) else None
            if not r or r['ip'] != ip or not hmac.compare_digest(r['secret'], secret):
                await _send_message(writer, {'status': 'expired'}); return
            if action == 'cancel':
                r['status'] = 'denied'
                self.requests.pop(key, None); self._changed()
                await _send_message(writer, {'status': 'cancelled'}); return
            if action == 'poll':
                await _send_message(writer, {'status': r['status'], 'error': r.get('error', '')}); return
            if action == 'unlock' and r['status'] == 'approved':
                pin = data.get('pin')
                r['attempts'] += 1
                if not isinstance(pin, str) or not hmac.compare_digest(r['pin'], pin):
                    if r['attempts'] >= 3:
                        r['status'] = 'denied'; self._changed()
                    await _send_message(writer, {'status': 'wrong_pin' if r['status'] == 'approved' else 'denied'}); return
                r.update(status='ready', token=secrets.token_urlsafe(32))
                self._changed()
                await _send_message(writer, dict(status='ready', token=r['token'])); return
            if action != 'download' or r['status'] != 'ready' or not hmac.compare_digest(str(data.get('token', '')), r.get('token', '')):
                await _send_message(writer, {'status': 'denied'}); return
            r['status'] = 'sending'; self._changed()
            r['expires'] = time.monotonic()+3600
            path, size, digest = self._package
            sent = 0
            await _send_message(writer, dict(status='download', size=size, sha256=digest, version=__version__))
            with path.open('rb') as stream:
                while not self._closed and r['status'] == 'sending' and r['expires'] > time.monotonic():
                    chunk = await finish_disk_work(stream.read, 256*1024)
                    if not chunk:
                        break
                    writer.write(chunk)
                    await asyncio.wait_for(writer.drain(), 15)
                    sent += len(chunk)
            r['status'] = 'complete' if sent == size else 'denied'; self._changed()
        except (OSError, ValueError, asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            self._tasks.discard(task); self._writers.discard(writer)
            writer.close()


async def exchange(beacon, data):
    meta = beacon['launcher_share']
    if meta.get('protocol') != 1:
        raise ValueError('This Host does not support launcher sharing.')
    reader, writer = await AdmissionAccess(meta['fingerprint']).connect(meta.get('host') or beacon['host'], meta['port'], limit=8192, timeout=CONTROL_TIMEOUT)
    try:
        await _send_message(writer, data, CONTROL_TIMEOUT)
        return await _read_message(reader, CONTROL_TIMEOUT)
    finally:
        writer.close()


async def download(beacon, data, destination, progress=None):
    meta = beacon['launcher_share']
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('The destination already exists. Choose a different filename.')
    temporary = destination.with_name(destination.name+'.'+secrets.token_hex(8)+'.part')
    reader, writer = await AdmissionAccess(meta['fingerprint']).connect(meta.get('host') or beacon['host'], meta['port'], limit=8192, timeout=CONTROL_TIMEOUT)
    try:
        await _send_message(writer, dict(data, action='download'))
        header = await _read_message(reader)
        if header.get('status') != 'download' or header.get('version') != beacon['version']:
            raise ValueError('Host did not authorize this launcher download.')
        size = header.get('size')
        if type(size) is not int or not 0 < size <= MAX_PACKAGE:
            raise ValueError('Invalid launcher package size.')
        digest, done = hashlib.sha256(), 0
        with temporary.open('xb') as stream:
            while done < size:
                chunk = await asyncio.wait_for(reader.read(min(256*1024, size-done)), 30)
                if not chunk:
                    raise OSError('Launcher download was interrupted.')
                await finish_disk_work(stream.write, chunk)
                digest.update(chunk); done += len(chunk)
                if progress:
                    progress(done, size)
                    await asyncio.sleep(0)  # Deliver a cancellation requested by the progress callback.
        if not hmac.compare_digest(digest.hexdigest(), header.get('sha256', '')):
            raise ValueError('Launcher package verification failed.')
        # A completed download never runs or installs itself.
        if destination.exists():
            raise FileExistsError('The destination already exists. Choose a different filename.')
        temporary.rename(destination)
        return str(destination)
    finally:
        writer.close()
        temporary.unlink(missing_ok=True)
