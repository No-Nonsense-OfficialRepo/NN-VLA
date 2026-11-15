"""Adapter metric switching (F3) and crash-recovery state (F14). Windows via PowerShell; runner is injectable for tests."""
from __future__ import annotations
import json, os, subprocess, uuid
from pathlib import Path

ZT_LOW_METRIC = 5
POWERSHELL_TIMEOUT = 30
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The kernel driver is shared. Its service name alone does not prove that an
# adapter belongs to this launcher; only our native service creates this tag
# and matching private device ledger.
_OWNERSHIP_PS = r"""
function Test-VlaAdapter($device) {
    if (-not $device -or -not $device.PnPDeviceID) { return $false }
    $service = (Get-PnpDeviceProperty -InstanceId $device.PnPDeviceID -KeyName 'DEVPKEY_Device_Service' -ErrorAction SilentlyContinue).Data
    if ($service -ine 'zttap300') { return $false }
    $netClass = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}'
    foreach ($key in Get-ChildItem -LiteralPath $netClass -ErrorAction SilentlyContinue) {
        $entry = Get-ItemProperty -LiteralPath $key.PSPath
        if ($entry.ComponentId -ine 'zttap300' -or
            $entry.DeviceInstanceID -ine $device.PnPDeviceID -or
            $entry._VLATapIdentifier -cnotmatch '^[0-9a-f]{16}$') { continue }
        if ($device.InterfaceGuid -and $entry.NetCfgInstanceId -and
            $entry.NetCfgInstanceId.Trim('{}') -ine $device.InterfaceGuid.ToString().Trim('{}')) { continue }
        $record = Join-Path $env:ProgramData ('LAN Launcher\VLA\vla-devices.d\' + $entry._VLATapIdentifier + '.instance')
        if ((Test-Path -LiteralPath $record -PathType Leaf) -and
            (Get-Content -LiteralPath $record -Raw).Trim() -ieq $device.PnPDeviceID) { return $true }
    }
    return $false
}
"""


def _ps(cmd: str) -> str:
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd],
                                capture_output=True, text=True, timeout=POWERSHELL_TIMEOUT,
                                creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Windows adapter operation timed out after {POWERSHELL_TIMEOUT}s. Retry Repair LAN.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(detail or f"PowerShell exited with code {result.returncode}.")
    return result.stdout.strip()


def apply_owned_metric(interface_index, alias, metric, missing_ok=False):
    """Elevated operation: change IPv4 metrics only on a verified VLA adapter."""
    if interface_index is not None and (type(interface_index) is not int or interface_index <= 0):
        raise ValueError('Invalid network interface index.')
    if not isinstance(alias, str) or not alias or len(alias) > 256:
        raise ValueError('Invalid network interface name.')
    if type(metric) is not int or not 0 <= metric <= 9999:
        raise ValueError('Invalid network interface metric.')
    if type(missing_ok) is not bool:
        raise ValueError('Invalid network interface recovery flag.')
    escaped = alias.replace("'", "''")
    selector = (f'-InterfaceIndex {interface_index}' if interface_index is not None else
                f"-InterfaceAlias '{escaped}'")
    setting = '-AutomaticMetric Enabled' if metric == 0 else f'-InterfaceMetric {metric}'
    _ps(_OWNERSHIP_PS + "$ErrorActionPreference='Stop'; " +
        f'$iface = Get-NetIPInterface {selector} -AddressFamily IPv4 '
        '-ErrorAction SilentlyContinue | Select-Object -First 1; '
        f"if ($iface -and $iface.InterfaceAlias -eq '{escaped}') {{ "
        '$device = Get-NetAdapter -IncludeHidden -InterfaceIndex $iface.InterfaceIndex; '
        "if (-not (Test-VlaAdapter $device)) { throw 'The selected adapter is not owned by VLA.' }; "
        f'$iface | Set-NetIPInterface {setting} -ErrorAction Stop '
        + ('} else { Write-Output "Saved VLA adapter is no longer present." }' if missing_ok else
         "} else { throw 'The VLA adapter changed or is no longer present.' }"))


