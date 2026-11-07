"""Ollama runners must not be orphaned when the app stops or restarts Ollama.

No real processes: subprocess and the process listing are replaced with fakes.
"""

import os
import subprocess

import pytest

from translator import ollama_procs
from translator.ai_client import AIClient
from translator.ollama_procs import Proc


# ── 1. Ollama runners must not be orphaned ───────────────────────────

def _record_run(monkeypatch):
    calls = []

    def fake_run(cmd, *a, **k):
        assert isinstance(cmd, list), "commands must be argument lists"
        assert not k.get("shell"), "never through a shell"
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return calls


class _FakeProc:
    def __init__(self, pid=4321):
        self.pid = pid
        self.terminated = False
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 1

    def kill(self):
        self.killed = True
        self.returncode = 1

    def wait(self, timeout=None):
        self.returncode = 1
        return 1


@pytest.fixture
def ollama_dir(tmp_path, monkeypatch):
    """A fake Ollama install: <tmp>/Ollama/lib/ollama is the runner folder."""
    lib = tmp_path / "Ollama" / "lib" / "ollama"
    lib.mkdir(parents=True)
    monkeypatch.setattr(ollama_procs, "is_windows", lambda: True)
    monkeypatch.setattr(ollama_procs, "ollama_runner_dirs", lambda: [str(lib)])
    return lib


def _procs(monkeypatch, procs):
    monkeypatch.setattr(ollama_procs, "list_processes", lambda: procs)


def _killed_pids(calls):
    return [c[-1] for c in calls if c[:1] == ["taskkill"] and "/PID" in c]


@pytest.mark.skipif(os.name != "nt", reason="taskkill tree kill is Windows-only")
def test_stop_server_kills_managed_ollama_with_its_whole_tree(monkeypatch):
    calls = _record_run(monkeypatch)
    _procs(monkeypatch, [])
    c = AIClient()
    c._managed_proc = _FakeProc(4321)
    c.stop_server()
    assert ["taskkill", "/F", "/T", "/PID", "4321"] in calls
    assert c._managed_proc is None


@pytest.mark.skipif(os.name != "nt", reason="taskkill tree kill is Windows-only")
def test_cleanup_kills_managed_ollama_with_its_whole_tree(monkeypatch):
    calls = _record_run(monkeypatch)
    _procs(monkeypatch, [])
    c = AIClient()
    c._managed_proc = _FakeProc(777)
    c.cleanup()
    assert ["taskkill", "/F", "/T", "/PID", "777"] in calls
    assert c._managed_proc is None


@pytest.mark.skipif(os.name != "nt", reason="taskkill tree kill is Windows-only")
def test_tree_kill_failure_falls_back_to_terminate(monkeypatch):
    def failing_run(cmd, *a, **k):
        raise OSError("taskkill missing")
    monkeypatch.setattr(subprocess, "run", failing_run)
    monkeypatch.setattr("time.sleep", lambda s: None)
    _procs(monkeypatch, [])
    c = AIClient()
    p = _FakeProc(55)
    c._managed_proc = p
    c.stop_server()
    assert p.terminated
    assert c._managed_proc is None


def test_kill_external_kills_ollama_trees(monkeypatch):
    calls = _record_run(monkeypatch)
    _procs(monkeypatch, [])
    AIClient().stop_server(kill_external=True)
    assert ["taskkill", "/F", "/T", "/IM", "ollama.exe"] in calls


