"""Guards for the test harness itself (2026-11-07 fast/reliable suite fix).

A full run used to die silently (exit 127, no summary): a SettingsDialog
deleted with deleteLater() was really destroyed by a later test's event loop
while its model-fetch QThread was still waiting on the user's real Ollama, and
Qt aborted the process.  Kept-alive MainWindows also made the suite take
8-17 minutes.  These tests keep both from coming back.
"""

import os
import re
import time

import pytest
import requests

from qt_helpers import dispose

TESTS = os.path.dirname(os.path.abspath(__file__))


def test_real_network_is_blocked():
    t0 = time.monotonic()
    with pytest.raises(requests.ConnectionError):
        requests.get("http://localhost:11434/api/tags", timeout=5)   # Ollama
    with pytest.raises(requests.ConnectionError):
        requests.get("https://example.com", timeout=5)               # DNS + internet
    assert time.monotonic() - t0 < 2   # fails at once, never waits on a timeout


def test_loopback_socketpair_still_works():
    import socket
    a, b = socket.socketpair()   # asyncio's self-pipe on Windows
    try:
        a.sendall(b"x")
        assert b.recv(1) == b"x"
    finally:
        a.close()
        b.close()


def test_dispose_waits_for_a_running_fetch_thread(qapp, monkeypatch):
    """The crash: a dialog destroyed while its fetch thread still runs."""
    from translator.ai_client import AIClient
    from translator.widgets import settings_dialog as sd
    monkeypatch.setattr(sd._ModelFetcher, "run", lambda self: time.sleep(0.3))
    c = AIClient()
    c.provider = "Ollama (Local)"
    dlg = sd.SettingsDialog(c)
    fetcher = dlg._model_fetcher
    assert fetcher.isRunning()
    dispose(dlg)                      # must wait, not abort the process
    with pytest.raises(RuntimeError):
        dlg.objectName()              # and the dialog is really gone now


_BANNED = [
    (re.compile(r"\.deleteLater\(\)"),
     "use qt_helpers.dispose(): deleteLater() outside an event loop deletes "
     "the widget during some later test, possibly with its threads running"),
    (re.compile(r"_KEEP\w*\s*(?::\s*list\s*)?=\s*\[\]"),
     "don't keep test widgets alive: use qt_helpers.dispose_main_window()"),
]


def test_tests_dispose_widgets_with_the_helper():
    problems = []
    for name in sorted(os.listdir(TESTS)):
        if not name.endswith(".py") or name in ("qt_helpers.py", "test_test_harness.py"):
            continue
        with open(os.path.join(TESTS, name), encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                for pattern, why in _BANNED:
                    if pattern.search(line):
                        problems.append(f"{name}:{lineno}: {why}")
    assert not problems, "\n".join(problems)