class AdapterSwitcher:
    def __init__(self, state_file="session_state.json", run=_ps):
        self.state_file, self.run = Path(state_file), run

    def _save(self, d):
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_file.with_name(self.state_file.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(d, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_file)
        finally:
            temporary.unlink(missing_ok=True)

    def _load(self):
        try:
            state = json.loads(self.state_file.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return {}
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError(f"Adapter recovery record is damaged: {self.state_file}. Previous network settings cannot be restored automatically.") from exc
        if not isinstance(state, dict) or (state.get('active') and (
                type(state.get('old_metric')) is not int or not 0 <= state['old_metric'] <= 9999
                or not isinstance(state.get('alias'), str) or not state['alias']
                or (state.get('interface_index') is not None and
                    (type(state['interface_index']) is not int or state['interface_index'] <= 0)))):
            raise RuntimeError(f"Adapter recovery record has invalid settings: {self.state_file}. No adapter was changed.")
        return state

    def prefer_zerotier(self, zt_alias: str):
        """Lower the matched ZeroTier adapter metric and save how to restore it."""
        return self._prefer_zerotier(zt_alias, None)

    def prefer_zerotier_for_ip(self, zt_alias: str, zt_ip: str):
        """Select the adapter carrying this room's assigned ZeroTier IP."""
        return self._prefer_zerotier(zt_alias, zt_ip)

    def _prefer_zerotier(self, zt_alias: str, zt_ip: str | None):
        alias = zt_alias.replace("'", "''")
        if zt_ip:
            ip = zt_ip.replace("'", "''")
            find_index = (f"$ip = Get-NetIPAddress -AddressFamily IPv4 -IPAddress '{ip}' "
                          "-ErrorAction SilentlyContinue | Select-Object -First 1; "
                          "if ($ip) { $ifIndex = $ip.InterfaceIndex }")
        else:
            find_index = ""
        command = _OWNERSHIP_PS + (
            f"{find_index} "
            f"if (-not $ifIndex) {{ $adapter = Get-NetAdapter -IncludeHidden | "
            f"Where-Object {{ $_.Name -eq '{alias}' }} | "
            "Select-Object -First 1; if ($adapter) { $ifIndex = $adapter.ifIndex } }; "
            "if (-not $ifIndex) { throw 'Windows could not find the VLA network adapter.' }; "
            "$vlaDevice = Get-NetAdapter -IncludeHidden -InterfaceIndex $ifIndex; "
            "if (-not (Test-VlaAdapter $vlaDevice)) { throw 'The selected adapter is not owned by VLA.' }; "
            "$iface = Get-NetIPInterface -InterfaceIndex $ifIndex -AddressFamily IPv4 | Select-Object -First 1; "
            "if (-not $iface) { throw 'Windows could not read the VLA IPv4 interface.' }; "
            "$iface | Select-Object InterfaceIndex,InterfaceAlias,InterfaceMetric,AutomaticMetric | ConvertTo-Json -Compress"
        )
        details = json.loads(self.run(command))
        index = int(details["InterfaceIndex"])
        old_metric = int(details["InterfaceMetric"])
        if old_metric == ZT_LOW_METRIC:
            return
        # Set-NetIPInterface disables automatic metrics. Restore its original mode,
        # rather than turning an automatically selected number into a fixed metric.
        if details.get('AutomaticMetric') in ('Enabled', 1):
            old_metric = 0
        self._save({"alias": details["InterfaceAlias"], "interface_index": index,
                    "old_metric": old_metric, "active": True})
        if self.run is _ps:
            from silent_tunnel.admin_actions import run_elevated
            run_elevated('adapter_metric', interface_index=index,
                         alias=details['InterfaceAlias'], metric=ZT_LOW_METRIC)
        else:
            self.run(f"Set-NetIPInterface -InterfaceIndex {index} -InterfaceMetric {ZT_LOW_METRIC}")

    def restore(self):
        st = self._load()
        if st.get("active"):
            m = st["old_metric"]
            if self.run is _ps:
                # Revalidate identity in the elevated child. Never elevate the UI
                # or accept a PowerShell command in the administrative payload.
                from silent_tunnel.admin_actions import run_elevated
                run_elevated('adapter_metric', interface_index=st.get('interface_index'),
                             alias=st.get('alias'), metric=m, missing_ok=True)
                st['active'] = False
                self._save(st)
                return
            metric = ("-AutomaticMetric Enabled" if m == 0 else f"-InterfaceMetric {int(m)}")
            if st.get("interface_index"):
                # Windows can remove an adapter or reuse its index after a
                # reinstall. Restore only the saved IPv4 interface identity.
                index = int(st['interface_index'])
                alias = str(st.get('alias', '')).replace("'", "''")
                self.run(
                    _OWNERSHIP_PS + f"$iface = Get-NetIPInterface -InterfaceIndex {index} -AddressFamily IPv4 "
                    "-ErrorAction SilentlyContinue | Select-Object -First 1; "
                    f"if ($iface -and $iface.InterfaceAlias -eq '{alias}') {{ "
                    "$device = Get-NetAdapter -IncludeHidden -InterfaceIndex $iface.InterfaceIndex; "
                    f"if (Test-VlaAdapter $device) {{ $iface | Set-NetIPInterface {metric} -ErrorAction Stop }} "
                    "} else { Write-Output 'Saved network adapter is no longer present.' }"
                )
            else:
                # Older releases persisted ZeroTier's internal device name as
                # InterfaceAlias. Resolve it to a Windows interface before restoring.
                alias = str(st.get("alias", "")).replace("'", "''")
                command = (
                    _OWNERSHIP_PS + f"$saved = '{alias}'; "
                    "$iface = Get-NetIPInterface -InterfaceAlias $saved -AddressFamily IPv4 "
                    "-ErrorAction SilentlyContinue | Select-Object -First 1; "
                    "if (-not $iface) { $adapter = Get-NetAdapter -IncludeHidden | "
                    "Where-Object { $_.Name -eq $saved } "
                    "| Select-Object -First 1; if ($adapter) { $iface = Get-NetIPInterface "
                    "-InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue "
                    "| Select-Object -First 1 } }; "
                    "if ($iface) { $device = Get-NetAdapter -IncludeHidden -InterfaceIndex $iface.InterfaceIndex; "
                    f"if (Test-VlaAdapter $device) {{ Set-NetIPInterface -InterfaceIndex $iface.InterfaceIndex {metric} }} }} "
                    "else { Write-Output 'Saved network adapter is no longer present.' }"
                )
                self.run(command)
            st["active"] = False
            self._save(st)

    def recover_after_crash(self) -> bool:
        """Call on startup (F14). True if a stale session was cleaned up."""
        if self._load().get("active"):
            self.restore()
            return True
        return False