def test_worker_change_restart_of_tray_serve_kills_tree_then_sweeps(
        monkeypatch, ollama_dir):
    # The user's repro: Settings worker change -> restart_server(kill_external=True)
    # while the serve was started by the Ollama tray app (not our _managed_proc).
    # taskkill /IM without /T left the loaded model's runner behind.
    calls = _record_run(monkeypatch)
    tray_dir = ollama_dir.parent.parent
    serve_alive = {"yes": True}

    def listing():
        procs = [Proc(12172, 4, "ollama app.exe", str(tray_dir / "ollama app.exe")),
                 Proc(90968, 95592, "llama-server.exe",
                      str(ollama_dir / "llama-server.exe"))]
        if serve_alive["yes"]:
            procs.append(Proc(95592, 12172, "ollama.exe", str(tray_dir / "ollama.exe")))
        return procs

    real_run = subprocess.run

    def run(cmd, *a, **k):
        r = real_run(cmd, *a, **k)  # the recording fake from _record_run
        if cmd[:1] == ["taskkill"] and "/IM" in cmd:
            serve_alive["yes"] = False  # serve dies; runner left orphaned
        return r
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(ollama_procs, "list_processes", listing)

    def no_start(*a, **k):
        raise OSError("not starting anything in tests")
    monkeypatch.setattr(subprocess, "Popen", no_start)

    c = AIClient()
    monkeypatch.setattr(c, "_find_ollama_exe", lambda: str(tray_dir / "ollama.exe"))
    assert c._managed_proc is None
    c.restart_server(4, kill_external=True)
    tree = ["taskkill", "/F", "/T", "/IM", "ollama.exe"]
    assert tree in calls
    assert ["taskkill", "/F", "/PID", "90968"] in calls
    assert calls.index(tree) < calls.index(["taskkill", "/F", "/PID", "90968"])
    assert "12172" not in _killed_pids(calls)  # the tray app is left alone


@pytest.mark.skipif(os.name != "nt", reason="taskkill tree kill is Windows-only")
def test_restart_with_same_parallel_count_is_skipped(monkeypatch):
    calls = _record_run(monkeypatch)
    started = []

    def popen(cmd, *a, **k):
        started.append((list(cmd), k.get("env", {}).get("OLLAMA_NUM_PARALLEL")))
        return _FakeProc(1000 + len(started))
    monkeypatch.setattr(subprocess, "Popen", popen)
    c = AIClient()
    monkeypatch.setattr(c, "_find_ollama_exe", lambda: r"C:\Ollama\ollama.exe")
    monkeypatch.setattr(c, "external_ollama_running", lambda: False)
    monkeypatch.setattr(c, "is_available", lambda: True)
    assert c.restart_server(3) is True
    assert [p for _, p in started] == ["3"]
    calls.clear()
    assert c.restart_server(3, kill_external=True) is True   # same count: no restart
    assert [p for _, p in started] == ["3"]
    assert calls == []
    assert c.restart_server(4) is True                       # new count: restart
    assert [p for _, p in started] == ["3", "4"]
    assert ["taskkill", "/F", "/T", "/PID", "1001"] in calls


def test_restart_not_skipped_when_server_unresponsive(monkeypatch):
    _record_run(monkeypatch)
    c = AIClient()
    c._managed_proc = _FakeProc(1001)
    c._managed_parallel = 3
    monkeypatch.setattr(c, "is_available", lambda: False)
    monkeypatch.setattr(c, "_find_ollama_exe", lambda: None)
    assert c.restart_server(3) is False
    assert c._managed_proc is None  # it was stopped for the restart


def test_orphan_ollama_runner_is_killed(monkeypatch, ollama_dir):
    calls = _record_run(monkeypatch)
    _procs(monkeypatch, [
        Proc(10, 4, "System", None),
        Proc(500, 999, "llama-server.exe", str(ollama_dir / "llama-server.exe")),
    ])
    AIClient().stop_server()
    assert _killed_pids(calls) == ["500"]


def test_orphan_runner_in_subfolder_of_ollama_lib_is_killed(monkeypatch, ollama_dir):
    calls = _record_run(monkeypatch)
    sub = ollama_dir / "cuda_v12"
    _procs(monkeypatch, [
        Proc(501, 999, "llama-server.exe", str(sub / "llama-server.exe"))])
    assert ollama_procs.kill_orphan_runners() == [501]
    assert _killed_pids(calls) == ["501"]


