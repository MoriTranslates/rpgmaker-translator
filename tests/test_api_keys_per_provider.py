"""Each cloud provider keeps its own API key.

Settings used to hold one key: saving an OpenRouter key wiped the saved
Gemini key. Keys now live per provider (``api_keys`` in the settings file);
``api_key`` + ``api_key_provider`` are still written for older versions.
A key is only ever used for the provider it belongs to, and an old key whose
owner is unknown is kept in the file but never used.
No network: the model-list fetch is stubbed and openai.OpenAI is faked.
"""

import json

import openai
import pytest
from qt_helpers import dispose, dispose_main_window

GEMINI = "Google Gemini"
OR = "OpenRouter"
OLLAMA = "Ollama (Local)"


@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "_settings.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


@pytest.fixture
def window(qapp, monkeypatch, settings_file):
    from translator.widgets import main_window as mw
    from translator.widgets import settings_dialog as sd
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", settings_file)
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    monkeypatch.setattr(sd.SettingsDialog, "_start_cloud_model_fetch",
                        lambda self: None)
    monkeypatch.setattr(sd.SettingsDialog, "_start_model_fetch",
                        lambda self: None)
    win = mw.MainWindow()
    win._closing = True
    yield win
    dispose_main_window(win)


def _dialog(win):
    from translator.widgets import settings_dialog as sd
    return sd.SettingsDialog(win.client)


def _write(path, cfg):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_in_dialog(win, provider, key=None):
    dlg = _dialog(win)
    try:
        dlg.provider_combo.setCurrentText(provider)
        if key is not None:
            dlg.api_key_edit.setText(key)
        shown = dlg.api_key_edit.text()
        dlg._save()
    finally:
        dispose(dlg)
    win._save_settings()
    return shown


def test_second_provider_key_does_not_wipe_first(window, settings_file):
    _save_in_dialog(window, GEMINI, "gem-key")
    _save_in_dialog(window, OR, "or-key")
    cfg = _read(settings_file)
    assert cfg["api_keys"] == {GEMINI: "gem-key", OR: "or-key"}
    # Older versions still read the active provider's key
    assert (cfg["provider"], cfg["api_key"], cfg["api_key_provider"]) == (
        OR, "or-key", OR)
    assert (window.client.provider, window.client.api_key) == (OR, "or-key")


def test_switching_back_shows_and_uses_first_key(window, settings_file,
                                                 monkeypatch):
    _save_in_dialog(window, GEMINI, "gem-key")
    _save_in_dialog(window, OR, "or-key")
    shown = _save_in_dialog(window, GEMINI)          # just switch back
    assert shown == "gem-key"
    assert (window.client.provider, window.client.api_key) == (GEMINI, "gem-key")
    used = []

    class _Rec:
        def __init__(self, api_key=None, **kw):
            used.append(api_key)
    monkeypatch.setattr(openai, "OpenAI", _Rec)
    window.client._openai_clients.clear()
    window.client._get_openai_client(openai)
    assert used == ["gem-key"]
    assert _read(settings_file)["api_keys"] == {GEMINI: "gem-key", OR: "or-key"}


def test_keys_round_trip_through_settings_file(window, settings_file):
    _save_in_dialog(window, GEMINI, "gem-key")
    _save_in_dialog(window, OR, "or-key")
    window.client.api_keys = {}
    window.client.api_key = ""
    window._load_settings()
    assert window.client.api_keys == {GEMINI: "gem-key", OR: "or-key"}
    assert (window.client.provider, window.client.api_key) == (OR, "or-key")
    dlg = _dialog(window)
    try:
        dlg.provider_combo.setCurrentText(GEMINI)
        assert dlg.api_key_edit.text() == "gem-key"
    finally:
        dispose(dlg)


def test_empty_box_removes_only_that_providers_key(window, settings_file):
    _save_in_dialog(window, GEMINI, "gem-key")
    _save_in_dialog(window, OR, "or-key")
    _save_in_dialog(window, OR, "")
    cfg = _read(settings_file)
    assert cfg["api_keys"] == {GEMINI: "gem-key"}
    assert window.client.api_key == ""


def test_old_single_slot_cloud_file_loads_under_its_owner(window, settings_file):
    _write(settings_file, {"provider": GEMINI, "api_key": "gem-key"})
    window._load_settings()
    assert window.client.api_keys == {GEMINI: "gem-key"}
    assert window.client.api_key == "gem-key"
    _save_in_dialog(window, OR, "or-key")
    cfg = _read(settings_file)
    assert cfg["api_keys"] == {GEMINI: "gem-key", OR: "or-key"}


def test_old_file_with_owner_on_ollama_keeps_owner(window, settings_file):
    _write(settings_file, {"provider": OLLAMA, "api_key": "ant-key",
                           "api_key_provider": "Anthropic"})
    window._load_settings()
    assert window.client.api_keys == {"Anthropic": "ant-key"}
    window._save_settings()
    cfg = _read(settings_file)
    assert (cfg["api_key"], cfg["api_key_provider"]) == ("ant-key", "Anthropic")


def test_old_ollama_file_key_is_kept_but_never_given_an_owner(window,
                                                              settings_file):
    _write(settings_file, {"provider": OLLAMA, "api_key": "mystery"})
    window._load_settings()
    assert window.client.api_keys == {}
    # Saving without changes leaves the old key exactly where it was
    window._save_settings()
    cfg = _read(settings_file)
    assert (cfg["api_key"], cfg["api_key_provider"]) == ("mystery", "")
    # A cloud provider never gets it, and it survives saving a real key
    shown = _save_in_dialog(window, GEMINI)
    assert shown == ""
    assert window.client.api_key == ""
    _save_in_dialog(window, GEMINI, "gem-key")
    cfg = _read(settings_file)
    assert cfg["api_keys"] == {GEMINI: "gem-key"}
    assert "mystery" in json.dumps(cfg)          # not deleted
    window._load_settings()
    assert window.client.api_key == "gem-key"
    assert "mystery" not in window.client.api_keys.values()
