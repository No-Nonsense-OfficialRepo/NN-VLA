"""File index, path safety and hash cache shared by the sync server and client (Workstream G, F8)."""
from __future__ import annotations
import asyncio, functools, hashlib, itertools, json, os, re, sqlite3, threading, time
from pathlib import Path

_SAVE_SEQ = itertools.count()

PART_SUFFIX = ".lantool-part"
CACHE_NAME = ".lantool-hashcache.json"
CHUNK = 1024 * 1024
HASH_CACHE_MAX_AGE = 24 * 60 * 60
GAME_ID_RE = re.compile(r"^[a-z0-9_\-]+$")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class PathError(ValueError):
    pass


class IndexCancelled(Exception):
    """A disk worker stopped between reads without publishing a partial index."""


def check_cancelled(cancelled):
    if cancelled and cancelled():
        raise IndexCancelled()


def replace_file(src, dst, attempts=6):
    """os.replace that rides out brief Windows sharing locks (antivirus, indexer) instead of failing the transfer."""
    for attempt in range(attempts):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1 * (attempt + 1))


async def finish_disk_work(function, *args):
    """Finish the current bounded disk operation before closing its file."""
    work = asyncio.get_running_loop().run_in_executor(None, function, *args)
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if work.done() and not work.cancelled():
            work.exception()
        raise


async def run_hash_work(function, *args, stop=None, **kwargs):
    """Cancel the actual hash worker and wait for its file handles to close."""
    cancelled = threading.Event()
    should_stop = lambda: cancelled.is_set() or (stop is not None and stop.is_set())
    work = asyncio.get_running_loop().run_in_executor(
        None, functools.partial(function, *args, cancelled=should_stop, **kwargs))
    try:
        return await asyncio.shield(work)
    except asyncio.CancelledError:
        cancelled.set()
        # Shield the executor future: cancelling a Future never stops a thread.
        # Even repeated shutdown cancellation must wait for its current read.
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if work.done() and not work.cancelled():
            work.exception()  # Retrieve cooperative-cancellation/read errors.
        raise


def safe_rel(rel: str) -> str:
    """Validate a forward-slash relative path. Returns it unchanged or raises PathError."""
    if not isinstance(rel, str) or not rel or "\\" in rel or "\x00" in rel or rel.startswith("/"):
        raise PathError(f"bad path {rel!r}")
    for seg in rel.split("/"):
        if seg in ("", ".", ".."):
            raise PathError(f"bad path {rel!r}")
        if ":" in seg or seg[-1] in (" ", ".") or seg.split(".")[0].lower() in _RESERVED:
            raise PathError(f"bad path {rel!r}")
    return rel


def safe_join(root, rel: str) -> Path:
    """root/rel after validation, guaranteed to stay inside root."""
    root = Path(root).resolve()
    p = (root / safe_rel(rel)).resolve()
    if root not in p.parents:
        raise PathError(f"path escapes root: {rel!r}")
    return p


def is_internal(name: str) -> bool:
    return name.endswith(PART_SUFFIX) or name == CACHE_NAME or name.startswith('.lantool-install.json')


def hash_file(path, chunk: int = CHUNK, *, cancelled=None) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            check_cancelled(cancelled)
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


