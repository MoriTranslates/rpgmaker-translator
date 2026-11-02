"""Consent before destructive / downloading actions (Ollama, EasyRPG)."""

import os
import subprocess

import pytest

from translator.ai_client import AIClient


# ── Ollama lifecycle ─────────────────────────────────────────────────

def _record_subprocess(monkeypatch, tasklist_out=""):
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout=tasklist_out, stderr="")

    def fake_popen(*a, **k):
        calls.append(["POPEN"] + list(a[0]))
        raise OSError("not starting anything in tests")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr("time.sleep", lambda s: None)
    return calls


def test_stop_server_never_kills_other_ollama_by_default(monkeypatch):
    calls = _record_subprocess(monkeypatch)
    AIClient().stop_server()
    assert not any("taskkill" in c or "net" in c for c in calls)


def test_stop_server_kills_only_with_consent_flag(monkeypatch):
    calls = _record_subprocess(monkeypatch)
    AIClient().stop_server(kill_external=True)
    assert ["taskkill", "/F", "/IM", "ollama.exe"] in calls


@pytest.mark.skipif(os.name != "nt", reason="tasklist is Windows-only")
def test_restart_without_consent_leaves_running_ollama_alone(monkeypatch):
    calls = _record_subprocess(
        monkeypatch, tasklist_out="ollama.exe   1234 Console  1  50,000 K")
    c = AIClient()
    monkeypatch.setattr(c, "is_available", lambda: False)
    assert c.restart_server(2) is False
    assert not any("taskkill" in x for x in calls)
    assert not any(x and x[0] == "POPEN" for x in calls)


@pytest.mark.parametrize("url, local", [
    ("http://localhost:11434", True), ("http://127.0.0.1:11434", True),
    ("http://192.168.1.20:11434", False), ("https://ollama.example.com", False),
])
def test_is_local_server(url, local):
    c = AIClient(base_url=url)
    assert c.is_local_server() is local


@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    win = mw.MainWindow()
    win._closing = True
    yield win
    win._autosave_timer.stop()
    win.hide()
    _KEEP.append(win)


_KEEP: list = []


def test_remote_ollama_down_never_restarts_local(window, monkeypatch):
    from translator.widgets import main_window as mw
    window.client.provider = "Ollama (Local)"
    window.client.base_url = "http://192.168.1.20:11434"
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    called = []
    monkeypatch.setattr(window.client, "restart_server",
                        lambda *a, **k: called.append(a))
    assert window._ensure_ollama_ready() is False
    assert called == []


def test_hung_local_ollama_is_only_killed_after_yes(window, monkeypatch):
    from translator.widgets import main_window as mw
    window.client.provider = "Ollama (Local)"
    window.client.base_url = "http://localhost:11434"
    monkeypatch.setattr(mw.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(window.client, "external_ollama_running", lambda: True)
    monkeypatch.setattr(window, "_run_blocking",
                        lambda t, l, fn, *a: called.append(a))
    called = []
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.No)
    window._ensure_ollama_ready()
    assert called == []
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.Yes)
    window._ensure_ollama_ready()
    assert called and called[0][-1] is True     # kill_external=True


# ── EasyRPG Player ───────────────────────────────────────────────────

def test_easyrpg_needs_consent(window, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    monkeypatch.setattr(mw, "app_dir", lambda: str(tmp_path / "app"))
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.No)

    def no_network(*a, **k):
        raise AssertionError("must not download without consent")
    monkeypatch.setattr("urllib.request.urlopen", no_network)
    game = tmp_path / "game"
    game.mkdir()
    assert window._ensure_easyrpg_player(str(game)) == ""
    assert not (game / "Player.exe").exists()


def test_easyrpg_rejects_tampered_download(window, monkeypatch, tmp_path):
    import io
    import zipfile
    from translator.widgets import main_window as mw
    monkeypatch.setattr(mw, "app_dir", lambda: str(tmp_path / "app"))
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.Yes)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Player.exe", b"MZ evil")

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **k: _Resp(buf.getvalue()))
    monkeypatch.setattr(window, "_run_blocking",
                        lambda t, l, fn, *a: fn(*a))
    game = tmp_path / "game"
    game.mkdir()
    msg = window._ensure_easyrpg_player(str(game))
    assert "not added" in msg and "SHA-256" in msg
    assert not (game / "Player.exe").exists()
    assert not (tmp_path / "app" / "tools" / "easyrpg-player.exe").exists()


def test_easyrpg_poisoned_cache_is_not_used(window, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    tools = tmp_path / "app" / "tools"
    tools.mkdir(parents=True)
    (tools / "easyrpg-player.exe").write_bytes(b"MZ swapped")
    monkeypatch.setattr(mw, "app_dir", lambda: str(tmp_path / "app"))
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.Yes)

    def offline(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr("urllib.request.urlopen", offline)
    monkeypatch.setattr(window, "_run_blocking",
                        lambda t, l, fn, *a: fn(*a))
    game = tmp_path / "game"
    game.mkdir()
    window._ensure_easyrpg_player(str(game))
    assert not (game / "Player.exe").exists()
