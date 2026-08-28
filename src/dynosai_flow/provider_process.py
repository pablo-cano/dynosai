# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Pablo Cano
"""Spawn and stop provider subprocesses, including process trees."""

from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import time
from typing import Any, Callable

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
TH32CS_SNAPPROCESS = 0x00000002
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
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


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32),
        ("cntUsage", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_uint32),
        ("cntThreads", ctypes.c_uint32),
        ("th32ParentProcessID", ctypes.c_uint32),
        ("pcPriClassBase", ctypes.c_int32),
        ("dwFlags", ctypes.c_uint32),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _kernel32():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = ctypes.c_void_p
    k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    k32.SetInformationJobObject.restype = ctypes.c_int
    k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    k32.AssignProcessToJobObject.restype = ctypes.c_int
    k32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    k32.TerminateJobObject.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.restype = ctypes.c_int
    k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32FirstW.restype = ctypes.c_int
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    k32.Process32NextW.restype = ctypes.c_int
    return k32


def _attach_windows_job(proc: subprocess.Popen) -> Any:
    handle = getattr(proc, "_handle", None)
    if not handle:
        return None
    k32 = _kernel32()
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    if not k32.AssignProcessToJobObject(job, handle):
        k32.CloseHandle(job)
        return None
    return job


def _close_windows_job(job: Any, *, terminate: bool = False) -> None:
    if not job:
        return
    k32 = _kernel32()
    if terminate:
        k32.TerminateJobObject(job, 1)
    k32.CloseHandle(job)


def _windows_descendants(root_pid: int) -> list[int]:
    k32 = _kernel32()
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == INVALID_HANDLE_VALUE:
        return []
    children: dict[int, list[int]] = {}
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
    try:
        more = k32.Process32FirstW(snap, ctypes.byref(entry))
        while more:
            children.setdefault(int(entry.th32ParentProcessID), []).append(int(entry.th32ProcessID))
            more = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    found: list[int] = []
    stack = list(children.get(root_pid, []))
    while stack:
        pid = stack.pop()
        if pid in found or pid == root_pid:
            continue
        found.append(pid)
        stack.extend(children.get(pid, []))
    return found


def spawn_provider_process(command: list[str], **kwargs: Any) -> subprocess.Popen:
    """Start a provider process so its descendants can be stopped as a tree.

    On Windows, ``Popen.terminate()`` only ends the direct child. Cursor ACP
    workers therefore keep ``acp-sessions`` files open unless the whole tree is
    collected. A kill-on-close job plus a descendant snapshot cover both nested
    jobs and parent-exit orphans. On POSIX the process is started in a new
    session so ``killpg`` can reach descendants.
    """
    kwargs = dict(kwargs)
    if os.name == "nt":
        flags = int(kwargs.get("creationflags") or 0)
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            flags |= subprocess.CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        try:
            proc = subprocess.Popen(command, **kwargs)
        except OSError:
            kwargs["creationflags"] = flags & ~CREATE_BREAKAWAY_FROM_JOB
            proc = subprocess.Popen(command, **kwargs)
        proc._dynosai_job = _attach_windows_job(proc)  # type: ignore[attr-defined]
        return proc
    kwargs.setdefault("start_new_session", True)
    proc = subprocess.Popen(command, **kwargs)
    proc._dynosai_job = None  # type: ignore[attr-defined]
    try:
        proc._dynosai_pgid = os.getpgid(proc.pid)  # type: ignore[attr-defined]
    except OSError:
        proc._dynosai_pgid = proc.pid  # type: ignore[attr-defined]
    return proc


def _wait_exit(proc: subprocess.Popen, timeout: float) -> bool:
    if proc.poll() is not None:
        return True
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return proc.poll() is not None
    return proc.poll() is not None


def _close_pipe(stream: Any) -> None:
    if stream is None:
        return
    try:
        stream.close()
    except Exception:
        pass


def _posix_group_is_running(pgid: int) -> bool:
    if pgid <= 0:
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _posix_signal_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        try:
            os.kill(pgid, sig)
        except OSError:
            pass


def _wait_posix_group_exit(proc: subprocess.Popen, pgid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        proc.poll()
        if not _posix_group_is_running(pgid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _windows_taskkill(pid: int, *, force: bool) -> None:
    cmd = ["taskkill", "/PID", str(pid), "/T"]
    if force:
        cmd.append("/F")
    subprocess.run(cmd, capture_output=True, check=False)


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        return str(pid) in (listed.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _kill_pid(pid: int) -> None:
    if pid <= 0 or pid == os.getpid():
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def _reap_known_children(child_pids: list[int]) -> None:
    for pid in child_pids:
        if pid_is_running(pid):
            _kill_pid(pid)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and any(pid_is_running(pid) for pid in child_pids):
        time.sleep(0.05)


def stop_provider_process(
    proc: subprocess.Popen,
    *,
    notify: Callable[[], None] | None = None,
    stdin_grace: float = 8.0,
    term_grace: float = 3.0,
    kill_grace: float = 2.0,
) -> None:
    """Shut down a provider stdio process and wait until it has exited.

    ACP/app-server agents treat stdin EOF as the graceful close signal. Windows
    Windows descendants are snapshotted *before* the parent can exit, because a
    child that outlives ``agent acp`` keeps ``acp-sessions`` locked with
    WinError 32. On POSIX the process group is stopped even after the direct
    child exits cleanly, because its descendants can keep the group alive.
    """
    job = getattr(proc, "_dynosai_job", None)
    pgid = getattr(proc, "_dynosai_pgid", None)
    descendants: list[int] = []
    if proc.poll() is None and os.name == "nt":
        descendants = _windows_descendants(proc.pid)
    if notify is not None and proc.poll() is None:
        try:
            notify()
        except Exception:
            pass
    _close_pipe(proc.stdin)
    _wait_exit(proc, stdin_grace)
    if os.name == "nt":
        if proc.poll() is None:
            _windows_taskkill(proc.pid, force=False)
            try:
                proc.terminate()
            except OSError:
                pass
            _wait_exit(proc, term_grace)
        if proc.poll() is None:
            _windows_taskkill(proc.pid, force=True)
            try:
                proc.kill()
            except OSError:
                pass
            _wait_exit(proc, kill_grace)
    else:
        group_id = pgid or proc.pid
        if _posix_group_is_running(group_id):
            _posix_signal_group(group_id, signal.SIGTERM)
            _wait_posix_group_exit(proc, group_id, term_grace)
        if _posix_group_is_running(group_id):
            _posix_signal_group(group_id, signal.SIGKILL)
            _wait_posix_group_exit(proc, group_id, kill_grace)
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
            _wait_exit(proc, kill_grace)
    _reap_known_children(descendants)
    _close_windows_job(job, terminate=proc.poll() is None)
    proc._dynosai_job = None  # type: ignore[attr-defined]
    _close_pipe(proc.stdout)
    _close_pipe(getattr(proc, "stderr", None))
    deadline = time.monotonic() + 1.0
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
