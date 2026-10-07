"""Ask another Jarvis process to quit cleanly, or end it with everything it started.

POSIX has ``SIGTERM`` for "please quit" and ``SIGKILL`` on a process group for
"stop now". Windows has neither: ``os.kill`` there is ``TerminateProcess`` (no
``atexit``, no cleanup — the web registry entry, MCP servers and background
jobs are left behind), and a Ctrl+Break only reaches processes that share a
console, which a Jarvis opened from the web remote does not.

So on Windows a process that wants to be stoppable creates a named event,
``Local\\jarvis-stop-<pid>``, and waits on it (``listen_for_stop``);
``request_stop`` sets it. ``kill_tree`` is the forced path (``taskkill /T /F``).
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from typing import Callable

from .osinfo import IS_WINDOWS, hidden_subprocess_kwargs

EVENT_MODIFY_STATE = 0x0002  # OpenEvent right needed to SetEvent
INFINITE = 0xFFFFFFFF  # WaitForSingleObject: no timeout

# Keeps each listener's event handle open (a closed handle would delete the event).
_listening: list[object] = []


def event_name(pid: int) -> str:
    """The Windows event a Jarvis with process id ``pid`` waits on."""
    # Local\: per logon session, so only this user's processes can reach it.
    return f"Local\\jarvis-stop-{pid}"


def _kernel32():
    """kernel32 with the event functions typed (64-bit handles stay intact)."""
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateEventW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR)
    k.CreateEventW.restype = wintypes.HANDLE
    k.OpenEventW.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR)
    k.OpenEventW.restype = wintypes.HANDLE
    k.SetEvent.argtypes = (wintypes.HANDLE,)
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    return k


def listen_for_stop(callback: Callable[[], None]) -> bool:
    """Call ``callback`` (on a daemon thread) when another process asks this one to quit.

    Windows only — POSIX callers handle ``SIGTERM`` themselves. Returns whether
    a listener was set up.
    """
    if not IS_WINDOWS:
        return False
    k = _kernel32()
    # Manual-reset, initially clear: once set it stays set, so a request is never lost.
    handle = k.CreateEventW(None, True, False, event_name(os.getpid()))
    if not handle:
        return False
    _listening.append(handle)

    def _wait() -> None:
        if k.WaitForSingleObject(handle, INFINITE) == 0:  # WAIT_OBJECT_0
            callback()

    threading.Thread(target=_wait, daemon=True, name="jarvis-stop-listener").start()
    return True


def request_stop(pid: int) -> bool:
    """Ask process ``pid`` to quit cleanly. True when the request was delivered.

    POSIX: ``SIGTERM``. Windows: sets the process's stop event; False when it
    has none (not a listening Jarvis, or already gone) — nothing is killed.
    """
    if pid <= 0:
        return False
    if not IS_WINDOWS:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return False
        return True
    k = _kernel32()
    handle = k.OpenEventW(EVENT_MODIFY_STATE, False, event_name(pid))
    if not handle:
        return False
    try:
        return bool(k.SetEvent(handle))
    finally:
        k.CloseHandle(handle)


def kill_tree(pid: int) -> None:
    """End ``pid`` now, with the processes it started. Never raises.

    Windows: ``taskkill /T /F`` (children are found from the parent pid).
    POSIX: ``SIGKILL`` to its process group when it leads one (a Jarvis opened
    with ``start_new_session``), else to the process alone.
    """
    if pid <= 0:
        return
    if IS_WINDOWS:
        try:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)], capture_output=True,
                           timeout=15, **hidden_subprocess_kwargs())
        except (OSError, subprocess.SubprocessError):
            pass
        return
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
            return
    except OSError:
        pass
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
