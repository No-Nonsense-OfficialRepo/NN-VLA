"""Tiny JSON persistence for settings (user-chosen) and state (machine-managed)."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


def data_dir() -> Path:
    """Where Silent Tunnel keeps its files. Override with SILENT_TUNNEL_HOME."""
    override = os.environ.get("SILENT_TUNNEL_HOME")
    if override:
        return Path(override)
    from lantool import appdirs
    if os.name == "nt" or appdirs.is_portable():
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NN VLA" / "state"
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "lan-launcher" / "vla-state"


@dataclass
class Settings:
    uninstall_on_exit: bool = False     # keep the driver for fast subsequent starts


@dataclass
class State:
    installed_by_us: bool = False       # did Silent Tunnel install ZeroTier on this PC?
    joined: list = field(default_factory=list)   # networks we joined (for crash recovery)
    hosted_network: str = ""            # locally controlled room not yet deleted

    def __post_init__(self):
        if not isinstance(self.joined, list):
            self.joined = []


def _load(path: Path, cls):
    raw = {}
    damaged = False
    loaded = False
    for candidate in (path, path.with_suffix(path.suffix + '.bak')):
        try:
            raw = json.loads(candidate.read_text('utf-8'))
            if not isinstance(raw, dict):
                raise ValueError('Expected state object')
            loaded = True
            break
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            damaged = True
            continue
    if damaged and not loaded:
        raise OSError(f'Cannot recover {path}; restore a valid state backup before continuing.')
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in names})


def load_settings(path: Path) -> Settings:
    return _load(path, Settings)


def load_state(path: Path) -> State:
    return _load(path, State)


def save_obj(path: Path, obj) -> None:
    """Serialize writers and retain a durable recovery copy."""
    from lantool.config_store import _locked
    from lantool.filesync.index import replace_file
    import uuid
    path.parent.mkdir(parents=True, exist_ok=True)
    with _locked(path):
        for target in (path, path.with_suffix(path.suffix + '.bak')):
            tmp = target.with_name(target.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                with tmp.open('w', encoding='utf-8') as stream:
                    json.dump(asdict(obj), stream, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
                replace_file(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
