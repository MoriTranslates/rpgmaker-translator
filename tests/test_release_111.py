"""Release 1.1.1 hardening: no update-merge, honest messages, safe backups.

1.1.1 ships WITHOUT the "merge a game update on resume" feature (it could
overwrite the Japanese backup with English, carry a user's skip over to new
text and had no snapshot).  These tests pin the behaviour that replaces it:
a resume never touches the game, the backup or the project; an export after
a game update skips and lists the changed lines and says what to do.
"""

import hashlib
import json
import os

import pytest

from translator import game_io
from translator.project_model import TranslationEntry, TranslationProject


def _entry(eid, file, field, original, translation, status="translated"):
    return TranslationEntry(id=eid, file=file, field=field, original=original,
                            translation=translation, status=status)


def _tree_hashes(*dirs):
    """{relpath: sha256} for every file under *dirs* (missing dirs: empty)."""
    out = {}
    for d in dirs:
        for root, _sub, files in os.walk(d):
            for fn in files:
                p = os.path.join(root, fn)
                with open(p, "rb") as f:
                    out[os.path.relpath(p, os.path.dirname(d))] = \
                        hashlib.sha256(f.read()).hexdigest()
    return out


def _window(qapp, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    shown = []

    def record(kind):
        def fn(_parent, title, text, *args, **kwargs):
            shown.append((kind, title, text))
        return fn
    monkeypatch.setattr(mw.QMessageBox, "information", record("information"))
    monkeypatch.setattr(mw.QMessageBox, "warning", record("warning"))
    monkeypatch.setattr(mw.QMessageBox, "question",
                        lambda *a, **k: mw.QMessageBox.StandardButton.No)
    return mw.MainWindow(), shown


def _close(win):
    win._closing = True
    win._autosave_timer.stop()
    win.hide()


def _update_items(mv_game, rename_first=True, add=True):
    """A game update of Items.json built from the ORIGINAL file."""
    src = os.path.join(mv_game, "data_original", "Items.json")
    if not os.path.isfile(src):
        src = os.path.join(mv_game, "data", "Items.json")
    with open(src, encoding="utf-8-sig") as f:
        items = json.load(f)
    if rename_first:
        items[1]["name"] = "ハイポーション"
    if add:
        items.append({"id": len(items), "name": "アップデート品",
                      "description": "", "note": ""})
    with open(os.path.join(mv_game, "data", "Items.json"), "w",
              encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)


# ── Resume never re-reads the game into the project ──────────────────

@pytest.mark.parametrize("exported_first", [True, False])
def test_resume_after_game_update_changes_nothing(
        qapp, monkeypatch, tmp_path, mv_game, mv_parser, exported_first):
    """A resumed project is exactly the saved one; no game file, backup or
    manifest is written; no "merged" dialog.  (Old code: merged the update,
    reset the changed line and refreshed data_original/ on resume.)"""
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    if exported_first:
        mv_parser.save_project(mv_game, entries)
    proj = TranslationProject(project_path=mv_game, project_type="rpgmaker_mv",
                              entries=entries)
    state = os.path.join(mv_game, "_translation_state.json")
    proj.save_state(state)
    saved = [(e.id, e.original, e.translation, e.status) for e in entries]
    _update_items(mv_game)
    data = os.path.join(mv_game, "data")
    before = _tree_hashes(data, data + "_original")
    manifest = data + "_original.manifest.json"
    manifest_before = open(manifest, "rb").read() if exported_first else None

    win, shown = _window(qapp, monkeypatch, tmp_path)
    try:
        assert win._restore_from_state(state)
        now = [(e.id, e.original, e.translation, e.status)
               for e in win.project.entries]
    finally:
        _close(win)
    assert now == saved
    assert _tree_hashes(data, data + "_original") == before
    if exported_first:
        assert open(manifest, "rb").read() == manifest_before
    assert not any("Merged" in title or "Merge" in title
                   for _k, title, _t in shown)


def test_parsers_have_no_merge_entry_points():
    """The resume merge's per-engine hooks are gone with the feature."""
    from translator import (crowd, csv_game, kirikiri, renpy, rpgmaker_2k,
                            rpgmaker_ace, rpgmaker_mv, srpgstudio,
                            tyranoscript, wolfrpg)
    parsers = [crowd.CrowdParser, csv_game.CSVGameParser,
               kirikiri.KirikiriParser, renpy.RenPyParser,
               rpgmaker_2k.RPGMaker2KParser, rpgmaker_ace.RPGMakerAceParser,
               rpgmaker_mv.RPGMakerMVParser, srpgstudio.SRPGStudioParser,
               tyranoscript.TyranoScriptParser, wolfrpg.WolfRPGParser]
    for cls in parsers:
        assert not hasattr(cls, "load_originals"), cls
        assert not hasattr(cls, "refresh_backups"), cls
    assert not hasattr(TranslationProject, "merge_fresh_originals")
    assert not hasattr(game_io, "refresh_on_resume")


# ── Honest messages after a game update ──────────────────────────────

def test_export_after_update_skips_changed_lines_and_says_new_project(
        mv_game, mv_parser):
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    mv_parser.save_project(mv_game, entries)
    _update_items(mv_game)
    snapshot = [(e.id, e.original, e.translation, e.status) for e in entries]
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert ei.value.changed == {"Items.json": 1}
    assert not ei.value.errors
    note = " ".join(mv_parser.last_export_notes)
    advice = game_io.export_advice(ei.value)
    for text in (note, advice, str(ei.value)):
        assert "re-open" not in text.lower() and "resume" not in text.lower()
    assert "new project" in note and "new project" in advice
    assert "skipped" in advice
    # the project itself is untouched by the export
    assert [(e.id, e.original, e.translation, e.status)
            for e in entries] == snapshot
    with open(os.path.join(mv_game, "data", "Items.json"),
              encoding="utf-8-sig") as f:
        assert json.load(f)[1]["name"] == "ハイポーション"   # game's text kept


def test_export_advice_is_chosen_by_cause_not_by_wording():
    changed = game_io.PartialExportError(["x"], errors=[],
                                         changed={"Map001.json": 2})
    locked = game_io.PartialExportError(["Map002.json: [Errno 13] denied"])
    unsafe = game_io.PartialExportError(["s"], errors=[], unsafe=["s"])
    unsupported = game_io.PartialExportError(["d"], errors=[],
                                             unsupported=["d"])
    a = game_io.export_advice(changed)
    assert "new project" in a and "close the game" not in a
    b = game_io.export_advice(locked)
    assert "close the game" in b and "new project" not in b
    c = game_io.export_advice(unsafe)
    assert "edit them" in c and "close the game" not in c
    d = game_io.export_advice(unsupported)
    assert "can't write" in d and "close the game" not in d


def test_renpy_unsafe_line_gets_edit_advice(tmp_path, monkeypatch):
    """Review M3: Ren'Py's "would break the script line" problem used to
    fall in the "close the game, free disk space" bucket."""
    from translator import renpy
    game = tmp_path / "RenGame"
    (game / "renpy").mkdir(parents=True)
    (game / "game").mkdir()
    (game / "game" / "script.rpy").write_bytes(
        'label start:\n    e "こんにちは"\n'.encode("utf-8"))
    p = renpy.RenPyParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = 'broken " quote', "translated"
    monkeypatch.setattr(renpy, "_escape_rpy", lambda text, original="": text)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(game), entries)
    assert ei.value.unsafe and not ei.value.errors
    advice = game_io.export_advice(ei.value)
    assert "edit them" in advice and "close the game" not in advice


