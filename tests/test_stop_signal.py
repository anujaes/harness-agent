"""Asking another Jarvis to quit cleanly: SIGTERM on POSIX, a named event on Windows."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

import pytest

from jarvis.utils import stop_signal

_CHILD = r'''
import sys, threading, time
sys.path.insert(0, {root!r})
from jarvis.utils import stop_signal
done = threading.Event()
stop_signal.listen_for_stop(done.set)
import os
print(os.getpid(), flush=True)  # a venv python.exe on Windows is a shim: proc.pid isn't us
if done.wait(20):
    print("stopped cleanly", flush=True)
    sys.exit(0)
sys.exit(5)
'''


def _wait(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_a_stop_request_reaches_the_listener_and_it_exits_cleanly(tmp_path):
    script = tmp_path / "child.py"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script.write_text(_CHILD.format(root=root), encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        pid = int(proc.stdout.readline().strip())
        assert stop_signal.request_stop(pid) is True
        out, _ = proc.communicate(timeout=10)
        assert proc.returncode == 0 and "stopped cleanly" in out  # its own cleanup ran
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stopping_a_process_that_does_not_listen_says_so():
    if sys.platform != "win32":
        pytest.skip("POSIX: SIGTERM reaches any process")
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert stop_signal.request_stop(proc.pid) is False  # no event: nothing was asked
        time.sleep(0.2)
        assert proc.poll() is None  # and nothing was killed
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="named events are the Windows path")
def test_listening_in_this_process_and_the_event_name():
    assert stop_signal.event_name(1234) == "Local\\jarvis-stop-1234"
    hit = threading.Event()
    stop_signal.listen_for_stop(hit.set)
    assert stop_signal.request_stop(os.getpid()) is True
    assert _wait(hit.is_set)


def test_kill_tree_ends_the_process_and_its_children(tmp_path):
    marker = tmp_path / "grandchild.pid"
    code = (
        "import subprocess, sys, time;"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
        f"open({str(marker)!r}, 'w').write(str(p.pid));"
        "time.sleep(60)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code], start_new_session=sys.platform != "win32")
    try:
        assert _wait(lambda: marker.exists() and marker.read_text())
        grandchild = int(marker.read_text())
        stop_signal.kill_tree(proc.pid)
        assert _wait(lambda: proc.poll() is not None)
        from jarvis.utils.osinfo import pid_alive

        assert _wait(lambda: not pid_alive(grandchild))
    finally:
        if proc.poll() is None:
            proc.kill()
