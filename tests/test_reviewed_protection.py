"""Entries the user marked "reviewed" are never changed by automatic passes,
and late LLM results never overwrite edits made while they were in flight.
"""

import pytest

from translator.project_model import TranslationEntry, TranslationProject


def _e(eid, original, translation, status, field="dialog", file="Map001.json"):
    return TranslationEntry(id=eid, file=file, field=field, original=original,
                            translation=translation, status=status)


# ── post-processing ──────────────────────────────────────────────────

def test_post_processing_never_clears_reviewed_entries():
    from translator.post_processor import run_post_processing
    reviewed = [
        _e("a", "命令に従う。", "As an AI, I obey your orders.", "reviewed"),
        _e("b", "行こう、と彼は言った。", '"Let\'s go," he said.', "reviewed"),
    ]
    before = [(x.translation, x.status) for x in reviewed]
    result = run_post_processing(reviewed)
    assert [(x.translation, x.status) for x in reviewed] == before
    assert not result.retranslate_ids


def test_post_processing_still_fixes_unreviewed_refusals():
    from translator.post_processor import run_post_processing
    e = _e("a", "命令に従う。", "As an AI, I cannot translate this.", "translated")
    result = run_post_processing([e])
    assert e.status == "untranslated" and "a" in result.retranslate_ids


# ── view must not demote ─────────────────────────────────────────────

def test_table_update_entry_keeps_reviewed_status(qapp):
    from translator.widgets.translation_table import TranslationTable
    t = TranslationTable()
    e = _e("Map001.json/Ev1/p0/dialog_1", "こんにちは", "Hello", "reviewed")
    t.set_entries([e])
    t.update_entry(e.id, "Hello there")
    assert e.status == "reviewed"
    assert e.translation == "Hello there"


# ── MainWindow: late results, polish selection, code restore ─────────

@pytest.fixture
def window(qapp, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    win = mw.MainWindow()
    win._closing = False
    yield win
    win._closing = True
    win._autosave_timer.stop()
    win.hide()
    _KEEP.append(win)


_KEEP: list = []


def _load(win, entries):
    win.project = TranslationProject(project_path="", entries=entries)
    win._batch_project = win.project
    win.file_tree.load_project(win.project)
    win.trans_table.set_entries(entries)
    win.event_viewer.set_entries(entries)


def test_late_result_does_not_overwrite_edit_made_in_flight(window):
    e = _e("Map001.json/Ev1/p0/dialog_1", "こんにちは", "", "untranslated")
    _load(window, [e])
    window.engine.dispatch_state = {e.id: (e.translation, e.status)}
    # User types their own translation and marks it reviewed meanwhile
    e.translation, e.status = "Hi there!", "reviewed"
    window._on_entry_done(e.id, "Hello (LLM)")
    assert (e.translation, e.status) == ("Hi there!", "reviewed")


def test_result_applies_when_entry_unchanged(window):
    e = _e("Map001.json/Ev1/p0/dialog_1", "こんにちは", "", "untranslated")
    _load(window, [e])
    window.engine.dispatch_state = {e.id: (e.translation, e.status)}
    window._on_entry_done(e.id, "Hello (LLM)")
    assert (e.translation, e.status) == ("Hello (LLM)", "translated")


def test_polish_result_keeps_reviewed_status(window, monkeypatch):
    """Even a polish result that does land (explicit request) never demotes."""
    e = _e("Map001.json/Ev1/p0/dialog_1", "こんにちは", "hello", "reviewed")
    _load(window, [e])
    monkeypatch.setattr(window, "_is_polish_run", lambda: True)
    window.engine.dispatch_state = {e.id: (e.translation, e.status)}
    window._on_entry_done(e.id, "Hello.")
    assert e.status == "reviewed"


def test_bulk_polish_skips_reviewed_entries(window, monkeypatch):
    from translator.widgets import main_window as mw
    rev = _e("Map001.json/Ev1/p0/dialog_1", "こんにちは", "hello", "reviewed")
    tr = _e("Map001.json/Ev1/p0/dialog_2", "さようなら", "bye", "translated")
    _load(window, [rev, tr])
    sent = {}
    monkeypatch.setattr(window, "_check_engine_idle", lambda: True)
    monkeypatch.setattr(window, "_ensure_ollama_ready", lambda: True)
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window.engine, "polish_batch",
                        lambda entries: sent.setdefault("ids", [x.id for x in entries]))
    window._polish_translations()
    assert sent["ids"] == [tr.id]


def test_restore_missing_codes_leaves_reviewed_alone(window):
    rev = _e("Map001.json/Ev1/p0/dialog_1", "\\C[2]こんにちは\\C[0]", "Hello", "reviewed")
    tr = _e("Map001.json/Ev1/p0/dialog_2", "\\C[2]さようなら\\C[0]", "Bye", "translated")
    _load(window, [rev, tr])
    window._restore_missing_codes()
    assert rev.translation == "Hello"
    assert "\\C[2]" in tr.translation
