"""Install the private VLA service using an unchanged, signed shared driver.

An ordinary ZeroTier MSI or service is never a VLA installation. Native release
artifacts are mandatory; there is no download or upstream-product fallback.
"""
from __future__ import annotations

import hashlib
import csv
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

from . import backend
from .admin_actions import (run_elevated, verify_authenticode, verify_driver_catalog,
                            operation_directory, ElevatedOperationTimeout)
from .errors import InstallError

SERVICE_NAME = backend.SERVICE_NAME
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
INSTALL_PENDING = '.vla-install-pending.json'


def installation_pending():
    path = backend.install_home() / INSTALL_PENDING
    _no_links(path)
    return path.is_file()
_REQUIRED_FILES = {backend.SERVICE_EXECUTABLE, backend.SERVICE_PROVENANCE,
                   *("driver/" + name for name in (backend.DRIVER_INF, backend.DRIVER_CAT, backend.DRIVER_SYS))}
_MANIFEST_IDENTITY = {
    "schema": 2,
    "backend": backend.BACKEND_ID,
    "service_name": backend.SERVICE_NAME,
    "display_name": backend.SERVICE_DISPLAY_NAME,
    "executable": backend.SERVICE_EXECUTABLE,
    "api_port": backend.DEFAULT_PORT,
    "driver_service": backend.DRIVER_SERVICE,
    "driver_component": backend.DRIVER_COMPONENT,
    "driver_provider": backend.DRIVER_PROVIDER,
    "driver_inf": "driver/" + backend.DRIVER_INF,
    "driver_catalog": "driver/" + backend.DRIVER_CAT,
    "driver_sys": "driver/" + backend.DRIVER_SYS,
    "driver_mode": backend.DRIVER_MODE,
    "driver_hardware_ids": backend.DRIVER_HARDWARE_IDS,
    "driver_remove_on_uninstall": False,
    "driver_source_msi_sha256": backend.DRIVER_SOURCE_MSI_SHA256,
    "service_provenance": backend.SERVICE_PROVENANCE,
}
Progress = Optional[Callable[[str], None]]


def is_windows() -> bool:
    return os.name == "nt"


def _say(progress: Progress, message: str) -> None:
    if progress:
        try:
            progress(message)
        except Exception:
            pass


def _system_tool(name: str) -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name)


def _current_user_sid() -> str:
    """Identify the launcher user before UAC can switch to another account."""
    result = subprocess.run([_system_tool("whoami.exe"), "/user", "/fo", "csv", "/nh"],
                            capture_output=True, text=True, timeout=10, creationflags=_NO_WINDOW)
    rows = list(csv.reader(result.stdout.splitlines())) if result.returncode == 0 else []
    sid = rows[0][-1].strip() if len(rows) == 1 and len(rows[0]) == 2 else ""
    if not re.fullmatch(r"S-1-5-21-(?:\d+-){3}\d+", sid):
        raise InstallError("Windows could not identify the launcher user for VLA access.")
    return sid


def _grant_vla_token_access(user_sid: str) -> None:
    """Let only the installing user read the local API key created by SYSTEM."""
    if not isinstance(user_sid, str) or not re.fullmatch(r"S-1-5-21-(?:\d+-){3}\d+", user_sid):
        raise InstallError("The VLA user identity is invalid.")
    token = backend.default_home() / "authtoken.secret"
    for _ in range(50):
        if token.is_file():
            break
        time.sleep(0.2)
    _no_links(token)
    if not token.is_file():
        raise InstallError("VLA did not create its access key. Check the Windows service.")
    result = subprocess.run([_system_tool("icacls.exe"), str(token), "/grant:r", f"*{user_sid}:(R)"],
                            capture_output=True, timeout=15, creationflags=_NO_WINDOW)
    if result.returncode:
        raise InstallError("Windows could not allow the launcher user to access VLA's local key.")


