"""Per-engine batch size / workers are kept separately for local (Ollama)
and online (cloud) providers, so switching to Gemini never inherits the
local 5-line / 1-worker values."""

import json

import pytest

from translator.ai_client import (
    CLOUD_DEFAULT_WORKERS, LOCAL_DEFAULT_WORKERS, AIClient, get_model_pricing)

GEMINI = "Google Gemini"
OLLAMA = "Ollama (Local)"
MV = "rpgmaker_mv"
GEMINI_MODEL = "gemini-3.8-flash"


@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "_settings.json"
    path.write_text("{}", encoding="utf-8")
    return str(path)


@pytest.fixture
def window(qapp, monkeypatch, settings_file):
    from translator.widgets import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", settings_file)
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    win = mw.MainWindow()
    win._closing = True
    yield win
    win._autosave_timer.stop()
    win.hide()
    _KEEP.append(win)


_KEEP: list = []


def _use(win, provider, model=""):
    win.client.provider = provider
    win._global_model = model or win.client.model
    win.client.model = win._global_model


def _open_dialog(win, monkeypatch):
    """A SettingsDialog wired like _open_settings does, with no network."""
    from translator.widgets import settings_dialog as sd
    monkeypatch.setattr(sd.SettingsDialog, "_start_cloud_model_fetch",
                        lambda self: None)
    monkeypatch.setattr(sd.SettingsDialog, "_start_model_fetch",
                        lambda self: None)
    monkeypatch.setattr(AIClient, "is_local_server", lambda self: False)
    win.client.project_type = win._project_type
    win._save_engine_settings()
    dlg = sd.SettingsDialog(
        win.client, win, parser=win.parser, engine=win.engine,
        plugin_analyzer=win.plugin_analyzer,
        engine_overrides=win._engine_overrides,
        engine_handlers=win._engine_handlers)
    dlg._active_engine_key = win._project_type
    return dlg


# ── helpers on their own ───────────────────────────────────────────

def test_cloud_default_batch_known_and_unknown():
    from translator.ai_client import cloud_default_batch
    assert cloud_default_batch(GEMINI_MODEL) == \
        get_model_pricing(GEMINI_MODEL)["batch_size"] == 30
    assert cloud_default_batch("some-model-nobody-heard-of") == 30


# ── apply ──────────────────────────────────────────────────────────

def test_gemini_ignores_local_pair(window):
    window._engine_overrides = {MV: {"context_size": 5, "batch_size": 5,
                                     "workers": 1, "wordwrap_chars": 42}}
    _use(window, GEMINI, GEMINI_MODEL)
    window._apply_engine_settings(MV)
    assert window.engine.batch_size == 30
    assert window.engine.num_workers == CLOUD_DEFAULT_WORKERS


def test_ollama_still_uses_local_pair(window):
    window._engine_overrides = {MV: {"batch_size": 5, "workers": 1,
                                     "cloud_batch_size": 20,
                                     "cloud_workers": 6}}
    _use(window, OLLAMA, "qwen3.5:9b")
    window._apply_engine_settings(MV)
    assert (window.engine.batch_size, window.engine.num_workers) == (5, 1)


def test_saved_online_pair_is_used_for_cloud(window):
    window._engine_overrides = {MV: {"batch_size": 5, "workers": 1,
                                     "cloud_batch_size": 20,
                                     "cloud_workers": 6}}
    _use(window, GEMINI, GEMINI_MODEL)
    window._apply_engine_settings(MV)
    assert (window.engine.batch_size, window.engine.num_workers) == (20, 6)


def test_save_engine_settings_writes_only_the_matching_set(window):
    window._engine_overrides = {MV: {"batch_size": 5, "workers": 1}}
    _use(window, GEMINI, GEMINI_MODEL)
    window.engine.batch_size, window.engine.num_workers = 20, 6
    window._save_engine_settings()
    ov = window._engine_overrides[MV]
    assert (ov["batch_size"], ov["workers"]) == (5, 1)
    assert (ov["cloud_batch_size"], ov["cloud_workers"]) == (20, 6)
    # and back: local edits leave the online set alone
    _use(window, OLLAMA, "qwen3.5:9b")
    window.engine.batch_size, window.engine.num_workers = 7, 2
    window._save_engine_settings()
    ov = window._engine_overrides[MV]
    assert (ov["batch_size"], ov["workers"]) == (7, 2)
    assert (ov["cloud_batch_size"], ov["cloud_workers"]) == (20, 6)