class HashCache:
    """sha256 by (size, mtime_ns). In memory, optionally persisted as JSON. Keys are caller-chosen strings."""
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._d: dict[str, list] = {}
        self._mutex = threading.Lock()  # several games can be hashed at once (per-game index locks)
        if self.path and self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                self._d = value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                self._d = {}

    def get(self, key: str, st: os.stat_result) -> str | None:
        e = self._lookup(key)
        if self._fresh(e) and e[0] == st.st_size and e[1] == st.st_mtime_ns:
            return e[2]
        return None

    @staticmethod
    def _fresh(entry):
        return (isinstance(entry, (list, tuple)) and len(entry) >= 5
                and isinstance(entry[4], (int, float))
                and 0 <= time.time() - entry[4] < HASH_CACHE_MAX_AGE)

    def put(self, key: str, st: os.stat_result, sha: str):
        self._store(key, [st.st_size, st.st_mtime_ns, sha, None, time.time()])

    def _lookup(self, key):
        with self._mutex:
            return self._d.get(key)

    def _store(self, key, entry):
        with self._mutex:
            self._d[key] = entry

    def hash(self, key: str, path) -> tuple[str, bool]:
        """(sha256, was_cached). Hashes the file if the cache entry is missing or stale."""
        st = os.stat(path)
        sha = self.get(key, st)
        if sha:
            return sha, True
        sha = hash_file(path)
        self.put(key, st, sha)
        return sha, False

    def hashes_with_pieces(self, key: str, path, *, cancelled=None, on_bytes=None,
                           verify=False) -> tuple[str, list[str], bool]:
        check_cancelled(cancelled)
        for attempt in range(3):
            st = os.stat(path)
            entry = None if verify else self._lookup(key)
            valid_pieces = (self._fresh(entry) and isinstance(entry[3], list)
                            and isinstance(entry[2], str) and re.fullmatch(r'[0-9a-f]{64}', entry[2])
                            and len(entry[3]) == (st.st_size + CHUNK - 1) // CHUNK
                            and all(isinstance(p, str) and re.fullmatch(r'[0-9a-f]{64}', p)
                                    for p in entry[3]))
            if valid_pieces and entry[0] == st.st_size and entry[1] == st.st_mtime_ns:
                if on_bytes:
                    on_bytes(st.st_size)
                return entry[2], entry[3], True
            whole, pieces = hashlib.sha256(), []
            with open(path, "rb") as f:
                before = os.fstat(f.fileno())
                while True:
                    check_cancelled(cancelled)
                    block = f.read(CHUNK)
                    if not block:
                        break
                    whole.update(block)
                    pieces.append(hashlib.sha256(block).hexdigest())
                    if on_bytes:
                        on_bytes(len(block))
                after = os.fstat(f.fileno())
            current = os.stat(path)
            if (before.st_size, before.st_mtime_ns, after.st_size, after.st_mtime_ns) == \
                    (st.st_size, st.st_mtime_ns, st.st_size, st.st_mtime_ns) and \
                    (current.st_size, current.st_mtime_ns) == (st.st_size, st.st_mtime_ns):
                sha = whole.hexdigest()
                self._store(key, [st.st_size, st.st_mtime_ns, sha, pieces, time.time()])
                return sha, pieces, False
            if attempt == 2:
                raise OSError(f"File changed during indexing: {path}")
        raise AssertionError('unreachable')

    def forget_except(self, keys):
        keep = set(keys)
        with self._mutex:
            self._d = {k: v for k, v in self._d.items() if k in keep}

    def forget_under(self, root, keys):
        """Drop entries under root that are not in keys. Entries for other game folders are kept."""
        prefix = os.path.join(str(root), "")
        keep = set(keys)
        with self._mutex:
            self._d = {k: v for k, v in self._d.items() if not k.startswith(prefix) or k in keep}

    def save(self):
        if self.path:
            # Unique temp name per call: a save from the seeding scan and one from a starting sync
            # must not write the same .tmp file.
            tmp = self.path.with_suffix(f"{self.path.suffix}.{os.getpid()}.{threading.get_ident()}.{next(_SAVE_SEQ)}.tmp")
            with self._mutex:
                data = json.dumps(dict(self._d))
            tmp.write_text(data, encoding="utf-8")
            replace_file(tmp, self.path)