def _service_exists() -> bool:
    try:
        result = subprocess.run([_system_tool("sc.exe"), "query", SERVICE_NAME],
                                capture_output=True, timeout=10, creationflags=_NO_WINDOW)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _service_is_owned() -> bool:
    """A matching service name alone is insufficient to claim an installation."""
    if not is_windows():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            "SYSTEM\\CurrentControlSet\\Services\\" + SERVICE_NAME) as key:
            command = winreg.QueryValueEx(key, "ImagePath")[0]
        return str(command).strip().casefold() == subprocess.list2cmdline(backend.service_command()).casefold()
    except OSError:
        return False


def is_installed() -> bool:
    """Validate service/package identity; install completion is tracked separately.

    Starting the service during installation requires this integrity check even
    while its journal remains pending. Callers preparing rooms also check
    installation_pending() before treating the installation as ready.
    """
    if not is_windows() or not _service_exists() or not _service_is_owned():
        return False
    try:
        validate_package(backend.install_home(), check_signatures=True)
    except InstallError:
        return False
    return _shared_driver_present()


def _require_owned_service() -> None:
    if not is_installed():
        raise InstallError("The launcher's VLA service is missing or its installation needs repair.")


def start_service() -> None:
    if not is_windows():
        raise InstallError("The VLA backend is currently available only on Windows.")
    _require_owned_service()
    flags = dict(capture_output=True, timeout=15, creationflags=_NO_WINDOW)
    query = subprocess.run([_system_tool("sc.exe"), "query", SERVICE_NAME], **flags)
    if query.returncode:
        raise InstallError("The VLA Windows service is missing. Repair its installation.")
    if re.search(rb"STATE\s*:\s*[24]\b", query.stdout):
        return
    result = subprocess.run([_system_tool("sc.exe"), "start", SERVICE_NAME], **flags)
    if result.returncode == 5:
        run_elevated("start_service")
    elif result.returncode not in (0, 1056):
        raise InstallError(f"Windows couldn't start VLA (code {result.returncode}).")


def restart_service() -> None:
    _require_owned_service()
    run_elevated("restart_service")


def bundled_package_path() -> Optional[Path]:
    roots = []
    if getattr(sys, "_MEIPASS", None):
        roots.append(Path(sys._MEIPASS))
    roots.append(Path(__file__).resolve().parents[1])
    for root in roots:
        candidate = root / "vendor" / "vla"
        if (candidate / backend.MANIFEST_NAME).is_file():
            return candidate
    return None


def _no_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (getattr(item, "is_junction", lambda: False)()):
            raise InstallError("VLA installation paths must not contain links or junctions.")


