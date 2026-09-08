"""Windows CLI-turn ownership, not a security sandbox.

The provider starts suspended and enters a private kill-on-close Job before
executing. CPython closes the primary thread handle in Popen, so we acquire it
from a thread snapshot while it is still suspended, verify its process identity,
and resume using that retained handle. See docs/windows-process-ownership.md.
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid

_JOB_ENV = "ARGUS_WINDOWS_TURN_JOB"
_CREATE_SUSPENDED = 0x00000004
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_JOB_OBJECT_QUERY = 0x0004


def _kernel():
    import ctypes
    from ctypes import wintypes as w

    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([w.LPVOID, w.LPCWSTR], w.HANDLE),
        "OpenJobObjectW": ([w.DWORD, w.BOOL, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD], w.BOOL),
        "QueryInformationJobObject": (
            [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, w.LPVOID], w.BOOL,
        ),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
        "IsProcessInJob": ([w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)], w.BOOL),
        "GetCurrentProcess": ([], w.HANDLE),
        "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
        "Thread32First": ([w.HANDLE, w.LPVOID], w.BOOL),
        "Thread32Next": ([w.HANDLE, w.LPVOID], w.BOOL),
        "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "GetProcessIdOfThread": ([w.HANDLE], w.DWORD),
        "ResumeThread": ([w.HANDLE], w.DWORD),
        "CloseHandle": ([w.HANDLE], w.BOOL),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = args
        function.restype = result
    return api


class _WindowsTurnJob:
    def __init__(self):
        import ctypes
        from ctypes import wintypes as w

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", w.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", w.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", w.DWORD),
                ("SchedulingClass", w.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimits),
                ("IoInfo", ctypes.c_ulonglong * 6),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        self.api = _kernel()
        self.name = "Local\\ArgusTurn-" + uuid.uuid4().hex
        # NULL security attributes make this handle non-inheritable.
        self.handle = self.api.CreateJobObjectW(None, self.name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        # BREAKAWAY_OK is explicit: ordinary child processes remain owned.
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x0800
        if not self.api.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        import ctypes

        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def resume(self, process):
        import ctypes
        from ctypes import wintypes as w

        class ThreadEntry(ctypes.Structure):
            _fields_ = [
                ("dwSize", w.DWORD), ("cntUsage", w.DWORD),
                ("th32ThreadID", w.DWORD), ("th32OwnerProcessID", w.DWORD),
                ("tpBasePri", w.LONG), ("tpDeltaPri", w.LONG), ("dwFlags", w.DWORD),
            ]

        snapshot = self.api.CreateToolhelp32Snapshot(0x00000004, 0)
        if snapshot == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        thread = None
        try:
            entry = ThreadEntry()
            entry.dwSize = ctypes.sizeof(entry)
            found = self.api.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.th32OwnerProcessID == process.pid:
                    thread = self.api.OpenThread(0x0002 | 0x0800, False, entry.th32ThreadID)
                    if not thread:
                        raise ctypes.WinError(ctypes.get_last_error())
                    if self.api.GetProcessIdOfThread(thread) != process.pid:
                        raise OSError("Suspended provider thread ownership changed")
                    # The primary thread has not executed; exactly one suspend
                    # count must be removed. Unexpected state fails closed.
                    if self.api.ResumeThread(thread) != 1:
                        raise OSError("Unable to resume the suspended provider thread")
                    return
                entry.dwSize = ctypes.sizeof(entry)
                found = self.api.Thread32Next(snapshot, ctypes.byref(entry))
            raise OSError("Suspended provider primary thread was not found")
        finally:
            if thread:
                self.api.CloseHandle(thread)
            self.api.CloseHandle(snapshot)

    def active_processes(self) -> int:
        import ctypes
        from ctypes import wintypes as w

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("times", ctypes.c_longlong * 4),
                ("TotalPageFaultCount", w.DWORD),
                ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD),
                ("TotalTerminatedProcesses", w.DWORD),
            ]

        if not self.handle:
            return 0
        accounting = Accounting()
        if not self.api.QueryInformationJobObject(
            self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return accounting.ActiveProcesses

    def terminate(self) -> bool:
        if not self.handle:
            return True
        try:
            self.api.TerminateJobObject(self.handle, 1)
            deadline = time.monotonic() + 5
            while self.active_processes():
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.02)
            return True
        finally:
            self.close()

    def close(self):
        handle, self.handle = getattr(self, "handle", None), None
        if handle:
            self.api.CloseHandle(handle)

    def __del__(self):
        self.close()


def spawn_owned_process(command, *, popen_factory=subprocess.Popen, **kwargs):
    """Spawn a turn; no descendant may execute before Windows ownership exists."""
    if os.name != "nt":
        return popen_factory(command, **kwargs)
    job = _WindowsTurnJob()
    process = None
    try:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _CREATE_SUSPENDED
        kwargs["env"] = {**(kwargs.get("env") or os.environ), _JOB_ENV: job.name}
        process = popen_factory(command, **kwargs)
        # Retaining the Popen process handle prevents PID reuse during resume.
        process._argus_windows_job = job
        job.assign(process)
        job.resume(process)
        return process
    except BaseException:
        # Assignment may have failed. Terminate the held process separately;
        # no provider code has run, so an unassigned child cannot have children.
        if process is not None:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
            finally:
                job.close()
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
        else:
            job.close()
        raise


def terminate_owned_process(process) -> bool | None:
    """Terminate via retained Job identity; None means this is not an owned turn."""
    job = getattr(process, "_argus_windows_job", None)
    if job is None:
        return None
    result = job.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        return False
    return result


def durable_windows_creationflags() -> int:
    """Allow only an explicit durable launcher to leave its current turn Job.

    An inherited name alone is insufficient: check actual membership before
    requesting breakaway. An outer host Job can refuse breakaway; the launcher
    then fails visibly rather than silently creating a falsely durable child.
    """
    if os.name != "nt" or not (name := os.environ.get(_JOB_ENV, "")):
        return 0
    import ctypes
    from ctypes import wintypes as w

    api = _kernel()
    handle = api.OpenJobObjectW(_JOB_OBJECT_QUERY, False, name)
    if not handle:
        if ctypes.get_last_error() == 2:
            return 0  # A former turn ended after this worker already detached.
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        member = w.BOOL()
        if not api.IsProcessInJob(api.GetCurrentProcess(), handle, ctypes.byref(member)):
            raise ctypes.WinError(ctypes.get_last_error())
        return _CREATE_BREAKAWAY_FROM_JOB if member.value else 0
    finally:
        api.CloseHandle(handle)
