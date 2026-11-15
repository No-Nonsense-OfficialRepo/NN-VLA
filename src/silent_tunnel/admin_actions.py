"""Narrow Windows elevation bridge for the launcher's isolated VLA operations.

The elevated process accepts only named operations with validated arguments;
it never evaluates a command script or a command supplied by a room host.
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import shutil
from contextlib import contextmanager
from pathlib import Path

from .backend import SERVICE_NAME
_MOON = re.compile(r"^[0-9a-fA-F]{16}\.moon$")
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
HELPER_NAME = "LAN Helper.exe"
HELPER_FOLDER = Path("Launcher Bin") / "Elevation"


class ElevatedOperationTimeout(RuntimeError):
    """The helper may still be applying changes; its input must remain available."""


@contextmanager
def operation_directory(prefix, dir):
    directory = Path(tempfile.mkdtemp(prefix=prefix, dir=dir))
    try:
        yield str(directory)
    except ElevatedOperationTimeout:
        # Keep input and eventual result for diagnosis. Never kill a service
        # installation halfway through a privileged write.
        raise
    finally:
        if sys.exc_info()[0] is not ElevatedOperationTimeout:
            shutil.rmtree(directory, ignore_errors=True)


def _wait_for_helper(kernel32, process, action, directory):
    seconds = {'install_vla': 300, 'prepare_pc': 900, 'wipe_vla': 960,
               'cleanup_pc': 600, 'restart_service': 180,
               'install_room_file': 240, 'network_private': 600}.get(action, 120)
    status = kernel32.WaitForSingleObject(process, seconds * 1000)
    if status == 0x102:
        raise ElevatedOperationTimeout(
            f"Windows operation '{action}' did not finish within {seconds}s and may still be running. "
            f"Do not retry until the helper exits. Request and eventual result kept at: {directory}")
    if status != 0:
        raise ElevatedOperationTimeout(f"Could not determine whether Windows operation '{action}' finished. "
                                       f"It may still be running; request kept at: {directory}")


def helper_executable():
    """Locate the packaged privileged helper. Older packages kept it at the root."""
    if not getattr(sys, "frozen", False):
        return None
    root = Path(sys.executable).resolve().parent
    candidate = root / HELPER_FOLDER / HELPER_NAME
    if candidate.is_file():
        return candidate
    candidate = root / HELPER_NAME
    return candidate if candidate.is_file() else None


def _is_helper_process() -> bool:
    return Path(sys.executable).name.casefold() == HELPER_NAME.casefold()


def _caller_program(payload: dict):
    """The launcher exe that asked for elevation, only when this process is the helper.

    The program must belong to this helper's release directory."""
    caller = payload.get("caller_program")
    if not caller or not _is_helper_process():
        return None
    path = Path(caller) if isinstance(caller, str) else None
    helper_parent = Path(sys.executable).resolve().parent
    nested = tuple(part.casefold() for part in helper_parent.parts[-2:]) == (
        "launcher bin", "elevation")
    release_root = helper_parent.parent.parent if nested else helper_parent
    if (path is None or not path.is_absolute() or path.suffix.lower() != ".exe" or not path.is_file()
            or path.name.casefold() == HELPER_NAME.casefold()
            or path.resolve().parent != release_root
            or (nested and path.resolve().name.casefold() not in {"host.exe", "client.exe"})):
        raise ValueError("The launcher program path is invalid.")
    return str(path.resolve())


def _valid_interface_indexes(indexes) -> list:
    if (not isinstance(indexes, list) or not indexes or len(indexes) > 8
            or not all(isinstance(i, int) and not isinstance(i, bool) and 0 < i < 1_000_000 for i in indexes)):
        raise ValueError("The network interface list is invalid.")
    return indexes


def _network_private(indexes) -> None:
    from .profile_records import make_private
    make_private(_valid_interface_indexes(indexes))
    return