def validate_package(directory: Path, *, check_signatures: bool = True) -> dict:
    """Validate the native release contract, file hashes and Windows trust."""
    directory = Path(directory)
    _no_links(directory)
    try:
        manifest_path = directory / backend.MANIFEST_NAME
        _no_links(manifest_path)
        manifest = json.loads(manifest_path.read_text("utf-8"))
    except (OSError, ValueError):
        raise InstallError("This package is missing a valid native VLA release manifest.") from None
    if not isinstance(manifest, dict) or any(manifest.get(k) != v for k, v in _MANIFEST_IDENTITY.items()):
        raise InstallError("This is not a compatible native VLA package.")
    files = manifest.get("files")
    if not isinstance(files, dict) or not _REQUIRED_FILES.issubset(files):
        raise InstallError("The private VLA service, build provenance and unchanged signed shared driver are required.")
    if type(manifest.get("service_signature_required", False)) is not bool:
        raise InstallError("The VLA service signature policy is invalid.")
    for name, digest in files.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                or PurePosixPath(name).is_absolute() or any(p in ("", ".", "..") for p in name.split("/"))
                or not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)):
            raise InstallError("The VLA package contains an invalid file entry.")
        path = directory / name
        _no_links(path)
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            raise InstallError(f"The VLA package is missing {name}.") from None
        if actual != digest.lower():
            raise InstallError(f"The VLA package failed its integrity check ({name}).")
        original = backend.SHARED_DRIVER_SHA256.get(name.removeprefix("driver/")) if name.startswith("driver/") else None
        if original and actual != original:
            raise InstallError(f"The signed shared driver must remain byte-for-byte unchanged ({name}).")
    _validate_service_provenance(directory, files)
    try:
        raw_inf = (directory / "driver" / backend.DRIVER_INF).read_bytes()
        inf = raw_inf.decode("utf-16" if raw_inf.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except (OSError, UnicodeError):
        raise InstallError("The VLA driver identity could not be verified.") from None
    if (not re.search(r'(?im)^\s*Provider\s*=\s*"?ZeroTier"?\s*$', inf)
            or not re.search(r"(?im)^\s*AddService\s*=\s*zttap300\s*,", inf)
            or not re.search(r"(?i)root\\zttap300\b", inf)
            or not re.search(r"(?im)^\s*CatalogFile\s*=\s*zttap300.cat\s*$", inf)):
        raise InstallError("The driver package does not have the original signed shared-driver identity.")
    if check_signatures:
        catalog = directory / "driver" / backend.DRIVER_CAT
        for name in (backend.DRIVER_INF, backend.DRIVER_SYS):
            if not verify_driver_catalog(catalog, directory / "driver" / name):
                raise InstallError(f"Windows could not verify the shared driver signature and catalog membership ({name}).")
        if manifest.get("service_signature_required") and not verify_authenticode(directory / backend.SERVICE_EXECUTABLE):
            raise InstallError("The VLA service requires a trusted publisher signature for this package.")
    return manifest


def _validate_service_provenance(directory: Path, files: dict) -> None:
    try:
        provenance = json.loads((directory / backend.SERVICE_PROVENANCE).read_text("utf-8-sig"))
        binary = (directory / backend.SERVICE_EXECUTABLE).read_bytes()
    except (OSError, ValueError):
        raise InstallError("The VLA service build provenance is missing or invalid.") from None
    expected = {
        "schema": 1, "backend": backend.BACKEND_ID, "service_name": backend.SERVICE_NAME,
        "display_name": backend.SERVICE_DISPLAY_NAME, "executable": backend.SERVICE_EXECUTABLE,
        "api_port": backend.DEFAULT_PORT, "driver_mode": backend.DRIVER_MODE,
        "driver_service": backend.DRIVER_SERVICE, "driver_component": backend.DRIVER_COMPONENT,
        "source_commit": backend.NATIVE_SOURCE_COMMIT,
        "executable_sha256": files[backend.SERVICE_EXECUTABLE].lower(),
    }
    if (not isinstance(provenance, dict) or any(provenance.get(k) != v for k, v in expected.items())
            or not re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("patch_sha256", "")))):
        raise InstallError("The VLA service build provenance does not match this private native service.")
    if not any(backend.SERVICE_NAME.encode(encoding) in binary for encoding in ("utf-8", "utf-16-le")):
        raise InstallError("The executable does not contain VLA's private service identity.")


def _registered_shared_driver() -> Optional[Path]:
    """Return the borrowed kernel service image without changing registration."""
    if not is_windows():
        return None
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                             "SYSTEM\\CurrentControlSet\\Services\\" + backend.DRIVER_SERVICE)
    except FileNotFoundError:
        return None
    except OSError:
        raise InstallError("Windows could not inspect the shared network driver; its installation was left unchanged.") from None
    with key:
        try:
            image = str(winreg.QueryValueEx(key, "ImagePath")[0]).strip().strip('"')
        except OSError:
            raise InstallError("The existing shared network driver has incomplete settings; it was left unchanged.") from None
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    if image.casefold().startswith("\\systemroot\\"):
        image = str(windows / image[len("\\SystemRoot\\"):])
    path = Path(os.path.expandvars(image))
    if not path.is_absolute():
        path = windows / path
    _no_links(path)
    if not path.resolve().is_relative_to((windows / "System32").resolve()):
        raise InstallError("The shared network driver uses an unexpected location; it was left unchanged.")
    return path


