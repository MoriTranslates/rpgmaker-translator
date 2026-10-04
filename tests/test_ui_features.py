"""UI features: recent projects, bulk-edit undo, file-tree keyboard
filtering, GPU monitor auto-hide, model recommendation order."""

import json
import os

import pytest
from PyQt6.QtCore import QByteArray, QProcess, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QMessageBox, QWidget, QVBoxLayout

from translator.project_model import TranslationEntry, TranslationProject


# ── helpers ────────────────────────────────────────────────────────

def _entry(eid, file, original, translation="", status="untranslated",
           field="dialog"):
    return TranslationEntry(id=eid, file=file, field=field, original=original,
                            translation=translation, status=status)


@pytest.fixture
def settings_file(tmp_path):
    path = tmp_path / "_settings.json"
    path.write_text("{}", encoding="utf-8")  # exists → no first-launch dialog
    return str(path)


@pytest.fixture
def make_window(qapp, monkeypatch, settings_file):
    """Factory for MainWindows that use a temp settings file, no network."""
    from translator.widgets import main_window as mw
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", settings_file)
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    windows = []

    def make():
        win = mw.MainWindow()
        win._closing = True
        windows.append(win)
        return win

    yield make
    # Deleting MainWindows mid-session (deleteLater outside a running event
    # loop) can crash at interpreter exit — keep them alive, just idle.
    for win in windows:
        win._autosave_timer.stop()
        win.hide()
    _KEEP_ALIVE.extend(windows)


_KEEP_ALIVE: list = []


def _load_entries(win, entries):
    win.project = TranslationProject(project_path="", entries=entries)
    win.file_tree.load_project(win.project)
    win.trans_table.set_entries(entries)
    win.event_viewer.set_entries(entries)
    win._enable_project_actions()


# ── 1. Recent projects ────────────────────────────────────────────

def test_recent_projects_order_dedupe_cap_persist(make_window, tmp_path,
                                                  settings_file):
    dirs = []
    for i in range(12):
        d = tmp_path / f"game{i:02d}"
        d.mkdir()
        dirs.append(str(d))

    win = make_window()
    for d in dirs:
        win._add_recent_project(d)
    # Re-open an older one with a different spelling of the same path
    alias = dirs[5].replace(os.sep, "/") + "/"
    if os.name == "nt":
        alias = alias.upper()
    win._add_recent_project(alias)

    recent = win._recent_projects
    assert len(recent) == 10                      # capped
    assert os.path.normcase(recent[0]) == os.path.normcase(dirs[5])  # newest first
    assert recent[1] == os.path.normpath(dirs[11])
    keys = [os.path.normcase(p) for p in recent]
    assert len(set(keys)) == len(keys)            # de-duplicated
    assert all(p == os.path.normpath(p) for p in recent)  # normalized
    assert os.path.normpath(dirs[0]) not in recent  # oldest dropped

    # Persisted to the (temp) settings file, other keys untouched
    with open(settings_file, encoding="utf-8") as f:
        cfg = json.load(f)
    assert cfg["recent_projects"] == recent

    # Welcome page + Open Recent submenu show them
    assert win._welcome.recent_list.count() == 10
    actions = [a for a in win.recent_menu.actions() if a.toolTip() in recent]
    assert len(actions) == 10

    # A folder that disappeared is hidden from the display lists
    os.rmdir(recent[2])
    gone = recent[2]
    win._refresh_recent_ui()
    assert win._welcome.recent_list.count() == 9
    shown = [win._welcome.recent_list.item(i).data(Qt.ItemDataRole.UserRole)
             for i in range(win._welcome.recent_list.count())]
    assert gone not in shown

    # A fresh window reads the list back
    win2 = make_window()
    assert win2._recent_projects == recent
    assert win2._welcome.recent_list.count() == 9

    win2._clear_recent_projects()
    assert win2._recent_projects == []
    assert win2._welcome._recent_box.isHidden()
    with open(settings_file, encoding="utf-8") as f:
        assert json.load(f)["recent_projects"] == []


def test_recent_click_goes_through_open_path(make_window, tmp_path,
                                             monkeypatch):
    d = tmp_path / "somegame"
    d.mkdir()
    win = make_window()
    win._add_recent_project(str(d))
    opened = []
    monkeypatch.setattr(win, "_open_project_path", opened.append)
    win._welcome.recent_selected.emit(win._recent_projects[0])
    assert opened == [os.path.normpath(str(d))]


