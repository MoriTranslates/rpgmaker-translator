"""Offscreen widget tests for TranslationTable / EventViewer / QueuePanel."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from translator.project_model import TranslationEntry  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _entries():
    return [
        TranslationEntry("Map001.json/Ev1/p0/dialog_0", "Map001.json", "dialog",
                         "こんにちは", "Hello", "translated"),
        TranslationEntry("Map001.json/Ev1/p0/dialog_1", "Map001.json", "dialog",
                         "さようなら", "", "untranslated"),
        TranslationEntry("Map001.json/Ev1/p0/dialog_2", "Map001.json", "dialog",
                         "ありがとう", "Thanks", "translated"),
        TranslationEntry("Map002.json/Ev1/p0/dialog_0", "Map002.json", "dialog",
                         "こんにちは", "Hello", "translated"),
    ]


def _select(table, row):
    table.table.setCurrentIndex(table._model.index(row, 0))


def test_filter_keeps_selected_entry(app):
    from translator.widgets.translation_table import TranslationTable
    t = TranslationTable()
    entries = _entries()
    t.set_entries(entries)
    _select(t, 2)
    target = entries[2]
    assert t._visible_entries[t._selected_row] is target

    # Filter that drops rows above the selection — row index shifts
    t.status_filter.setCurrentText("Translated")
    assert t._visible_entries[t._selected_row] is target
    assert t.trans_editor.toPlainText() == "Thanks"

    # Typing in the editor must edit the selected entry, not a neighbour
    t.trans_editor.setPlainText("Thank you")
    assert target.translation == "Thank you"
    assert entries[0].translation == "Hello"


def test_filter_hiding_selection_clears_editor(app):
    from translator.widgets.translation_table import TranslationTable
    t = TranslationTable()
    entries = _entries()
    t.set_entries(entries)
    _select(t, 1)  # untranslated entry
    t.status_filter.setCurrentText("Translated")
    assert t._selected_row == -1
    assert t.trans_editor.toPlainText() == ""
    t.trans_editor.setPlainText("oops")
    assert all(e.translation != "oops" for e in entries)


def test_empty_id_filter_shows_nothing(app):
    from translator.widgets.translation_table import TranslationTable
    t = TranslationTable()
    t.set_entries(_entries())
    t.set_id_filter(set())
    assert t._visible_entries == []


def test_master_view_counts_whole_project(app):
    from translator.widgets.translation_table import TranslationTable
    t = TranslationTable()
    entries = _entries()
    t.set_entries(entries)
    t.filter_by_file([e for e in entries if e.file == "Map001.json"])
    t.master_check.setChecked(True)
    assert t._dupe_counts["こんにちは"] == 2


def test_refresh_all_does_not_emit_status_changed(app):
    from translator.widgets.translation_table import TranslationTable
    t = TranslationTable()
    t.set_entries(_entries())
    hits = []
    t.status_changed.connect(lambda: hits.append(1))
    t._model.refresh_all()
    t._status_timer.stop()
    app.processEvents()
    assert not t._status_timer.isActive()
    assert hits == []


def test_event_viewer_editor_tracks_new_event(app):
    from translator.widgets.event_viewer import EventViewerPanel
    ev = EventViewerPanel()
    entries = _entries()
    ev.set_entries(entries)
    prefixes = list(ev._event_groups)
    assert len(prefixes) == 2
    ev._show_event(prefixes[0])
    ev._show_event(prefixes[1])
    # Editor must show the new event's first row, and edits go there
    assert ev._trans_editor.toPlainText() == ev._current_entries[0].translation
    ev._trans_editor.setPlainText("Hi there")
    assert ev._current_entries[0].translation == "Hi there"
    assert entries[0].translation == "Hello"


def test_event_viewer_mark_reviewed_skips_empty(app):
    from translator.widgets.event_viewer import EventViewerPanel
    ev = EventViewerPanel()
    entries = _entries()
    ev.set_entries(entries)
    ev._show_event(list(ev._event_groups)[0])
    ev._mark_event_reviewed()
    assert entries[0].status == "reviewed"
    assert entries[1].status == "untranslated"


def test_queue_panel_counts(app):
    from translator.widgets.queue_panel import QueuePanel
    q = QueuePanel()
    entries = _entries()
    q.load_queue(entries)
    q.mark_entry_done(entries[0].id, "Hello")
    q.mark_entry_done(entries[0].id, "Hello")  # double-count guard
    q.mark_entry_error(entries[1].id, "boom")
    total = sum(c["done"] for k, c in q._event_counts.items())
    errors = sum(c["error"] for k, c in q._event_counts.items())
    assert total == 1
    assert errors == 1
