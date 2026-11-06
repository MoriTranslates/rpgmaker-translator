"""Windows-only calls must not crash the app on other systems.

Linux/macOS aren't supported, but running from source there should fail
softly (a message, a False) instead of with an AttributeError.  Each test
removes the Windows-only attribute to simulate that.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from translator import ai_client  # noqa: E402
from translator.ai_client import AIClient  # noqa: E402


class _Resp:
    status_code = 200

    @staticmethod
    def json():
        return {"models": [{"name": "qwen3.5:9b"}]}


def test_unload_models_without_create_no_window(monkeypatch):
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)
    monkeypatch.setattr(ai_client.requests, "get", lambda *a, **k: _Resp())
    seen = []
    monkeypatch.setattr(ai_client.subprocess, "run",
                        lambda cmd, **k: seen.append((cmd, k)))
    client = AIClient()
    client.provider = "Ollama (Local)"
    assert client.unload_models() == 1
    assert seen[0][0] == ["ollama", "stop", "qwen3.5:9b"]


def test_restart_server_without_create_no_window(monkeypatch):
    monkeypatch.delattr(subprocess, "CREATE_NO_WINDOW", raising=False)
    monkeypatch.setattr(AIClient, "_find_ollama_exe",
                        staticmethod(lambda: "/usr/bin/ollama"))
    monkeypatch.setattr(AIClient, "external_ollama_running",
                        staticmethod(lambda: False))
    monkeypatch.setattr(ai_client.time, "sleep", lambda s: None)

    class _Proc:
        pid = 1
        returncode = 1

        def poll(self):
            return 1     # exits at once: restart reports False, no crash

    started = []
    monkeypatch.setattr(ai_client.subprocess, "Popen",
                        lambda cmd, **k: started.append(cmd) or _Proc())
    client = AIClient()
    monkeypatch.setattr(client, "is_available", lambda: False)
    assert client.restart_server() is False
    assert started == [["/usr/bin/ollama", "serve"]]


def test_find_ollama_uses_the_plain_name_off_windows(monkeypatch, tmp_path):
    exe = tmp_path / "ollama"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(ai_client.os, "name", "posix")
    monkeypatch.setenv("PATH", str(tmp_path))
    assert AIClient._find_ollama_exe() == str(exe)


def test_find_ollama_on_windows_still_wants_the_exe(monkeypatch, tmp_path):
    (tmp_path / "ollama").write_text("", encoding="utf-8")
    monkeypatch.setattr(ai_client.os, "name", "nt")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "none"))
    found = AIClient._find_ollama_exe()
    assert found is None or found.lower().endswith("ollama.exe")
    (tmp_path / "ollama.exe").write_text("", encoding="utf-8")
    assert AIClient._find_ollama_exe() == str(tmp_path / "ollama.exe")


def test_open_with_default_app_without_startfile(monkeypatch, tmp_path):
    from translator.utils import open_with_default_app
    monkeypatch.delattr(os, "startfile", raising=False)
    with pytest.raises(OSError):
        open_with_default_app(str(tmp_path / "game.rmmzproject"))