def _check_shared_driver_compatibility() -> None:
    """Never replace the globally shared service's binary with another build."""
    path = _registered_shared_driver()
    if path is None:
        return
    try:
        matches = hashlib.sha256(path.read_bytes()).hexdigest() == backend.SHARED_DRIVER_SHA256[backend.DRIVER_SYS]
    except OSError:
        matches = False
    if not matches:
        raise InstallError("A different or unreadable shared ZeroTier network driver is already installed. "
                           "VLA left it unchanged to preserve other applications. This VLA package requires a matching driver build.")


def _shared_driver_present() -> bool:
    """Find the exact borrowed SYS or staged package; never claim its ownership.

    Callers validate the matching private package's catalog before using this
    presence check. A staged driver may have no service until its first device.
    """
    if not is_windows():
        return False
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    try:
        path = _registered_shared_driver()
        if path is not None:
            return hashlib.sha256(path.read_bytes()).hexdigest() == backend.SHARED_DRIVER_SHA256[backend.DRIVER_SYS]
    except (OSError, InstallError, ValueError):
        return False
    store = windows / "System32/DriverStore/FileRepository"
    try:
        _no_links(store)
        for directory in store.glob(backend.DRIVER_INF + "_amd64_*"):
            try:
                _no_links(directory)
                matches = True
                for name, expected in backend.SHARED_DRIVER_SHA256.items():
                    file = directory / name
                    _no_links(file)
                    if hashlib.sha256(file.read_bytes()).hexdigest() != expected:
                        matches = False
                        break
            except (OSError, InstallError):
                continue
            if matches:
                return True
    except (OSError, InstallError):
        pass
    return False


def _check_port_available() -> None:
    """Refuse collisions; never stop another product to free VLA's port."""
    sockets = []
    try:
        for kind in (socket.SOCK_STREAM, socket.SOCK_DGRAM):
            sock = socket.socket(socket.AF_INET, kind)
            sockets.append(sock)
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind(("0.0.0.0", backend.DEFAULT_PORT))
    except OSError:
        raise InstallError(f"VLA's dedicated port {backend.DEFAULT_PORT} is in use. Close the conflicting application and retry.") from None
    finally:
        for sock in sockets:
            sock.close()


def install(download_dir: Path, progress: Progress = None) -> None:
    if not is_windows():
        raise InstallError("The VLA backend is currently available only on Windows.")
    package = bundled_package_path()
    if package is None:
        raise InstallError("This launcher does not include the private VLA service and unchanged signed shared driver. "
                           "A complete VLA release is required before rooms can be opened.")
    _say(progress, "Checking the VLA service and driver…")
    manifest = validate_package(package)
    if _service_exists() and not installation_pending():
        raise InstallError("A VLA service is already registered. Repair or remove that VLA installation before retrying.")
    _check_shared_driver_compatibility()
    if not _service_exists():
        _check_port_available()
    user_sid = _current_user_sid()
    download_dir = Path(download_dir)
    _no_links(download_dir)
    download_dir.mkdir(parents=True, exist_ok=True)
    with operation_directory(prefix="vla-package-", dir=download_dir) as staging:
        local = Path(staging)
        for name in [backend.MANIFEST_NAME, *manifest["files"]]:
            target = local / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package / name, target)
        validate_package(local)
        _say(progress, "Installing VLA… (click Yes if Windows asks)")
        try:
            run_elevated("install_vla", path=str(local), user_sid=user_sid)
        except ElevatedOperationTimeout:
            raise
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            raise InstallError(f"VLA installation failed: {exc}") from None
    if not is_installed() or installation_pending():
        raise InstallError("VLA did not finish installing. Repair its installation and retry.")