class SqliteHashCache(HashCache):
    """Host cache: each verified file commits separately, surviving an interrupted scan."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.Lock()
        try:
            self._open()
        except sqlite3.DatabaseError:
            self._db.close()
            # The cache is derived data. Keep the damaged copy for diagnosis,
            # then rebuild from game files without requiring a manual wipe.
            self.path.replace(self.path.with_name(self.path.name + f'.corrupt-{int(time.time())}'))
            for suffix in ('-wal', '-shm'):
                sidecar = Path(str(self.path) + suffix)
                if sidecar.exists():
                    sidecar.unlink()
            self._open()

    def _open(self):
        self._db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('CREATE TABLE IF NOT EXISTS files '
                         '(path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, sha TEXT, pieces TEXT)')
        columns = {row[1] for row in self._db.execute('PRAGMA table_info(files)')}
        if 'checked_at' not in columns:
            self._db.execute('ALTER TABLE files ADD COLUMN checked_at REAL')
        self._db.commit()

    def _lookup(self, key):
        with self._mutex:
            row = self._db.execute('SELECT size, mtime_ns, sha, pieces, checked_at FROM files WHERE path=?', (key,)).fetchone()
        if not row:
            return None
        if not row[3]:
            return [row[0], row[1], row[2], None, row[4]]
        try:
            pieces = json.loads(row[3])
        except (TypeError, ValueError):
            return None
        return [row[0], row[1], row[2], pieces, row[4]] if isinstance(pieces, list) else None

    def _store(self, key, entry):
        with self._mutex:
            self._db.execute('INSERT OR REPLACE INTO files (path, size, mtime_ns, sha, pieces, checked_at) VALUES (?, ?, ?, ?, ?, ?)',
                             (key, entry[0], entry[1], entry[2],
                              json.dumps(entry[3]) if len(entry) > 3 else None,
                              entry[4] if len(entry) > 4 else None))
            self._db.commit()

    def forget_under(self, root, keys):
        prefix = os.path.join(str(root), '')
        keep = set(keys)
        with self._mutex:
            rows = self._db.execute('SELECT path FROM files WHERE substr(path, 1, ?) = ?',
                                    (len(prefix), prefix)).fetchall()
            self._db.executemany('DELETE FROM files WHERE path=?',
                                 ((row[0],) for row in rows if row[0] not in keep))
            self._db.commit()

    def save(self):
        pass  # Every file is already committed.

    def close(self):
        with self._mutex:
            self._db.close()


def build_index(game_id: str, root, cache: HashCache | None = None, *, cancelled=None,
                progress=None, verify=False) -> dict:
    """Unsigned index of every regular file under root (symlinks and internal files skipped)."""
    root = Path(root)
    cache = cache if cache is not None else HashCache()
    def candidates():
        found = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            check_cancelled(cancelled)
            dirnames.sort()
            for name in sorted(filenames):
                check_cancelled(cancelled)
                full = Path(dirpath) / name
                if is_internal(name) or full.is_symlink():
                    continue
                rel = full.relative_to(root).as_posix()
                try:
                    safe_rel(rel)
                except PathError:
                    continue
                stat = full.stat()
                found.append((rel, full, stat.st_size, stat.st_mtime_ns))
        return found

    for attempt in range(2):
        snapshot = candidates()
        total = sum(row[2] for row in snapshot)
        done, files = 0, []
        if progress:
            progress(0, total, 0, len(snapshot), '')
        for rel, full, size, _mtime in snapshot:
            check_cancelled(cancelled)
            file_done = 0
            def advance(n):
                nonlocal file_done
                file_done = min(size, file_done + n)
                if progress:
                    progress(min(total, done + file_done), total, len(files), len(snapshot), rel)
            sha, pieces, _ = cache.hashes_with_pieces(str(full), full, cancelled=cancelled,
                                                       on_bytes=advance, verify=verify)
            files.append({"path": rel, "size": full.stat().st_size,
                          "sha256": sha, "pieces": pieces})
            done += size
            if progress:
                progress(min(done, total), total, len(files), len(snapshot), rel)
        check_cancelled(cancelled)
        if [(r[0], r[2], r[3]) for r in candidates()] == [(r[0], r[2], r[3]) for r in snapshot]:
            cache.forget_under(root, (str(Path(root) / f["path"]) for f in files))
            return {"game_id": game_id, "files": files, "total_bytes": sum(f["size"] for f in files)}
        if attempt == 1:
            raise OSError(f"Game files kept changing during indexing: {game_id}")
    raise AssertionError('unreachable')


def validate_index(idx: dict, game_id: str) -> list[dict]:
    """Structural check of a (signature-verified) index. Returns the file list or raises ValueError."""
    if idx.get("game_id") != game_id:
        raise ValueError("index is for a different game")
    files = idx.get("files")
    if not isinstance(files, list):
        raise ValueError("index has no file list")
    seen = set()
    for f in files:
        safe_rel(f.get("path"))
        if f["path"].lower() in seen:
            raise ValueError(f"duplicate path {f['path']!r}")
        seen.add(f["path"].lower())
        if not isinstance(f.get("size"), int) or f["size"] < 0:
            raise ValueError(f"bad size for {f['path']!r}")
        if not isinstance(f.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", f["sha256"]):
            raise ValueError(f"bad hash for {f['path']!r}")
        if "pieces" in f:
            pieces = f["pieces"]
            expected = (f["size"] + CHUNK - 1) // CHUNK
            if not isinstance(pieces, list) or len(pieces) != expected or any(
                    not isinstance(piece, str) or not re.fullmatch(r"[0-9a-f]{64}", piece)
                    for piece in pieces):
                raise ValueError(f"bad piece hashes for {f['path']!r}")
    for path in seen:
        segments = path.split('/')
        if any('/'.join(segments[:i]) in seen for i in range(1, len(segments))):
            raise ValueError(f"file conflicts with directory: {path!r}")
    return files


def diff_index(files: list[dict], dest, cache: HashCache, verify: bool = False, *, cancelled=None) -> tuple[list[dict], list[dict]]:
    """Split index entries into (need_download, unchanged) by size + sha256 against dest."""
    dest = Path(dest)
    need, same = [], []
    for f in files:
        check_cancelled(cancelled)
        p = safe_join(dest, f["path"])
        try:
            st = p.stat()
        except OSError:
            need.append(f)
            continue
        if not p.is_file() or st.st_size != f["size"]:
            need.append(f)
            continue
        key = f["path"]
        sha = None if verify else cache.get(key, st)
        if sha is None:
            sha = hash_file(p, cancelled=cancelled)
            cache.put(key, st, sha)
        (same if sha == f["sha256"] else need).append(f)
    return need, same
