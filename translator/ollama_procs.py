"""Ending Ollama processes without leaving their model runners behind.

On Windows, killing ``ollama.exe serve`` (terminate(), or taskkill without
/T) leaves its per-model runner (``llama-server.exe``) running with a dead
parent. Each restart orphaned one; a dozen held ~18 GB RAM and almost all
VRAM. This module kills the whole tree and sweeps up such orphans.

Safety rules for the sweep (Windows only):
- only a runner whose executable lies inside an Ollama install's
  ``lib\\ollama`` folder (real paths compared) -- a separate llama.cpp
  install is never touched;
- only when its parent process no longer exists -- the runners of a live
  Ollama (ours or anyone's) are never touched;
- any error while listing processes means nothing is killed.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import NamedTuple

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Ollama's per-model runner (current builds) and the older runner name.
RUNNER_NAMES = frozenset({"llama-server.exe", "ollama_llama_server.exe"})


class Proc(NamedTuple):
    pid: int
    ppid: int
    name: str
    exe: str | None  # full executable path; None when it can't be read


def is_windows() -> bool:
    return os.name == "nt"


def kill_tree(pid: int) -> bool:
    """End a process and all its children (Windows taskkill /T).

    Returns True when taskkill reports success. False (also off Windows)
    tells the caller to fall back to terminate()/kill().
    """
    if not is_windows():
        return False
    try:
        r = subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(int(pid))],
            capture_output=True, timeout=10, creationflags=_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        log.warning("taskkill /T for PID %s failed: %s", pid, e)
        return False
    if r.returncode != 0:
        log.warning("taskkill /T for PID %s returned %s", pid, r.returncode)
        return False
    return True


def ollama_runner_dirs() -> list[str]:
    """The ``lib\\ollama`` folders of the Ollama installs we can find."""
    bases = []
    exe = shutil.which("ollama")
    if exe:
        bases.append(os.path.dirname(exe))
    bases += [
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama"),
        os.path.expandvars(r"%LOCALAPPDATA%\Ollama"),
        r"C:\Program Files\Ollama",
    ]
    dirs: list[str] = []
    for base in bases:
        d = os.path.join(base, "lib", "ollama")
        if os.path.isdir(d) and d not in dirs:
            dirs.append(d)
    return dirs


def _real(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _is_inside(path: str, root: str) -> bool:
    p, r = _real(path), _real(root)
    try:
        return os.path.commonpath([p, r]) == r
    except ValueError:  # different drives: certainly not inside
        return False


def find_orphan_runners(procs: list[Proc], dirs: list[str]) -> list[Proc]:
    """Ollama runners (by real path) whose parent process is gone."""
    live = {p.pid for p in procs}
    out = []
    for p in procs:
        if (p.name or "").lower() not in RUNNER_NAMES or not p.exe:
            continue
        if not any(_is_inside(p.exe, d) for d in dirs):
            continue
        if p.ppid in live:
            continue  # parent alive (or its PID reused): leave it alone
        out.append(p)
    return out


def _snapshot_processes() -> list[Proc]:
    """Every running process with its parent PID and executable path.

    Uses the Toolhelp snapshot (no child process, fast). Raises on failure.
    """
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32FirstW.restype = wintypes.BOOL
    k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD)]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    snap_procs = 0x2
    query_limited = 0x1000
    invalid = wintypes.HANDLE(-1).value

    def exe_path(pid: int) -> str | None:
        h = k32.OpenProcess(query_limited, False, pid)
        if not h:
            return None  # access denied / already gone: path unknown
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return None
        finally:
            k32.CloseHandle(h)

    snap = k32.CreateToolhelp32Snapshot(snap_procs, 0)
    if not snap or snap == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(entry)):
            raise ctypes.WinError(ctypes.get_last_error())
        procs = []
        while True:
            pid = int(entry.th32ProcessID)
            procs.append(Proc(pid, int(entry.th32ParentProcessID),
                              entry.szExeFile, exe_path(pid) if pid else None))
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                break
        return procs
    finally:
        k32.CloseHandle(snap)


def list_processes() -> list[Proc] | None:
    """All processes, or None if they can't be listed (caller does nothing)."""
    try:
        return _snapshot_processes()
    except Exception as e:  # noqa: BLE001 -- any failure means "unknown": kill nothing
        log.warning("Could not list processes; skipping Ollama runner cleanup: %s", e)
        return None


# Kept for tests: the autouse fixture replaces list_processes.
_real_list_processes = list_processes


def kill_orphan_runners() -> list[int]:
    """Kill Ollama runners left behind by a dead ``ollama serve``.

    Windows only. Returns the PIDs that taskkill ended. Never raises.
    """
    if not is_windows():
        return []
    dirs = ollama_runner_dirs()
    if not dirs:
        return []
    procs = list_processes()
    if not procs:
        return []
    killed = []
    for p in find_orphan_runners(procs, dirs):
        log.warning("Ending orphaned Ollama runner %s (PID %d, parent %d gone)",
                    p.exe, p.pid, p.ppid)
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/PID", str(p.pid)],
                capture_output=True, timeout=10, creationflags=_NO_WINDOW,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            log.warning("Could not end orphaned runner PID %d: %s", p.pid, e)
            continue
        if r.returncode == 0:
            killed.append(p.pid)
        else:
            log.warning("taskkill for orphaned runner PID %d returned %s",
                        p.pid, r.returncode)
    return killed