def _set_network_profile(index, category) -> None:
    if (not isinstance(index, int) or isinstance(index, bool) or not 0 < index < 1_000_000
            or category not in ("Public", "Private")):
        raise ValueError("The network profile selection is invalid.")
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    script = (f"$p = Get-NetConnectionProfile -InterfaceIndex {index} -ErrorAction Stop; "
              "if ($p.NetworkCategory -eq 'DomainAuthenticated') { throw 'Domain profiles cannot be changed here.' }; "
              f"Set-NetConnectionProfile -InterfaceIndex {index} -NetworkCategory {category} -ErrorAction Stop")
    result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
                            capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode != 0:
        raise RuntimeError("Windows couldn't change that adapter's network profile.")


def _install_vla(payload: dict):
    from .installer import install_native_package
    directory = Path(payload.get("path", ""))
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("The VLA package path is invalid.")
    return install_native_package(directory, payload.get("user_sid"))


def _prepare_pc(payload: dict) -> dict:
    """One elevated pass for 'Prepare this PC': VLA service/driver, firewall rules, Private profile.

    Every step is the same fixed operation the individual actions run; nothing here takes a
    command. Returns what happened per step so the UI can say so."""
    from .firewall import apply_rules
    steps = {}
    if payload.get("path"):
        from .installer import _service_exists, installation_pending
        if _service_exists() and not installation_pending():
            steps["vla"] = "already installed"
        else:
            _install_vla(payload)
            steps["vla"] = "installed"
    else:
        steps["vla"] = "skipped"
    apply_rules(_caller_program(payload))
    steps["firewall"] = "rules added"
    if payload.get("interface_indexes"):
        _network_private(payload["interface_indexes"])
        steps["network"] = "set to Private"
    else:
        steps["network"] = "unchanged"
    return {"steps": steps}


