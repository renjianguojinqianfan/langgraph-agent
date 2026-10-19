"""Windows Job Object wrapper for bash process trees (S1 / #148 F3).

Only imported on Windows. A job keeps track of every descendant of the shell,
so the tree can be terminated even after the shell itself has exited. Pure
``ctypes``: no new dependency.

``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` is set, so a backend that exits without
stopping the tree still reaps it when the handle closes.
"""

from __future__ import annotations

import ctypes
import subprocess
from typing import Any

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x0000_2000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1


def _last_error() -> int:
    getter: Any = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter is not None else 0


def _dll(name: str) -> Any:
    loader: Any = getattr(ctypes, "WinDLL")
    return loader(name, use_last_error=True)


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


class WindowsJob:
    """A job holding one bash shell and every process it spawns."""

    def __init__(self) -> None:
        self._k32 = _dll("kernel32")
        self._ntdll = _dll("ntdll")
        k32 = self._k32
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        k32.SetInformationJobObject.restype = ctypes.c_int
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.AssignProcessToJobObject.restype = ctypes.c_int
        k32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.TerminateJobObject.restype = ctypes.c_int
        k32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        k32.QueryInformationJobObject.restype = ctypes.c_int
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = ctypes.c_int
        self._ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]
        self._ntdll.NtResumeProcess.restype = ctypes.c_long

        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(_last_error(), "CreateJobObjectW failed")
        self._handle: int | None = int(handle)
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(
            self._handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            error = _last_error()
            self.close()
            raise OSError(error, "SetInformationJobObject failed")

    @staticmethod
    def _process_handle(proc: subprocess.Popen[Any]) -> int:
        handle = getattr(proc, "_handle", None)
        if handle is None:
            raise OSError("process handle unavailable")
        return int(handle)

    def assign(self, proc: subprocess.Popen[Any]) -> None:
        if self._handle is None:
            raise OSError("job is closed")
        if not self._k32.AssignProcessToJobObject(
            self._handle, ctypes.c_void_p(self._process_handle(proc))
        ):
            raise OSError(_last_error(), "AssignProcessToJobObject failed")

    def resume(self, proc: subprocess.Popen[Any]) -> None:
        status = int(self._ntdll.NtResumeProcess(ctypes.c_void_p(self._process_handle(proc))))
        if status != 0:
            raise OSError(f"NtResumeProcess failed: 0x{status & 0xFFFFFFFF:08x}")

    def active_processes(self) -> int:
        if self._handle is None:
            return 0
        info = _BasicAccountingInformation()
        returned = ctypes.c_uint32()
        if not self._k32.QueryInformationJobObject(
            self._handle,
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(returned),
        ):
            return 1  # unknown -> keep the handle rather than dropping a live tree
        return int(info.ActiveProcesses)

    def terminate(self) -> None:
        if self._handle is not None:
            self._k32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle is not None:
            self._k32.CloseHandle(self._handle)
            self._handle = None
