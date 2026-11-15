"""Elevated, identity-checked journal for optional network profile changes."""
import json
import os
import subprocess
import uuid
import threading
from contextlib import contextmanager
from . import backend

_mutex = threading.RLock()


@contextmanager
def transaction():
    """Serialize complete Windows changes and journal updates across processes.

    Keep the lock beside VLA's data directory so wipe can delete that directory
    while retaining the lock through the destructive operation.
    """
    from .installer import _no_links
    from lantool.config_store import _locked
    lock_base = backend.default_home().parent / 'vla-profile-records'
    _no_links(lock_base)
    _no_links(lock_base.with_name(lock_base.name + '.lock'))
    lock_base.parent.mkdir(parents=True, exist_ok=True)
    with _mutex, _locked(lock_base, timeout=600):
        yield


def record_path():
    return backend.default_home() / 'prepare-private-record.json'


def _ps(script):
    powershell = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'),
                              'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
    result = subprocess.run([powershell, '-NoProfile', '-NonInteractive', '-Command',
                             "$ErrorActionPreference='Stop'; " + script],
                            capture_output=True, text=True, timeout=30,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('Windows could not read or restore the recorded network profile.')
    return result.stdout.strip()


def identity(index):
    raw = _ps(f"$a = Get-NetAdapter -InterfaceIndex {int(index)} -IncludeHidden -ErrorAction Stop; "
              f"$p = Get-NetConnectionProfile -InterfaceIndex {int(index)} -ErrorAction Stop; "
              "[pscustomobject]@{index=[int]$a.ifIndex; guid=[string]$a.InterfaceGuid; "
              "profile=[string]$p.Name; profile_id=[string]$p.InstanceID; category=[string]$p.NetworkCategory} | ConvertTo-Json -Compress")
    value = json.loads(raw)
    if not isinstance(value, dict) or not value.get('guid') or not value.get('profile') or not value.get('profile_id'):
        raise RuntimeError('Windows did not return a stable network profile identity.')
    uuid.UUID(value['guid'].strip('{}'))
    return value


def load():
    from .installer import _no_links
    path = record_path()
    _no_links(path)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {'changed': True, 'profiles': []}
    if not isinstance(value, dict):
        raise RuntimeError('Network profile recovery record is damaged.')
    return value


def save(value):
    from .installer import _no_links
    path = record_path()
    _no_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def set_checked(profile, category):
    index = profile['index']
    if type(index) is not int or not 0 < index < 1_000_000 or category not in ('Public', 'Private'):
        raise ValueError('Invalid recorded profile settings.')
    guid = str(uuid.UUID(profile['guid'].strip('{}')))
    name = profile['profile'].replace("'", "''")
    profile_id = profile['profile_id'].replace("'", "''")
    _ps(f"$a=Get-NetAdapter -InterfaceIndex {index} -IncludeHidden -ErrorAction Stop; "
        f"$p=Get-NetConnectionProfile -InterfaceIndex {index} -ErrorAction Stop; "
        f"if ($a.InterfaceGuid.ToString().Trim('{{}}') -ine '{guid}' -or $p.Name -cne '{name}' "
        f"-or $p.InstanceID -cne '{profile_id}') "
        "{ throw 'Network identity changed; profile left unchanged.' }; "
        "if ($p.NetworkCategory -eq 'DomainAuthenticated') { throw 'Domain network left unchanged.' }; "
        f"Set-NetConnectionProfile -InterfaceIndex {index} -NetworkCategory {category} -ErrorAction Stop")


def make_private(indexes):
    with transaction():
        _make_private_unlocked(indexes)


def _make_private_unlocked(indexes):
    value = load()
    profiles = value.setdefault('profiles', [])
    if not isinstance(profiles, list):
        raise RuntimeError('Network profile recovery record is damaged.')
    for index in indexes:
        current = identity(index)
        if current['category'] != 'Public':
            continue
        if not any(row.get('guid') == current['guid'] and row.get('profile_id') == current['profile_id'] for row in profiles):
            profiles.append(current)
        # Save before mutation, including on partial failure or parent exit.
        save(value)
        set_checked(current, 'Private')


def restore():
    with transaction():
        _restore_unlocked()


def _restore_unlocked():
    value = load()
    if value.get('indexes'):
        raise RuntimeError('Legacy network profile record lacks adapter identity; restore its profiles manually.')
    profiles = value.get('profiles', [])
    if not isinstance(profiles, list):
        raise RuntimeError('Network profile recovery record is damaged.')
    errors = []
    for profile in list(profiles):
        # Revalidate the GUID and network name inside the same command that
        # changes the category. Failed rows remain available for a retry.
        try:
            set_checked(profile, profile['category'])
        except (OSError, RuntimeError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            errors.append(str(exc))
            continue
        profiles.remove(profile)
        save(value)
    if not profiles:
        record_path().unlink(missing_ok=True)
    if errors:
        raise RuntimeError('Some network profiles were left unchanged: ' + '; '.join(errors))