def install_native_package(directory: Path, user_sid: str) -> None:
    """Elevated-only implementation; every destination and command is fixed."""
    if not is_windows():
        raise InstallError("The VLA backend is currently available only on Windows.")
    if not isinstance(user_sid, str) or not re.fullmatch(r"S-1-5-21-(?:\d+-){3}\d+", user_sid):
        raise InstallError("The VLA user identity is invalid.")
    manifest = validate_package(directory)
    registered = _service_exists()
    if registered and (not installation_pending() or not _service_is_owned()):
        raise InstallError("A VLA service is already registered; it was left unchanged.")
    _check_shared_driver_compatibility()
    if not registered:
        _check_port_available()
    destination = backend.install_home()
    state = backend.default_home()
    _no_links(destination)
    _no_links(state)
    pending = destination / INSTALL_PENDING
    _no_links(pending)
    if pending.exists():
        try:
            saved = json.loads(pending.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise InstallError('VLA installation journal is damaged. Use Wipe VLA before retrying.') from None
        if saved != {'files': manifest['files'], 'user_sid': user_sid}:
            raise InstallError('An interrupted VLA install used another package or user. Use Wipe VLA before retrying.')
    elif destination.exists() and any(destination.iterdir()):
        raise InstallError("The VLA installation folder is not empty. Repair or remove it before retrying.")
    destination.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True)
    if not pending.exists():
        with pending.open('x', encoding='utf-8') as journal:
            json.dump({'files': manifest['files'], 'user_sid': user_sid}, journal)
            journal.flush()
            os.fsync(journal.fileno())
    for name in [backend.MANIFEST_NAME, *manifest["files"]]:
        target = destination / name
        _no_links(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        # A resumed service may have its executable open. The journal identifies
        # the exact package, so keep already matching files rather than rewrite.
        if not target.is_file() or target.read_bytes() != (Path(directory) / name).read_bytes():
            if registered:
                raise InstallError('An interrupted VLA installation changed on disk. Use Wipe VLA before retrying.')
            shutil.copyfile(Path(directory) / name, target)
    validate_package(destination)
    if not _shared_driver_present():
        # Borrow the original signed package. Do not globally update matching
        # devices (/install) and never delete this package during VLA removal.
        result = subprocess.run([_system_tool("pnputil.exe"), "/add-driver", str(destination / "driver" / backend.DRIVER_INF)],
                                capture_output=True, timeout=120, creationflags=_NO_WINDOW)
        if result.returncode not in (0, 3010):
            raise InstallError(f"Windows could not stage the signed shared driver (code {result.returncode}).")
    command = subprocess.list2cmdline(backend.service_command())
    result = subprocess.CompletedProcess([], 0) if registered else subprocess.run([_system_tool("sc.exe"), "create", SERVICE_NAME, "binPath=", command,
                             "start=", "auto", "DisplayName=", backend.SERVICE_DISPLAY_NAME],
                            capture_output=True, timeout=30, creationflags=_NO_WINDOW)
    if result.returncode:
        raise InstallError(f"Windows could not register VLA (code {result.returncode}).")
    result = subprocess.run([_system_tool("netsh.exe"), "advfirewall", "firewall", "add", "rule",
                             "name=LanLauncherVLA-Transport-In", "dir=in", "action=allow", "protocol=UDP",
                             "program=" + str(destination / backend.SERVICE_EXECUTABLE), "enable=yes", "profile=any"],
                            capture_output=True, timeout=30, creationflags=_NO_WINDOW)
    if result.returncode:
        raise InstallError(f"Windows could not configure the VLA firewall rule (code {result.returncode}).")
    start_service()
    _grant_vla_token_access(user_sid)
    pending.unlink()


def uninstall(progress: Progress = None) -> bool:
    """Remove private VLA resources; the borrowed shared driver stays installed."""
    if not is_windows():
        raise InstallError("Automatic VLA removal requires Windows.")
    if not _service_exists() and not backend.install_home().exists() and not backend.default_home().exists():
        return False
    _say(progress, "Removing VLA… (click Yes if Windows asks)")
    try:
        result = run_elevated("wipe_vla")
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"VLA removal failed: {exc}") from None
    report = result.get("report") or {}
    if report.get("issues"):
        raise InstallError("VLA removal was incomplete: " + "; ".join(str(item) for item in report["issues"]))
    if report.get("reboot"):
        raise InstallError("Restart Windows to finish removing VLA.")
    return True