def test_online_values_survive_save_load_round_trip(window, settings_file):
    window._engine_overrides = {MV: {"batch_size": 5, "workers": 1}}
    _use(window, GEMINI, GEMINI_MODEL)
    window.engine.batch_size, window.engine.num_workers = 20, 6
    window._save_settings()
    with open(settings_file, encoding="utf-8") as f:
        saved = json.load(f)["engine_settings"][MV]
    assert (saved["cloud_batch_size"], saved["cloud_workers"]) == (20, 6)
    assert (saved["batch_size"], saved["workers"]) == (5, 1)
    window._engine_overrides = {}
    window._load_settings()
    _use(window, GEMINI, GEMINI_MODEL)
    window._apply_engine_settings(MV)
    assert (window.engine.batch_size, window.engine.num_workers) == (20, 6)


def test_old_format_file_keeps_local_values_and_gets_online_defaults(
        window, settings_file):
    old = {"provider": GEMINI, "model": GEMINI_MODEL,
           "engine_settings": {MV: {"context_size": 5, "batch_size": 5,
                                    "workers": 1, "wordwrap_chars": 42}}}
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(old, f)
    window._load_settings()
    assert window._engine_overrides[MV]["batch_size"] == 5
    assert window._engine_overrides[MV]["workers"] == 1
    window._apply_engine_settings(MV)
    assert window.engine.batch_size == 30
    assert window.engine.num_workers == CLOUD_DEFAULT_WORKERS
    # the user's local pair is still there for Ollama
    _use(window, OLLAMA, "qwen3.5:9b")
    window._apply_engine_settings(MV)
    assert (window.engine.batch_size, window.engine.num_workers) == (5, 1)


def test_global_keys_cannot_reintroduce_local_pair(window, settings_file):
    """Old files stored the local pair in the global keys too."""
    old = {"provider": GEMINI, "model": GEMINI_MODEL,
           "batch_size": 5, "workers": 1,
           "engine_settings": {MV: {"batch_size": 5, "workers": 1}}}
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump(old, f)
    window._load_settings()   # no project loaded yet
    assert window.engine.batch_size == 30
    assert window.engine.num_workers == CLOUD_DEFAULT_WORKERS


@pytest.mark.parametrize("overrides", [
    {},                                  # no entry for the engine
    {MV: "garbage"},                     # non-dict entry
    {MV: None},
    {MV: {"workers": 1}},                # missing batch_size
    {MV: {"batch_size": "x", "workers": None,
          "cloud_batch_size": "x", "cloud_workers": 0}},
])
@pytest.mark.parametrize("provider", [GEMINI, OLLAMA])
def test_odd_overrides_do_not_crash(window, overrides, provider):
    window._engine_overrides = overrides
    _use(window, provider, GEMINI_MODEL if provider == GEMINI else "m")
    window._apply_engine_settings(MV)
    assert isinstance(window.engine.batch_size, int)
    assert window.engine.batch_size >= 1
    window._save_engine_settings()
    assert isinstance(window._engine_overrides[MV], dict)


def test_missing_engine_settings_key_loads(window, settings_file):
    with open(settings_file, "w", encoding="utf-8") as f:
        json.dump({"provider": GEMINI}, f)
    window._load_settings()
    window._apply_engine_settings(MV)
    assert window.engine.batch_size == 30


# ── settings dialog ────────────────────────────────────────────────

@pytest.mark.parametrize("local", [(5, 1), (8, 3)])
def test_dialog_ollama_gemini_ollama_ok_keeps_local_set(
        window, monkeypatch, local):
    batch, workers = local
    window._engine_overrides = {MV: {"context_size": 5, "batch_size": batch,
                                     "workers": workers,
                                     "wordwrap_chars": 42}}
    _use(window, OLLAMA, "qwen3.5:9b")
    window._apply_engine_settings(MV)
    dlg = _open_dialog(window, monkeypatch)
    dlg.provider_combo.setCurrentText(GEMINI)
    dlg.provider_combo.setCurrentText(OLLAMA)
    assert (dlg.batch_spin.value(), dlg.workers_spin.value()) == local
    dlg._save()
    ov = dlg.engine_overrides[MV]
    assert (ov["batch_size"], ov["workers"]) == local
    assert (dlg.engine.batch_size, dlg.engine.num_workers) == local


