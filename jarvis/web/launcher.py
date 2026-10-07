"""Open a project from the browser: start a Jarvis with no terminal in a folder.

``open_project`` starts ``jarvis --headless`` in the folder — the same app as
in a terminal, drawn nowhere, with its web remote on — detached from this
process (its own session), so it keeps running when this terminal closes.
A watcher thread follows it until it is listed in ``registry`` with a session
(``ready``), or it exits / takes too long (``failed``, with the end of its log).
The page polls ``launch_status`` and switches to it when it's ready.

``stop_project`` ends a Jarvis that was opened this way; one that runs in a
terminal is the user's to quit there.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..constants import CONFIG_DIR
from ..utils import stop_signal
from ..utils.osinfo import CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, IS_WINDOWS, pid_alive
from . import fs_api, registry

CREATE_BREAKAWAY_FROM_JOB = 0x01000000  # Windows: leave the parent's job object

LAUNCH_TIMEOUT = 45.0   # a cold start (imports, catalogs) takes a few seconds; this is the "stuck" line
STOP_TIMEOUT = 10.0    # idle: well under a second; right after starting it may finish startup work first
MAX_RUNNING = 16
LOG_DIR = CONFIG_DIR / "instances" / "logs"
KEEP_LOGS = 20
# Inherited settings that belong to the terminal Jarvis, not to a project opened from it.
_DROP_ENV = ("HARNESS_WEB_TUNNEL", "HARNESS_WEB", "HARNESS_UPDATED_REEXEC", "HARNESS_UPDATE_RESULT")


def command() -> list[str]:
    """How a project is started (tests swap this for a stand-in)."""
    return [sys.executable, "-m", "jarvis", "--headless"]


@dataclass
class Launch:
    id: str
    path: str
    status: str = "starting"      # starting | ready | failed
    stage: str = "spawn"          # spawn → boot → ready
    pid: int = 0
    instance_id: str = ""
    code: str = ""
    error: str = ""
    log: str = ""
    log_path: str = ""
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "path": self.path, "display": fs_api.display(self.path),
            "name": Path(self.path).name or self.path, "status": self.status, "stage": self.stage,
            "instance_id": self.instance_id, "code": self.code, "error": self.error, "log": self.log,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
        }


_launches: dict[str, Launch] = {}
_lock = threading.Lock()


def _running_in(path: Path) -> list[dict[str, Any]]:
    real = fs_api.same_dir_key(path)
    return [r for r in registry.list_instances() if r.get("cwd") and fs_api.same_dir_key(str(r["cwd"])) == real]


def _log_tail(path: str, lines: int = 25) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # A headless Textual app may still write escape codes on exit: keep readable lines.
    keep = [ln.rstrip() for ln in text.splitlines() if ln.strip() and "\x1b" not in ln]
    return "\n".join(keep[-lines:])


def _reason(tail: str) -> str:
    """The line of the log that says what went wrong."""
    for line in reversed(tail.splitlines()):
        s = line.strip()
        if s and ("Error" in s or "error:" in s.lower() or "Exception" in s):
            return s[:300]
    return ""


def _prune_logs() -> None:
    try:
        logs = sorted(LOG_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return
    for old in logs[KEEP_LOGS:]:
        try:
            old.unlink()
        except OSError:
            pass


def _finish(launch: Launch, status: str, *, code: str = "", error: str = "") -> None:
    with _lock:
        launch.status = status
        launch.code = code
        launch.error = error
        launch.finished = time.time()
        if status == "ready":
            launch.stage = "ready"


def _launched_record(launch: Launch, pid: int) -> dict[str, Any] | None:
    """The registry entry of the Jarvis started as process ``pid``.

    Usually its own entry (``id`` = its pid). On Windows a venv's
    ``python.exe`` is a launcher that runs the real interpreter as a child, so
    the Jarvis that registers is not ``pid``: it is found by the
    ``HARNESS_LAUNCH_ID`` it inherited (any number of shims deep).
    """
    rec = registry.get(str(pid))
    if rec is not None:
        return rec
    for rec in registry.list_instances(prune=False):
        if rec.get("launch_id") == launch.id:
            return rec
    return None


def _watch(launch: Launch, proc: subprocess.Popen) -> None:
    deadline = time.time() + LAUNCH_TIMEOUT
    while time.time() < deadline:
        rec = _launched_record(launch, proc.pid)
        if rec is not None:
            iid = str(rec["id"])
            with _lock:
                launch.stage = "boot"
                launch.instance_id = iid
            if rec.get("session_id") is not None:  # it has a chat to show
                _finish(launch, "ready")
                break
        rc = proc.poll()
        if rc is not None:
            tail = _log_tail(launch.log_path)
            with _lock:
                launch.log = tail
            _finish(launch, "failed", code="exited",
                    error=_reason(tail) or f"Jarvis stopped while starting (exit code {rc}).")
            return
        time.sleep(0.2)
    else:
        if IS_WINDOWS:
            # terminate() would leave whatever it already started running.
            stop_signal.kill_tree(proc.pid)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass
        tail = _log_tail(launch.log_path)
        with _lock:
            launch.log = tail
        _finish(launch, "failed", code="timeout",
                error=f"Jarvis didn't start within {int(LAUNCH_TIMEOUT)} seconds.")
        return
    # Ready: it lives on its own now. Reap it when it exits, so it never lingers
    # as a zombie while this Jarvis runs.
    try:
        proc.wait()
    except Exception:
        pass


def _spawn(cmd: list[str], *, cwd: str, env: dict[str, str], log: Any) -> subprocess.Popen:
    """Start a project's Jarvis detached from this terminal."""
    common = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                  stderr=subprocess.STDOUT, close_fds=True)
    if not IS_WINDOWS:
        return subprocess.Popen(cmd, start_new_session=True, **common)  # survives this terminal closing
    # Windows has no sessions: a hidden console of its own (so closing this
    # terminal's window never reaches it, and the shells it runs don't flash
    # windows up) in its own process group (Ctrl+C here doesn't either).
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        # Also leave the terminal's job object, which may kill its members on close.
        return subprocess.Popen(cmd, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **common)
    except PermissionError:
        return subprocess.Popen(cmd, creationflags=flags, **common)  # that job forbids leaving


