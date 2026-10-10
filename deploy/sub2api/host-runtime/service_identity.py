"""Read-only process birth and local listener ownership (Windows native APIs)."""
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
import socket
import struct
import base64
import os
import subprocess


def process_birth(pid):
    kernel = c.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.GetProcessTimes.argtypes = [w.HANDLE] + [c.POINTER(w.FILETIME)] * 4
    kernel.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        code = w.DWORD()
        if not kernel.GetExitCodeProcess(handle, c.byref(code)) or code.value != 259:
            return None
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[c.byref(t) for t in times]):
            return None
        return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def listener_pid(port):
    api = c.WinDLL('iphlpapi').GetExtendedTcpTable
    api.argtypes = [c.c_void_p, c.POINTER(w.DWORD), w.BOOL, w.ULONG, c.c_int, w.ULONG]
    size = w.DWORD()
    api(None, c.byref(size), False, socket.AF_INET, 3, 0)
    for _ in range(3):
        buf = c.create_string_buffer(size.value)
        result = api(buf, c.byref(size), False, socket.AF_INET, 3, 0)
        if result == 122:
            continue
        if result:
            return None
        count = struct.unpack_from('<I', buf.raw)[0]
        for i in range(count):
            state, addr, local_port, _, _, pid = struct.unpack_from('<6I', buf.raw, 4 + i * 24)
            if socket.inet_ntoa(struct.pack('<I', addr)) == '127.0.0.1' and socket.ntohs(local_port & 0xffff) == port:
                return pid
        return None
    return None


def cim_worker_matches(role, receipt):
    """Least-privilege fallback when another service's process handle is denied.

    CIM exposes process creation time at microsecond precision and parent PIDs;
    require both the receipt birth and the current SCM ancestry. No privilege
    changes, arbitrary process command lines, or persistent identity cache.
    """
    name = {'tunnel-primary': 'RealYuTunnelPrimary', 'tunnel-replica': 'RealYuTunnelReplica'}[role]
    pid, launcher = int(receipt['pid']), int(receipt['launcher_pid'])
    if not 0 < pid < 2**32 or not 0 < launcher < 2**32:
        return False
    script = f'''$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$p=Get-CimInstance Win32_Process -Filter "ProcessId={pid}"
$l=Get-CimInstance Win32_Process -Filter "ProcessId={launcher}"
$s=Get-CimInstance Win32_Service -Filter "Name='{name}'"
if (!$p -or !$l -or !$s -or !$p.CreationDate) {{exit 1}}
@{{birth=[string]$p.CreationDate.ToUniversalTime().ToFileTimeUtc();chain=($p.ParentProcessId -eq {launcher} -and $l.ParentProcessId -eq $s.ProcessId -and $s.State -eq 'Running')}} | ConvertTo-Json -Compress
'''
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    exe = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    result = subprocess.run([str(exe), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=2.5, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode != 0:
        return False
    identity = json.loads(result.stdout.decode('utf-8-sig'))
    return identity.get('chain') is True and int(identity['birth']) // 10 == int(receipt['birth']) // 10


def managed_tunnel_identity(role, base=Path(r'C:\ProgramData\RealYuServices')):
    port = {'tunnel-primary': 20242, 'tunnel-replica': 18432}[role]
    try:
        receipt = json.loads((base / 'logs' / role / 'worker.json').read_text('utf-8'))
        pid = receipt['pid']
        if not receipt.get('birth') or listener_pid(port) != pid:
            return False
        birth = process_birth(pid)
        if birth is not None:
            return birth == receipt['birth']
        return cim_worker_matches(role, receipt) and listener_pid(port) == pid
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return False
