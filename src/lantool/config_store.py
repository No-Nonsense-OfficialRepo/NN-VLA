"""Merge settings changes under a cross-process lock before atomic replacement."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

_mutex = threading.RLock()
_baselines = {}


def _merge_changes(current, baseline, snapshot):
    """Apply only paths changed by this writer, preserving other writers' edits."""
    merged = copy.deepcopy(current)
    for key in baseline.keys() - snapshot.keys():
        merged.pop(key, None)
    for key, value in snapshot.items():
        if key in baseline and value == baseline[key]:
            continue
        if (key in baseline and isinstance(value, dict) and isinstance(baseline[key], dict)
                and isinstance(merged.get(key), dict)):
            merged[key] = _merge_changes(merged[key], baseline[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _read(path):
    try:
        data = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        return {}
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Settings file must contain an object.')
    return value


def read(path):
    with _mutex:
        try:
            value = _read(path)
        except (OSError, ValueError):
            value = {}
        _baselines[str(Path(path).resolve())] = copy.deepcopy(value)
        return value


@contextmanager
def _locked(path, timeout=5):
    with Path(str(path) + '.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if not stream.tell():
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another launcher is still saving settings.')
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    key = str(path.resolve())
    with _mutex, _locked(path):
        snapshot = copy.deepcopy(value)
        baseline = _baselines.get(key, {})
        try:
            current = _read(path)
        except (ValueError, UnicodeError):
            # Preserve the damaged record before accepting fresh settings.
            shutil.copyfile(path, path.with_name(path.name + '.corrupt-' + uuid.uuid4().hex))
            current = {}
        current = _merge_changes(current, baseline, snapshot)
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(current, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            from .filesync.index import replace_file
            replace_file(temporary, path)
            _baselines[key] = snapshot
        finally:
            temporary.unlink(missing_ok=True)
