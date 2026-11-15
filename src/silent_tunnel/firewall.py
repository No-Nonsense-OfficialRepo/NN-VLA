"""Narrow launcher firewall rules, keyed to the actual executable location."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys


def rule_names(program=None):
    """Rule names keyed to the launcher exe (default: this process; the helper passes the caller's)."""
    program = program or sys.executable
    key = hashlib.sha256(str(Path(program).resolve()).casefold().encode()).hexdigest()[:16]
    return [f'GamerUtility-{key}-TCP', f'GamerUtility-{key}-UDP']


_OUR_RULE = re.compile(r'^GamerUtility-[0-9a-f]{16}-(TCP|UDP)$')


def list_our_rules():
    """Read-only (no admin): our inbound rules as [{'name','program','enabled','direction','action'}].
    None when they can't be read (not Windows, PowerShell failed)."""
    if os.name != 'nt':
        return None
    command = ("$r = @(Get-NetFirewallRule -Name 'GamerUtility-*' -ErrorAction SilentlyContinue | "
               "ForEach-Object { $f = $_ | Get-NetFirewallApplicationFilter; "
               "[pscustomobject]@{name=[string]$_.Name; program=[string]$f.Program; "
               "enabled=($_.Enabled -eq 'True'); direction=[string]$_.Direction; action=[string]$_.Action} }); "
               "ConvertTo-Json -InputObject $r -Compress")
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                capture_output=True, text=True, timeout=20,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not isinstance(result.stdout, str) or not result.stdout.strip():
            return None
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    rows = data if isinstance(data, list) else [data]
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get('name'), str)]


def stale_rule_names(program=None, rules=None):
    """Names of OUR rules that point somewhere other than `program` (moved/renamed/old release).
    Names come from the rules found on this PC, must match our exact pattern, and never include the
    current exe's own names. Never takes names from a caller."""
    from lantool.diagnostics import path_drift
    program = program or sys.executable
    rules = list_our_rules() if rules is None else rules
    current = set(rule_names(program))
    names = []
    for rule in path_drift.stale_rules(rules or [], program):
        name = rule.get('name')
        if isinstance(name, str) and _OUR_RULE.match(name) and name not in current and name not in names:
            names.append(name)
    return names


def configure_rules():
    from .admin_actions import run_elevated
    return run_elevated('launcher_firewall')


def ensure_rules():
    if os.name != 'nt':
        return
    names = ','.join("'" + name + "'" for name in rule_names())
    command = (f"@(Get-NetFirewallRule -Name {names} -ErrorAction SilentlyContinue | "
               "Where-Object {$_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' "
               "-and $_.Action -eq 'Allow' -and ($_.Name.EndsWith('-UDP') "
               "-or ((($_ | Get-NetFirewallPortFilter).LocalPort -contains '47807') "
               "-and (($_ | Get-NetFirewallPortFilter).LocalPort -contains '47808')))}).Count")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                            capture_output=True, text=True, timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or result.stdout.strip() != '2' or stale_rule_names():
        configure_rules()


def apply_rules(program=None):
    """Called only in the elevated child. `program` is validated by admin_actions to be an
    .exe beside the helper; it is never a value supplied by a room peer."""
    program_path = program or sys.executable
    program = str(Path(program_path).resolve()).replace("'", "''")
    commands = ["$ErrorActionPreference='Stop'"]
    removed = stale_rule_names(program_path)         # idempotent: drop our rules for other paths first
    for name in removed:
        commands.append(f"Get-NetFirewallRule -Name '{name}' -ErrorAction SilentlyContinue | Remove-NetFirewallRule")
    for name, protocol, ports in zip(rule_names(program_path), ['TCP', 'UDP'],
                                    ['47800,47802,47803,47805,47807,47808', '47801,47804,47806']):
        commands.append(f"Get-NetFirewallRule -Name '{name}' -ErrorAction SilentlyContinue | Remove-NetFirewallRule")
        commands.append(f"New-NetFirewallRule -Name '{name}' -DisplayName 'NN LAN Launcher {protocol}' "
                        f"-Direction Inbound -Action Allow -Program '{program}' -Protocol {protocol} "
                        f"-LocalPort {ports} -RemoteAddress LocalSubnet,10.147.0.0/16 -Profile Any | Out-Null")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', '; '.join(commands)],
                            capture_output=True, text=True, timeout=60,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Windows could not configure the launcher firewall rules.')
    return removed