def open_project(raw_path: str, *, reuse: bool = True) -> dict[str, Any]:
    """Start (or, with ``reuse``, find) a Jarvis in a folder. Never raises."""
    try:
        path = fs_api.resolve_dir(raw_path)
    except fs_api.FsError as exc:
        return {"ok": False, "code": exc.code, "error": str(exc)}
    if reuse:
        existing = _running_in(path)
        if existing:
            return {"ok": True, "reused": True, "instance_id": existing[0]["id"],
                    "launch": {"status": "ready", "instance_id": existing[0]["id"], "path": str(path),
                               "display": fs_api.display(path), "name": path.name}}
    if len(registry.list_instances()) >= MAX_RUNNING:
        return {"ok": False, "code": "too_many",
                "error": f"{MAX_RUNNING} projects are already open. Stop one you're done with first."}

    launch = Launch(id=uuid.uuid4().hex[:12], path=str(path))
    env = {k: v for k, v in os.environ.items() if k not in _DROP_ENV}
    env["PWD"] = str(path)
    env["HARNESS_LAUNCHED_BY"] = str(os.getpid())
    env["HARNESS_LAUNCH_ID"] = launch.id  # registry.Registration records it
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in path.name)[:40] or "root"
        launch.log_path = str(LOG_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}-{launch.id[:4]}.log")
        log = open(launch.log_path, "ab")
    except OSError as exc:
        return {"ok": False, "code": "no_log", "error": f"Couldn't prepare the project's log: {exc}."}
    try:
        proc = _spawn(command(), cwd=str(path), env=env, log=log)
    except OSError as exc:
        log.close()
        return {"ok": False, "code": "spawn_failed", "error": f"Couldn't start Jarvis: {exc.strerror or exc}."}
    finally:
        _prune_logs()
    log.close()  # the child holds its own copy
    launch.pid = proc.pid
    with _lock:
        _launches[launch.id] = launch
        # Forget finished launches after a while.
        cutoff = time.time() - 600
        for lid in [k for k, v in _launches.items() if v.finished and v.finished < cutoff]:
            _launches.pop(lid, None)
    fs_api.remember_dir(path)
    threading.Thread(target=_watch, args=(launch, proc), daemon=True, name=f"jarvis-launch-{launch.id}").start()
    return {"ok": True, "launch": launch.public()}


def launch_status(launch_id: str) -> dict[str, Any] | None:
    with _lock:
        launch = _launches.get(launch_id or "")
        return launch.public() if launch else None


def stop_project(instance_id: str) -> dict[str, Any]:
    """End a Jarvis that was opened from the web. Never raises."""
    rec = registry.get(instance_id)
    if rec is None:
        return {"ok": True, "already": True}
    if not rec.get("headless"):
        return {"ok": False, "code": "terminal",
                "error": f"{rec.get('project') or 'This Jarvis'} runs in a terminal — quit it there (Ctrl+C twice)."}
    pid = int(rec.get("pid") or 0)
    if pid <= 0:
        return {"ok": False, "code": "bad_entry", "error": "That project's entry is damaged."}
    if IS_WINDOWS:
        return _stop_windows(instance_id, pid)
    if pid == os.getpid():
        # Stopping the Jarvis that serves this page: answer first, then go.
        threading.Timer(0.4, lambda: os.kill(pid, signal.SIGTERM)).start()
        return {"ok": True, "self": True}
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": True, "already": True}
    except PermissionError:
        return {"ok": False, "code": "no_access", "error": "Not allowed to stop that process."}
    deadline = time.time() + STOP_TIMEOUT
    while time.time() < deadline:
        if registry.get(instance_id) is None:
            return {"ok": True}
        time.sleep(0.15)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    registry.forget(instance_id)  # killed: its own cleanup never ran
    return {"ok": True, "forced": True}


def _stop_windows(instance_id: str, pid: int) -> dict[str, Any]:
    """``stop_project`` on Windows, where ``os.kill`` would be ``TerminateProcess``.

    The headless Jarvis listens on its stop event (``utils.stop_signal``) and
    exits through the app, so its registry entry, MCP servers and jobs are
    cleaned up. One that doesn't go in time is ended with its whole tree.
    """
    if pid == os.getpid():
        # Stopping the Jarvis that serves this page: answer first, then go.
        threading.Timer(0.4, lambda: stop_signal.request_stop(pid)).start()
        return {"ok": True, "self": True}
    if not pid_alive(pid):
        registry.forget(instance_id)
        return {"ok": True, "already": True}
    if stop_signal.request_stop(pid):
        deadline = time.time() + STOP_TIMEOUT
        while time.time() < deadline:
            if registry.get(instance_id) is None:
                return {"ok": True}
            time.sleep(0.15)
    stop_signal.kill_tree(pid)
    registry.forget(instance_id)  # killed: its own cleanup never ran
    return {"ok": True, "forced": True}