def verify_authenticode(path: Path) -> bool:
    """Ask Windows trust policy to verify the embedded Authenticode signature."""
    if os.name != "nt":
        return False

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [("cbStruct", ctypes.c_ulong), ("pcwszFilePath", ctypes.c_wchar_p),
                    ("hFile", ctypes.c_void_p), ("pgKnownSubject", ctypes.POINTER(GUID))]

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = [("cbStruct", ctypes.c_ulong), ("pPolicyCallbackData", ctypes.c_void_p),
                    ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", ctypes.c_ulong),
                    ("fdwRevocationChecks", ctypes.c_ulong), ("dwUnionChoice", ctypes.c_ulong),
                    ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
                    ("dwStateAction", ctypes.c_ulong), ("hWVTStateData", ctypes.c_void_p),
                    ("pwszURLReference", ctypes.c_wchar_p), ("dwProvFlags", ctypes.c_ulong),
                    ("dwUIContext", ctypes.c_ulong), ("pSignatureSettings", ctypes.c_void_p)]

    action = GUID(0x00AAC56B, 0xCD44, 0x11D0,
                  (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    file_info = WINTRUST_FILE_INFO()
    file_info.cbStruct = ctypes.sizeof(file_info)
    file_info.pcwszFilePath = str(Path(path).resolve())
    data = WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(data)
    data.dwUIChoice = 2  # WTD_UI_NONE
    data.dwUnionChoice = 1  # WTD_CHOICE_FILE
    data.dwProvFlags = 0x1000  # WTD_CACHE_ONLY_URL_RETRIEVAL: never fetch trust data.
    data.pFile = ctypes.pointer(file_info)
    wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
    verify = wintrust.WinVerifyTrust
    verify.argtypes = [ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
    verify.restype = ctypes.c_long
    return verify(None, ctypes.byref(action), ctypes.byref(data)) == 0


def verify_driver_catalog(catalog: Path, member: Path) -> bool:
    """Read-only Windows driver-policy trust and exact catalog membership.

    No catalog is registered and no driver is staged, loaded or installed. Use
    cached Windows trust data only. SHA256 and SHA1 cover modern and legacy
    unchanged catalog formats; the Windows driver policy decides signer trust.
    """
    if os.name != "nt":
        return False
    import msvcrt
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    class CATALOG_INFO(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("dwCatalogVersion", wintypes.DWORD),
                    ("pcwszCatalogFilePath", wintypes.LPCWSTR), ("pcwszMemberTag", wintypes.LPCWSTR),
                    ("pcwszMemberFilePath", wintypes.LPCWSTR), ("hMemberFile", wintypes.HANDLE),
                    ("pbCalculatedFileHash", ctypes.POINTER(ctypes.c_ubyte)),
                    ("cbCalculatedFileHash", wintypes.DWORD), ("pcCatalogContext", ctypes.c_void_p),
                    ("hCatAdmin", wintypes.HANDLE)]

    class TRUST_DATA(ctypes.Structure):
        _fields_ = [("cbStruct", wintypes.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                    ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", wintypes.DWORD),
                    ("fdwRevocationChecks", wintypes.DWORD), ("dwUnionChoice", wintypes.DWORD),
                    ("pCatalog", ctypes.POINTER(CATALOG_INFO)), ("dwStateAction", wintypes.DWORD),
                    ("hWVTStateData", wintypes.HANDLE), ("pwszURLReference", wintypes.LPCWSTR),
                    ("dwProvFlags", wintypes.DWORD), ("dwUIContext", wintypes.DWORD),
                    ("pSignatureSettings", ctypes.c_void_p)]

    action = GUID(0xF750E6C3, 0x38EE, 0x11D1,
                  (ctypes.c_ubyte * 8)(0x85, 0xE5, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    try:
        wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
        acquire = wintrust.CryptCATAdminAcquireContext2
        acquire.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(GUID),
                            wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD]
        acquire.restype = wintypes.BOOL
        calculate = wintrust.CryptCATAdminCalcHashFromFileHandle2
        calculate.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                              ctypes.POINTER(ctypes.c_ubyte), wintypes.DWORD]
        calculate.restype = wintypes.BOOL
        release = wintrust.CryptCATAdminReleaseContext
        release.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        release.restype = wintypes.BOOL
        verify = wintrust.WinVerifyTrust
        verify.argtypes = [wintypes.HWND, ctypes.POINTER(GUID), ctypes.POINTER(TRUST_DATA)]
        verify.restype = wintypes.LONG
        catalog = Path(catalog).resolve(strict=True)
        member = Path(member).resolve(strict=True)
        for algorithm in ("SHA256", "SHA1"):
            context = wintypes.HANDLE()
            if not acquire(ctypes.byref(context), ctypes.byref(action), algorithm, None, 0):
                continue
            try:
                with member.open("rb") as stream:
                    handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
                    size = wintypes.DWORD()
                    if not calculate(context, handle, ctypes.byref(size), None, 0) or not 1 <= size.value <= 128:
                        continue
                    digest = (ctypes.c_ubyte * size.value)()
                    if not calculate(context, handle, ctypes.byref(size), digest, 0):
                        continue
                    info = CATALOG_INFO()
                    info.cbStruct = ctypes.sizeof(info)
                    info.pcwszCatalogFilePath = str(catalog)
                    info.pcwszMemberTag = bytes(digest).hex().upper()
                    info.pcwszMemberFilePath = str(member)
                    info.hMemberFile = handle
                    info.pbCalculatedFileHash = digest
                    info.cbCalculatedFileHash = size.value
                    info.hCatAdmin = context
                    data = TRUST_DATA()
                    data.cbStruct = ctypes.sizeof(data)
                    data.dwUIChoice = 2  # WTD_UI_NONE
                    data.dwUnionChoice = 2  # WTD_CHOICE_CATALOG
                    data.pCatalog = ctypes.pointer(info)
                    data.dwStateAction = 1  # WTD_STATEACTION_VERIFY
                    data.dwProvFlags = 0x1000  # WTD_CACHE_ONLY_URL_RETRIEVAL
                    try:
                        trusted = verify(wintypes.HWND(-1), ctypes.byref(action), ctypes.byref(data)) == 0
                    finally:
                        data.dwStateAction = 2  # WTD_STATEACTION_CLOSE
                        verify(wintypes.HWND(-1), ctypes.byref(action), ctypes.byref(data))
                    if trusted:
                        return True
            finally:
                release(context, 0)
    except (OSError, AttributeError, ValueError):
        return False
    return False


def _run_action(payload: dict) -> None:
    action = payload.get("action")
    if action == 'adapter_metric':
        from lantool.netcore.adapter import apply_owned_metric
        apply_owned_metric(payload.get('interface_index'), payload.get('alias'), payload.get('metric'),
                           payload.get('missing_ok', False))
        return
    if action == "wipe_vla":
        from .wipe import wipe_windows
        if _is_helper_process():
            caller = _caller_program(payload)
            if not caller:
                raise ValueError('The launcher program is required to determine the network reset scope.')
            return wipe_windows(root=Path(caller).parent)
        return wipe_windows()
    if action == "install_vla":
        return _install_vla(payload)
    if action == "prepare_pc":
        return _prepare_pc(payload)
    if action == "start_service":
        from .installer import _require_owned_service
        _require_owned_service()
        sc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "sc.exe"
        result = subprocess.run([str(sc), "start", SERVICE_NAME], capture_output=True,
                                timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode not in (0, 1056):
            raise RuntimeError(f"Windows couldn't start VLA (code {result.returncode}).")
        return
    if action == 'launcher_firewall':
        from .firewall import apply_rules
        apply_rules(_caller_program(payload))
        return
    if action == "install_room_file":
        from .local import default_home
        from .installer import _require_owned_service

        _require_owned_service()

        source = Path(payload.get("source", "")).resolve(strict=True)
        kind = payload.get("kind")
        if kind == "planet":
            target = Path(default_home()) / "planet"
        elif kind == "moon":
            filename = payload.get("filename", "")
            if not isinstance(filename, str) or not _MOON.fullmatch(filename):
                raise ValueError("The room network file name is invalid.")
            target = Path(default_home()) / "moons.d" / filename
        else:
            raise ValueError("The room network file type is invalid.")
        if source.suffix.lower() != f".{kind}" or not source.is_file():
            raise ValueError("The room network file is invalid.")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_suffix(target.suffix + ".new")
        if kind == "planet" and target.exists() and not target.with_suffix(".orig").exists():
            target.with_suffix(".orig").write_bytes(target.read_bytes())
        staging.write_bytes(source.read_bytes())
        os.replace(staging, target)
        if payload.get("restart", True):
            _restart_service()
        return

    if action == "cleanup_pc":
        from .cleanup import elevated_cleanup
        return elevated_cleanup(bool(payload.get("remove_service")), bool(payload.get("remove_private")))
    if action == "restart_service":
        _restart_service()
        return
    if action == "network_private":
        _network_private(payload.get("interface_indexes"))
        return
    if action == "set_network_profile":
        _set_network_profile(payload.get("interface_index"), payload.get("category"))
        return
    raise ValueError("Unknown administrative operation.")


def _restart_service() -> None:
    from .installer import _require_owned_service
    _require_owned_service()
    sc = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "sc.exe"

    def query_state():
        result = subprocess.run([str(sc), "query", SERVICE_NAME], capture_output=True,
                                text=True, timeout=10,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("Windows couldn't query the VLA service.")
        match = re.search(r"STATE\s*:\s*\d+\s+(\w+)", result.stdout)
        return match.group(1).upper() if match else "UNKNOWN"

    state = query_state()
    if state != "STOPPED":
        subprocess.run([str(sc), "stop", SERVICE_NAME], capture_output=True, timeout=10,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and query_state() != "STOPPED":
            time.sleep(0.3)
        if query_state() != "STOPPED":
            raise RuntimeError("VLA did not stop within 45 seconds.")
    subprocess.run([str(sc), "start", SERVICE_NAME], capture_output=True, timeout=10,
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and query_state() != "RUNNING":
        time.sleep(0.3)
    if query_state() != "RUNNING":
        raise RuntimeError("VLA did not start within 45 seconds.")


def _allow_unelevated_read(path: Path) -> None:
    """An elevated process creates files with an Administrators-only ACL. Grant the
    Users group read access so the normal-rights launcher can read the result,
    whether or not the launcher itself is elevated."""
    icacls = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe"
    subprocess.run([str(icacls), str(path), "/grant", "*S-1-5-32-545:R"],
                   check=True, capture_output=True, timeout=15)


def run_admin_child(payload_path: str) -> int:
    """Entry point used only by the re-launched process after UAC approval."""
    try:
        path = Path(payload_path).resolve(strict=True)
        payload = json.loads(path.read_text(encoding="utf-8"))
        details = _run_action(payload) or {}
        result = {"ok": True, "error": "", **details}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)[:1000]}
    try:
        result_path = Path(path).parent / "result.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        _allow_unelevated_read(result_path)
    except (OSError, UnboundLocalError, subprocess.SubprocessError):
        pass
    return 0 if result["ok"] else 1


def run_elevated(action: str, **arguments) -> dict:
    """Run one allow-listed operation in this app with a visible UAC consent prompt."""
    if os.name != "nt":
        raise RuntimeError("This operation requires Windows administrator rights.")
    if ctypes.windll.shell32.IsUserAnAdmin():
        details = _run_action({"action": action, **arguments}) or {}
        return {"ok": True, "error": "", **details}
    # The unelevated process and the UAC-elevated process may run under
    # different Windows accounts. A per-user temp directory can therefore be
    # inaccessible to the elevated child (or resolve to a different profile).
    # Use the shared system temp directory and grant the elevated child access
    # through a private per-request directory.
    with operation_directory(prefix="lanlauncher-admin-",
                                     dir=os.environ.get("SystemRoot", r"C:\Windows") + r"\Temp") as directory:
        payload_path = Path(directory) / "request.json"
        result_path = Path(directory) / "result.json"
        helper = helper_executable()
        request = {"action": action, **arguments, "result_path": str(result_path)}
        if helper:
            # The helper is a different exe; tell it which launcher asked (it validates this).
            request["caller_program"] = sys.executable
        payload_path.write_text(json.dumps(request), encoding="utf-8")
        if getattr(sys, "frozen", False):
            executable = str(helper) if helper else sys.executable
            command = ["--lan-admin-action", str(payload_path)]
        else:
            executable = sys.executable
            script = Path(sys.argv[0]).resolve()
            command = [str(script), "--lan-admin-action", str(payload_path)]

        class SHELLEXECUTEINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong), ("fMask", ctypes.c_ulong),
                        ("hwnd", ctypes.c_void_p), ("lpVerb", ctypes.c_wchar_p),
                        ("lpFile", ctypes.c_wchar_p), ("lpParameters", ctypes.c_wchar_p),
                        ("lpDirectory", ctypes.c_wchar_p), ("nShow", ctypes.c_int),
                        ("hInstApp", ctypes.c_void_p), ("lpIDList", ctypes.c_void_p),
                        ("lpClass", ctypes.c_wchar_p), ("hkeyClass", ctypes.c_void_p),
                        ("dwHotKey", ctypes.c_ulong), ("hIconOrMonitor", ctypes.c_void_p),
                        ("hProcess", ctypes.c_void_p)]

        info = SHELLEXECUTEINFO()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = _SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "runas"
        info.lpFile = executable
        info.lpParameters = subprocess.list2cmdline(command)
        info.lpDirectory = str(Path(executable).parent)
        info.nShow = 0
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell_execute = shell32.ShellExecuteExW
        shell_execute.argtypes = [ctypes.POINTER(SHELLEXECUTEINFO)]
        shell_execute.restype = ctypes.c_bool
        if not shell_execute(ctypes.byref(info)):
            error = ctypes.get_last_error()
            if error == 1223:
                raise RuntimeError("Windows asked for permission, but it wasn't approved.")
            raise OSError(error, "Couldn't start the approved Windows operation.")
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            kernel32.WaitForSingleObject.restype = ctypes.c_ulong
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_bool
            _wait_for_helper(kernel32, info.hProcess, action, directory)
        finally:
            kernel32.CloseHandle(info.hProcess)
        if result_path.exists():
            result = json.loads(result_path.read_text(encoding="utf-8"))
        else:
            raise RuntimeError("The elevated Windows operation ended without a result.")
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "The elevated Windows operation failed.")
        return result