def test_recent_full_save_settings_keeps_list(make_window, tmp_path,
                                              settings_file):
    d = tmp_path / "g"
    d.mkdir()
    win = make_window()
    win._add_recent_project(str(d))
    win._save_settings()  # full settings write must keep the list
    with open(settings_file, encoding="utf-8") as f:
        assert json.load(f)["recent_projects"] == [os.path.normpath(str(d))]


# ── 2. Undo for bulk edits ────────────────────────────────────────

def test_undo_replace_all_round_trip(make_window, monkeypatch):
    from translator.widgets import translation_table as tt
    entries = [
        _entry("a", "Map001.json", "あ", "Hello Bob", "translated"),
        _entry("b", "Map001.json", "い", "Bob", "reviewed"),
        _entry("c", "Map002.json", "う", "Nothing here", "translated"),
    ]
    win = make_window()
    _load_entries(win, entries)
    assert not win.undo_bulk_action.isEnabled()

    monkeypatch.setattr(tt.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    win.trans_table._find_edit.setText("Bob")
    win.trans_table._replace_edit.setText("")
    win.trans_table._replace_all()

    assert entries[0].translation == "Hello "
    assert (entries[1].translation, entries[1].status) == ("", "untranslated")
    assert win.undo_bulk_action.isEnabled()
    assert "Replace All" in win.undo_bulk_action.toolTip()

    win._undo_last_bulk()
    assert (entries[0].translation, entries[0].status) == ("Hello Bob", "translated")
    assert (entries[1].translation, entries[1].status) == ("Bob", "reviewed")
    assert entries[2].translation == "Nothing here"
    assert not win.undo_bulk_action.isEnabled()
    assert win.statusbar.currentMessage() == "Undid: Replace All (2 entries)"


def test_undo_apply_glossary_round_trip(make_window, monkeypatch):
    from translator.widgets import main_window as mw
    entries = [
        _entry("n", "Actors.json", "勇者", "Brave", "translated", field="name"),
        _entry("d", "Map001.json", "勇者が来た", "The Brave came", "translated"),
        _entry("r", "Map001.json", "勇者だ", "It's the Brave", "reviewed"),
    ]
    win = make_window()
    _load_entries(win, entries)
    win.client.glossary = {"勇者": "Hero"}

    def fake_exec(box):
        for btn in box.buttons():
            if btn.text() == "Apply":
                btn.click()
                return 0
        raise AssertionError("no Apply button")

    monkeypatch.setattr(mw.QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(mw.QMessageBox, "information", lambda *a, **k: None)
    win._apply_glossary()

    assert entries[0].translation == "Hero"
    assert entries[1].translation == "The Hero came"
    assert entries[2].translation == "It's the Brave"  # reviewed untouched
    assert win.undo_bulk_action.isEnabled()

    win._undo_last_bulk()
    assert entries[0].translation == "Brave"
    assert entries[1].translation == "The Brave came"
    assert win.statusbar.currentMessage() == "Undid: Apply Glossary (2 entries)"


def test_undo_mark_event_reviewed_and_cancelled_op(make_window, monkeypatch):
    from translator.widgets import main_window as mw
    entries = [
        _entry("Map001.json/Ev1/p0/0", "Map001.json", "あ", "A", "translated"),
        _entry("Map001.json/Ev1/p0/1", "Map001.json", "い", "B", "translated"),
    ]
    win = make_window()
    _load_entries(win, entries)
    win.event_viewer._current_entries = list(entries)
    win.event_viewer._mark_event_reviewed()
    assert all(e.status == "reviewed" for e in entries)
    assert win.undo_bulk_action.isEnabled()

    # A cancelled bulk op records nothing and keeps the previous undo
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    win._reset_all_for_retranslation()
    assert win._undo_snapshot[0] == "Mark Event Reviewed"

    win._undo_last_bulk()
    assert all(e.status == "translated" for e in entries)


def test_undo_cleared_by_engine_run_and_project_close(make_window, monkeypatch):
    from translator.widgets import main_window as mw
    entries = [_entry("x", "Map001.json", "あ", "Some text", "translated")]
    win = make_window()
    _load_entries(win, entries)
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)

    win._reset_all_for_retranslation()
    assert entries[0].status == "untranslated"
    assert win.undo_bulk_action.isEnabled()

    win._begin_run("batch")   # batch changes aren't undoable
    assert not win.undo_bulk_action.isEnabled()
    win._reset_batch_ui()

    entries[0].translation, entries[0].status = "Some text", "translated"
    win._reset_all_for_retranslation()
    assert win.undo_bulk_action.isEnabled()
    win._reset_project_runtime_state()  # open/close/load path
    assert win._undo_snapshot is None
    assert not win.undo_bulk_action.isEnabled()


# ── 3. File tree keyboard / selection filtering ───────────────────

def test_file_tree_selection_filters_once(make_window):
    entries = [
        _entry("a", "Map001.json", "あ"),
        _entry("b", "Map001.json", "い"),
        _entry("c", "Map002.json", "う"),
    ]
    win = make_window()
    _load_entries(win, entries)
    tree = win.file_tree
    emitted = []
    tree.file_selected.connect(emitted.append)

    def find(name):
        from PyQt6.QtWidgets import QTreeWidgetItemIterator
        it = QTreeWidgetItemIterator(tree)
        while it.value():
            if it.value().data(0, Qt.ItemDataRole.UserRole) == name:
                return it.value()
            it += 1
        raise AssertionError(name)

    # Selection change (keyboard / programmatic) filters the table
    maps = find("Map001.json").parent()
    maps.setExpanded(True)
    tree.setCurrentItem(find("Map002.json"))
    assert emitted == ["Map002.json"]
    assert [e.id for e in win.trans_table._entries] == ["c"]

    # Arrow key moves to the previous file and filters again
    tree.show()
    tree.setFocus()
    QTest.keyClick(tree, Qt.Key.Key_Up)
    assert emitted[-1] == "Map001.json"
    assert {e.id for e in win.trans_table._entries} == {"a", "b"}

    # A mouse click changes selection AND clicks — must filter only once
    emitted.clear()
    item = find("Map002.json")
    rect = tree.visualItemRect(item)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                     pos=rect.center())
    assert emitted == ["Map002.json"]
    tree.hide()