def test_dialog_switch_to_gemini_shows_online_defaults_then_saves_them(
        window, monkeypatch):
    window._engine_overrides = {MV: {"batch_size": 5, "workers": 1}}
    _use(window, OLLAMA, "qwen3.5:9b")
    window._apply_engine_settings(MV)
    dlg = _open_dialog(window, monkeypatch)
    dlg.provider_combo.setCurrentText(GEMINI)
    assert dlg.model_combo.currentText()
    assert dlg.batch_spin.value() == get_model_pricing(
        dlg.model_combo.currentText())["batch_size"]
    assert dlg.workers_spin.value() == CLOUD_DEFAULT_WORKERS
    dlg.batch_spin.setValue(20)
    dlg.workers_spin.setValue(6)
    dlg._save()
    ov = dlg.engine_overrides[MV]
    assert (ov["batch_size"], ov["workers"]) == (5, 1)
    assert (ov["cloud_batch_size"], ov["cloud_workers"]) == (20, 6)


def test_dialog_loads_saved_online_set_on_switch(window, monkeypatch):
    window._engine_overrides = {MV: {"batch_size": 5, "workers": 1,
                                     "cloud_batch_size": 20,
                                     "cloud_workers": 6}}
    _use(window, OLLAMA, "qwen3.5:9b")
    window._apply_engine_settings(MV)
    dlg = _open_dialog(window, monkeypatch)
    dlg.provider_combo.setCurrentText(GEMINI)
    assert (dlg.batch_spin.value(), dlg.workers_spin.value()) == (20, 6)
    dlg.provider_combo.setCurrentText(OLLAMA)
    assert (dlg.batch_spin.value(), dlg.workers_spin.value()) == (5, 1)


def test_dialog_engines_tab_edits_current_set_and_labels_it(
        window, monkeypatch):
    window._engine_overrides = {MV: {"batch_size": 5, "workers": 1,
                                     "cloud_batch_size": 20,
                                     "cloud_workers": 6}}
    _use(window, GEMINI, GEMINI_MODEL)
    window._apply_engine_settings(MV)
    dlg = _open_dialog(window, monkeypatch)
    assert "Online" in dlg.engine_table.horizontalHeaderItem(2).text()
    spins = {k: (b, w) for k, _c, b, w, _m in dlg._engine_spins}
    assert (spins[MV][0].value(), spins[MV][1].value()) == (20, 6)
    spins["rpgmaker_mz"][0].setValue(12)
    dlg.provider_combo.setCurrentText(OLLAMA)
    assert "Local" in dlg.engine_table.horizontalHeaderItem(2).text()
    assert (spins[MV][0].value(), spins[MV][1].value()) == (5, 1)
    dlg._save()
    assert dlg.engine_overrides["rpgmaker_mz"]["cloud_batch_size"] == 12
    assert dlg.engine_overrides[MV]["cloud_batch_size"] == 20
    assert dlg.engine_overrides[MV]["batch_size"] == 5


def test_dialog_model_change_in_cloud_still_updates_online_batch(
        window, monkeypatch):
    from translator.ai_client import MODEL_PRICING
    window._engine_overrides = {MV: {"batch_size": 5, "workers": 1}}
    _use(window, GEMINI, GEMINI_MODEL)
    window._apply_engine_settings(MV)
    dlg = _open_dialog(window, monkeypatch)
    dlg.batch_spin.setValue(9)
    model = "gpt-6-luna"
    MODEL_PRICING_BATCH = MODEL_PRICING[model]["batch_size"]
    assert MODEL_PRICING_BATCH != 9
    dlg.model_combo.setCurrentText(model)
    assert dlg.batch_spin.value() == MODEL_PRICING_BATCH


def test_dialog_local_defaults_when_no_local_set_saved(window, monkeypatch):
    window._engine_overrides = {MV: {"cloud_batch_size": 20,
                                     "cloud_workers": 6}}
    _use(window, GEMINI, GEMINI_MODEL)
    window._apply_engine_settings(MV)
    dlg = _open_dialog(window, monkeypatch)
    dlg.provider_combo.setCurrentText(OLLAMA)
    assert (dlg.batch_spin.value(), dlg.workers_spin.value()) == (
        5, LOCAL_DEFAULT_WORKERS)


def test_key_owner_round_trips_through_settings_file(window, settings_file):
    _use(window, GEMINI, GEMINI_MODEL)
    window.client.api_key, window.client.api_key_provider = "k", GEMINI
    window._save_settings()
    with open(settings_file, encoding="utf-8") as f:
        assert json.load(f)["api_key_provider"] == GEMINI
    window.client.api_key_provider = ""
    window._load_settings()
    assert window.client.api_key_provider == GEMINI


def test_old_settings_key_owner_migration(window, settings_file):
    for provider, owner in ((GEMINI, GEMINI), (OLLAMA, "")):
        with open(settings_file, "w", encoding="utf-8") as f:
            json.dump({"provider": provider, "api_key": "k"}, f)
        window._load_settings()
        assert window.client.api_key_provider == owner, provider