def test_no_reopen_advice_left_in_the_app():
    """No user-facing text may tell the user to re-open / resume the
    project to pick up a game update: nothing does that any more."""
    root = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "translator")
    bad = []
    for dirpath, _d, files in os.walk(root):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as f:
                text = f.read().lower()
            for phrase in ("re-open the project", "pick up the new",
                           "pick up any new", "resume it to pick up",
                           "merges on reopen"):
                if phrase in text:
                    bad.append(f"{fn}: {phrase}")
    assert not bad, bad


def test_recovery_note_is_shown_on_resume_with_its_own_title(
        qapp, monkeypatch, tmp_path, mv_game, mv_parser):
    """An interrupted Restore Originals put back while resuming is told
    as such -- not as "Some Game Files Could Not Be Read"."""
    entries = mv_parser.load_project(mv_game)
    proj = TranslationProject(project_path=mv_game, project_type="rpgmaker_mv",
                              entries=entries)
    state = os.path.join(mv_game, "_translation_state.json")
    proj.save_state(state)
    data = os.path.join(mv_game, "data")
    os.rename(data, data + ".rmt-old")                  # killed mid-swap
    with open(os.path.join(data + ".rmt-old", game_io.SWAP_MARKER), "w") as f:
        f.write("x")
    game_io.take_recovery_notes()
    win, shown = _window(qapp, monkeypatch, tmp_path)
    try:
        assert win._restore_from_state(state)
    finally:
        _close(win)
    assert os.path.isdir(data)
    titles = [t for _k, t, _x in shown]
    assert "Interrupted Restore Recovered" in titles
    assert "Some Game Files Could Not Be Read" not in titles


def test_recovery_note_of_another_game_is_not_shown(tmp_path):
    """Review L2: a recovery inside one game (e.g. during a patch zip) must
    not pop up when a different game is opened later."""
    a, b = tmp_path / "GameA", tmp_path / "GameB"
    for g in (a, b):
        (g / "data").mkdir(parents=True)
    os.rename(a / "data", a / "data.rmt-old")
    (a / "data.rmt-old" / game_io.SWAP_MARKER).write_text("x")
    game_io.take_recovery_notes()
    assert game_io.recover_interrupted_restore(str(a / "data"))
    assert game_io.take_recovery_notes(str(b)) == []
    assert game_io.take_recovery_notes(str(a)) == []     # already cleared
    os.rename(a / "data", a / "data.rmt-old")
    (a / "data.rmt-old" / game_io.SWAP_MARKER).write_text("x")
    assert game_io.recover_interrupted_restore(str(a / "data"))
    assert len(game_io.take_recovery_notes(str(a))) == 1