# ── 4. GPU monitor hides without nvidia-smi ───────────────────────

def test_gpu_monitor_hidden_when_nvidia_smi_missing(qapp, monkeypatch):
    from translator.widgets import gpu_monitor
    monkeypatch.setattr(gpu_monitor, "_CMD",
                        ["definitely-not-nvidia-smi-xyz", "--version"])
    host = QWidget()
    lay = QVBoxLayout(host)
    panel = gpu_monitor.GPUMonitorPanel(poll_ms=60_000)
    lay.addWidget(panel)
    host.show()  # showEvent polls immediately
    for _ in range(100):
        if panel.isHidden():
            break
        QTest.qWait(20)
    assert panel.isHidden()
    assert not panel._timer.isActive()
    host.hide()
    host.show()          # re-showing the parent must not bring it back
    assert panel.isHidden()
    host.deleteLater()


def test_gpu_monitor_nonzero_exit_without_gpu_hides(qapp):
    from translator.widgets.gpu_monitor import GPUMonitorPanel
    panel = GPUMonitorPanel(poll_ms=60_000)
    panel._timer.stop()
    panel._on_proc_finished(9, QProcess.ExitStatus.NormalExit)
    assert panel.isHidden()
    assert not panel._timer.isActive()
    panel.deleteLater()


def test_gpu_monitor_stays_visible_on_nvidia(qapp, monkeypatch):
    from translator.widgets.gpu_monitor import GPUMonitorPanel
    panel = GPUMonitorPanel(poll_ms=60_000)
    panel._timer.stop()
    panel.show()
    monkeypatch.setattr(
        panel._proc, "readAllStandardOutput",
        lambda: QByteArray(b"NVIDIA GeForce RTX 4070 Ti, 6000, 12282, 37, 55, 120.5\n"))
    panel._on_proc_finished(0, QProcess.ExitStatus.NormalExit)
    assert panel.is_available and panel.isVisible()
    # A later transient failure keeps the panel (shows N/A)
    panel._on_proc_finished(1, QProcess.ExitStatus.NormalExit)
    assert panel.isVisible()
    panel.hide()
    panel.deleteLater()


# ── 5. Model recommendation order ─────────────────────────────────

def test_model_suggestion_recommends_qwen35_first(qapp, monkeypatch):
    from translator.widgets import model_suggestion_dialog as msd
    monkeypatch.setattr(msd, "_detect_gpu", lambda: (None, 0))
    dlg = msd.ModelSuggestionDialog(installed_models=[])
    assert dlg._get_selected_tag() == "qwen3.5:9b"
    first = dlg._qwen_table.item(0, 0)
    assert "Recommended" in first.text()
    # Qwen3.5 group sits above the Sugoi group
    layout = dlg.layout()
    groups = [layout.itemAt(i).widget() for i in range(layout.count())
              if layout.itemAt(i).widget() is not None]
    titles = [g.title() for g in groups if hasattr(g, "title")]
    assert titles[0].startswith("Qwen3.5")
    assert "Sugoi" in titles[1]
    dlg.deleteLater()


def test_images_tab_renamed(make_window):
    win = make_window()
    names = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert "Images (Beta)" in names
