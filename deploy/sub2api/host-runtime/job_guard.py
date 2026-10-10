"""Native job lifetime guard; no polling/restart loop.

Closing the launcher destroys its worker job. Waiting on the SCM wrapper handle
also closes that job if WinSW itself is terminated unexpectedly.
"""
import ctypes as c
from ctypes import wintypes as w
import os

kernel = c.WinDLL('kernel32', use_last_error=True)
kernel.CreateJobObjectW.argtypes = [c.c_void_p, w.LPCWSTR]
kernel.CreateJobObjectW.restype = w.HANDLE
kernel.GetCurrentProcess.restype = w.HANDLE
kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel.OpenProcess.restype = w.HANDLE
kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
kernel.WaitForMultipleObjects.argtypes = [w.DWORD, c.POINTER(w.HANDLE), w.BOOL, w.DWORD]
kernel.WaitForMultipleObjects.restype = w.DWORD


class Basic(c.Structure):
    _fields_ = [('ProcessTime', c.c_int64), ('JobTime', c.c_int64), ('Flags', w.DWORD),
                ('MinWorkingSet', c.c_size_t), ('MaxWorkingSet', c.c_size_t), ('ActiveProcesses', w.DWORD),
                ('Affinity', c.c_size_t), ('Priority', w.DWORD), ('Scheduling', w.DWORD)]


class Extended(c.Structure):
    _fields_ = [('Basic', Basic), ('Io', c.c_uint64 * 6), ('ProcessMemory', c.c_size_t),
                ('JobMemory', c.c_size_t), ('PeakProcessMemory', c.c_size_t), ('PeakJobMemory', c.c_size_t)]


def guard():
    parent = kernel.OpenProcess(0x100000, False, os.getppid())
    job = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not parent or not job or not kernel.SetInformationJobObject(job, 9, c.byref(info), c.sizeof(info)) or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess()):
        raise OSError(c.get_last_error(), 'Unable to establish service worker lifetime guard')
    return job, parent  # Keep non-inheritable handles alive for the launcher lifetime.


def wait(child, handles):
    job, parent = handles
    result = kernel.WaitForMultipleObjects(2, (w.HANDLE * 2)(int(child._handle), parent), False, 0xffffffff)
    if result == 0:
        return child.wait()
    if result == 1:
        return 254  # Wrapper disappeared; process exit closes the worker job.
    raise OSError(c.get_last_error(), 'Service wait failed')
