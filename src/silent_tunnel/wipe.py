"""Explicit removal of LAN Launcher's isolated VLA backend only."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from . import profile_records


# Fixed operations only: no paths, device IDs or commands supplied by the caller.
SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$issues = [System.Collections.Generic.List[string]]::new()
$script:reboot = $false
function Step([string]$label, [scriptblock]$work) {
    try { & $work | Out-Null } catch { $issues.Add($label + ': ' + $_.Exception.Message) }
}
function Native([string]$file, [string[]]$arguments, [int[]]$allowed = @(0)) {
    $output = & $file @arguments 2>&1
    $code = $LASTEXITCODE
    if ($code -in @(1641,3010,1072)) { $script:reboot = $true }
    if ($code -notin $allowed) { throw "Exit $code - $output" }
}
function RemoveTree([string]$target, [string]$parent) {
    $full = [IO.Path]::GetFullPath($target).TrimEnd('\')
    $root = [IO.Path]::GetFullPath($parent).TrimEnd('\') + '\'
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe removal path.' }
    if (-not (Test-Path -LiteralPath $full)) { return }
    # Refuse junctions, including ancestors and children, before recursive removal.
    $cursor = Get-Item -LiteralPath $full -Force
    if (-not $cursor.PSIsContainer) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked path: $full" }
        $cursor = $cursor.Directory
    }
    while ($cursor) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked path: $full" }
        $cursor = $cursor.Parent
    }
    foreach ($child in Get-ChildItem -LiteralPath $full -Force -Recurse) {
        if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Linked child: $($child.FullName)" }
    }
    Remove-Item -LiteralPath $full -Force -Recurse
}
$system = Join-Path $env:SystemRoot 'System32'
$programRoot = if ($env:ProgramW6432) { $env:ProgramW6432 } else { $env:ProgramFiles }
$install = Join-Path $programRoot 'LAN Launcher\VLA'
$nativeExe = Join-Path $install 'vla-service.exe'
$data = Join-Path $env:ProgramData 'LAN Launcher\VLA'
$ledger = Join-Path $data 'vla-devices.d'
$netClass = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4D36E972-E325-11CE-BFC1-08002BE10318}'
function AdapterEntries {
    # Enumerate names without opening protected non-adapter keys such as
    # Properties. Errors reading an actual numbered adapter must still fail.
    $root = Get-Item -LiteralPath $netClass -ErrorAction Stop
    try { $names = $root.GetSubKeyNames() } finally { $root.Close() }
    foreach ($name in $names) {
        if ($name -cmatch '^[0-9]{4}$') {
            Get-ItemProperty -LiteralPath (Join-Path $netClass $name) -ErrorAction Stop
        }
    }
}
function OwnedDevices {
    if (-not (Test-Path -LiteralPath $netClass)) { return }
    foreach ($entry in AdapterEntries) {
        if ($entry.ComponentId -ine 'zttap300' -or
            $entry._VLATapIdentifier -cnotmatch '^[0-9a-f]{16}$' -or
            -not $entry.DeviceInstanceID) { continue }
        $record = Join-Path $ledger ($entry._VLATapIdentifier + '.instance')
        if (-not (Test-Path -LiteralPath $record -PathType Leaf)) {
            $issues.Add('An unrecorded VLA adapter requires manual review: ' + $entry.DeviceInstanceID)
            continue
        }
        $recorded = (Get-Content -LiteralPath $record -Raw).Trim()
        if ($recorded -ine $entry.DeviceInstanceID) {
            $issues.Add('VLA adapter ownership record does not match: ' + $entry.DeviceInstanceID)
            continue
        }
        $entry.DeviceInstanceID
    }
}
$registered = Get-CimInstance Win32_Service -Filter "Name='LanLauncherVLA'"
if ($registered -and $registered.PathName -notmatch ('^"?' + [regex]::Escape($nativeExe) + '("|\s|$)')) {
    throw 'VLA has an unexpected service executable. Nothing was removed.'
}
$sc = Join-Path $system 'sc.exe'
$pnp = Join-Path $system 'pnputil.exe'
Step 'Stop service' {
    $service = Get-Service -Name 'LanLauncherVLA' -ErrorAction SilentlyContinue
    if ($service) { Stop-Service -InputObject $service -Force; $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30)) }
}
Step 'Stop VLA process' {
    Get-Process -Name 'vla-service' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $nativeExe } | Stop-Process -Force
}
Step 'Remove virtual adapters' {
    foreach ($id in @(OwnedDevices)) {
        Native $pnp @('/remove-device', $id) @(0,3010)
    }
}
$markedAdapterRemains = $true
Step 'Check remaining VLA adapters' {
    $found = $false
    foreach ($entry in AdapterEntries) {
        if ($entry.ComponentId -ieq 'zttap300' -and $entry._VLATapIdentifier -cmatch '^[0-9a-f]{16}$') {
            $found = $true
            $script:reboot = $true
        }
    }
    $script:markedAdapterRemains = $found
    if ($script:markedAdapterRemains) { throw 'A VLA-marked adapter remains. Restart Windows and retry.' }
}
Step 'Remove VLA service' { Native $sc @('delete', 'LanLauncherVLA') @(0,1060,1072) }
Step 'Remove firewall rules' {
    $vlaRules = @(Get-NetFirewallRule | Where-Object {
        $_.Name -cmatch '^GamerUtility-[0-9a-f]{16}-(TCP|UDP)$' -or
        $_.Name -like 'LanLauncherVLA-*' -or $_.DisplayName -eq 'LAN Launcher VLA' -or
        $_.DisplayName -like 'lanlauncher-vla-icmp*'
    })
    $vlaRules += @(Get-NetFirewallApplicationFilter | Where-Object { $_.Program -eq $nativeExe } | Get-NetFirewallRule)
    $vlaRules | Sort-Object Name -Unique | Remove-NetFirewallRule
}
Step 'Remove VLA DNS policies' {
    $dnsRoot = 'HKLM:\SYSTEM\CurrentControlSet\Services\Dnscache\Parameters\DnsPolicyConfig'
    if (Test-Path -LiteralPath $dnsRoot) {
        foreach ($policy in Get-ChildItem -LiteralPath $dnsRoot) {
            $comment = (Get-ItemProperty -LiteralPath $policy.PSPath).Comment
            if ($comment -match '^LANLauncherVLA:[0-9a-fA-F]{16}$') {
                Remove-Item -LiteralPath $policy.PSPath -Recurse -Force
            }
        }
    }
}
$locations = @(
    @{Path=$data; Parent=$env:ProgramData},
    @{Path=$install; Parent=$programRoot}
)
$portableData = $env:LANLAUNCHER_PORTABLE_DATA
Step 'Find user state' {
    if ($portableData) {
        # Portable mode: only LanTool-data beside portable.flag; never any user profile.
        $full = [IO.Path]::GetFullPath($portableData).TrimEnd('\')
        $flag = Join-Path (Split-Path -Parent $full) 'portable.flag'
        if ((Split-Path -Leaf $full) -ne 'LanTool-data' -or -not (Test-Path -LiteralPath $flag -PathType Leaf)) {
            throw 'Portable data path is not a LanTool-data folder beside portable.flag. Nothing was removed there.'
        }
        foreach ($item in @('vla-state','vla-installed-by-lantool','vla-adapter-state.json')) {
            $script:locations += @{Path=(Join-Path $full $item); Parent=$full}
        }
        return
    }
    foreach ($ztProfile in Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\*') {
        $homePath = [Environment]::ExpandEnvironmentVariables($ztProfile.ProfileImagePath)
        if (-not $homePath -or -not [IO.Path]::IsPathRooted($homePath)) { continue }
        foreach ($relative in @('AppData\Local\LanTool\vla-state','AppData\Local\LanTool\vla-installed-by-lantool','AppData\Local\LanTool\vla-adapter-state.json')) {
            $script:locations += @{Path=(Join-Path $homePath $relative); Parent=$homePath}
        }
    }
}
foreach ($location in $locations) {
    # Keep ownership records for a retry if PnP could not remove a device.
    if ($markedAdapterRemains -and $location.Path -eq $data) { continue }
    Step "Remove $($location.Path)" { RemoveTree $location.Path $location.Parent }
}
Step 'Verify service removal' {
    if (Get-Service 'LanLauncherVLA' -ErrorAction SilentlyContinue) { $script:reboot = $true; throw 'The VLA service is still registered. Restart Windows and retry.' }
}
@{issues=@($issues); reboot=$script:reboot} | ConvertTo-Json -Compress
'''


ENV_PORTABLE = 'LANLAUNCHER_PORTABLE_DATA'


def wipe_environment(root=None, base_env=None) -> dict:
    """Environment for the fixed script. The portable data folder is derived here from portable.flag
    (never caller-supplied) and passed by environment variable, so no path is spliced into the script."""
    from lantool import appdirs   # lazy, like store.py: silent_tunnel stays importable on its own
    env = dict(os.environ if base_env is None else base_env)
    env.pop(ENV_PORTABLE, None)          # an inherited value must never steer the wipe
    targets = appdirs.wipe_targets(root)
    if targets['portable']:
        env[ENV_PORTABLE] = targets['data_dir']
    return env


def wipe_windows(root=None) -> dict:
    """Run only after explicit UI confirmation and elevation."""
    if os.name != 'nt':
        raise RuntimeError('VLA removal requires Windows.')
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    # Hold the same transaction lock as Prepare through deletion. A concurrent
    # preparation must not recreate a profile journal just before it is erased.
    with profile_records.transaction():
        profile_records._restore_unlocked()
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', SCRIPT],
                                capture_output=True, text=True, timeout=900, env=wipe_environment(root),
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('VLA removal failed: ' + result.stderr[-1500:])
    report = json.loads(result.stdout.strip())
    return {'report': report}