def test_cleanup_sweeps_orphan_runners(monkeypatch, ollama_dir):
    calls = _record_run(monkeypatch)
    _procs(monkeypatch, [
        Proc(502, 999, "llama-server.exe", str(ollama_dir / "llama-server.exe"))])
    AIClient().cleanup()
    assert _killed_pids(calls) == ["502"]


def test_non_ollama_llama_server_is_never_killed(monkeypatch, ollama_dir, tmp_path):
    calls = _record_run(monkeypatch)
    other = tmp_path / "llama.cpp" / "llama-server.exe"
    lookalike = tmp_path / "Ollama" / "lib" / "ollama2" / "llama-server.exe"
    _procs(monkeypatch, [
        Proc(600, 999, "llama-server.exe", str(other)),       # user's llama.cpp
        Proc(601, 999, "llama-server.exe", str(lookalike)),   # prefix trick
        Proc(602, 999, "llama-server.exe", None),             # path unknown
    ])
    assert ollama_procs.kill_orphan_runners() == []
    assert _killed_pids(calls) == []


def test_runner_with_live_parent_is_never_killed(monkeypatch, ollama_dir):
    calls = _record_run(monkeypatch)
    _procs(monkeypatch, [
        Proc(300, 4, "ollama.exe", str(ollama_dir.parent.parent / "ollama.exe")),
        Proc(700, 300, "llama-server.exe", str(ollama_dir / "llama-server.exe")),
    ])
    assert ollama_procs.kill_orphan_runners() == []
    assert _killed_pids(calls) == []


def test_other_processes_are_never_killed(monkeypatch, ollama_dir):
    calls = _record_run(monkeypatch)
    _procs(monkeypatch, [
        Proc(800, 999, "ollama.exe", str(ollama_dir / "ollama.exe")),
        Proc(801, 999, "notepad.exe", str(ollama_dir / "notepad.exe")),
    ])
    assert ollama_procs.kill_orphan_runners() == []
    assert _killed_pids(calls) == []


def test_process_query_failure_kills_nothing(monkeypatch, ollama_dir):
    calls = _record_run(monkeypatch)

    def boom():
        raise OSError("snapshot failed")
    monkeypatch.setattr(ollama_procs, "_snapshot_processes", boom)
    monkeypatch.setattr(ollama_procs, "list_processes",
                        ollama_procs.__dict__["_real_list_processes"])
    assert ollama_procs.list_processes() is None
    assert ollama_procs.kill_orphan_runners() == []
    assert calls == []


def test_listing_none_kills_nothing(monkeypatch, ollama_dir):
    calls = _record_run(monkeypatch)
    _procs(monkeypatch, None)
    assert ollama_procs.kill_orphan_runners() == []
    assert calls == []


def test_unknown_ollama_install_kills_nothing(monkeypatch):
    calls = _record_run(monkeypatch)
    monkeypatch.setattr(ollama_procs, "is_windows", lambda: True)
    monkeypatch.setattr(ollama_procs, "ollama_runner_dirs", lambda: [])
    _procs(monkeypatch, [Proc(900, 999, "llama-server.exe", r"C:\x\llama-server.exe")])
    assert ollama_procs.kill_orphan_runners() == []
    assert calls == []


def test_not_windows_kills_nothing(monkeypatch, ollama_dir):
    calls = _record_run(monkeypatch)
    monkeypatch.setattr(ollama_procs, "is_windows", lambda: False)
    _procs(monkeypatch, [
        Proc(500, 999, "llama-server.exe", str(ollama_dir / "llama-server.exe"))])
    assert ollama_procs.kill_orphan_runners() == []
    assert calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows process snapshot")
def test_real_snapshot_sees_this_process_and_its_parent():
    # Read-only: lists processes, kills nothing.
    procs = ollama_procs._snapshot_processes()
    me = [p for p in procs if p.pid == os.getpid()]
    assert len(me) == 1
    assert me[0].ppid == os.getppid()
    assert me[0].name.lower().startswith("python")
    assert me[0].exe and os.path.isfile(me[0].exe)
