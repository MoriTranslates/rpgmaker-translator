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

def test_export_after_update_reports_the_file_and_says_new_project(
        mv_game, mv_parser):
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    mv_parser.save_project(mv_game, entries)
    _update_items(mv_game)
    snapshot = [(e.id, e.original, e.translation, e.status) for e in entries]
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert ei.value.outside == ["Items.json"]
    assert not ei.value.errors and not ei.value.changed
    advice = game_io.export_advice(ei.value)
    for text in (advice, str(ei.value)):
        assert "re-open" not in text.lower() and "resume" not in text.lower()
    assert "changed outside the translator" in str(ei.value)
    assert "new project on a clean copy" in advice
    assert "Restore Originals first" in advice
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


# ── Export-time backup refresh made safe ─────────────────────────────

def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _mv_translated(mv_game, mv_parser):
    from test_smoke_findings import _set_page
    _set_page(mv_game, [(401, "一行目の台詞"), (401, "二行目の台詞")])
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    return entries


def test_mv_english_file_missing_from_backup_is_never_adopted(mv_game,
                                                               mv_parser):
    """Review H3 (related path): a data file the backup lacks whose game
    copy is our own English (1.1.0 export) is not copied into the backup
    as the "original" and not written; its lines are reported."""
    entries = _mv_translated(mv_game, mv_parser)
    mv_parser.save_project(mv_game, entries)
    os.remove(os.path.join(mv_game, "data_original", "Map001.json"))
    live = os.path.join(mv_game, "data", "Map001.json")
    before = _sha(live)
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert "Map001.json" in ei.value.changed
    assert not os.path.exists(os.path.join(mv_game, "data_original",
                                           "Map001.json"))
    assert _sha(live) == before


def test_mv_file_missing_from_backup_without_lines_is_left_alone(
        mv_game, mv_parser):
    entries = mv_parser.load_project(mv_game)
    mv_parser.save_project(mv_game, entries)
    extra = os.path.join(mv_game, "data", "Map099.json")
    with open(extra, "w", encoding="utf-8") as f:
        f.write('{"events": [], "displayName": "新しい"}')
    before = _sha(extra)
    mv_parser.save_project(mv_game, entries)
    assert _sha(extra) == before
    assert not os.path.exists(os.path.join(mv_game, "data_original",
                                           "Map099.json"))


def test_ace_english_file_missing_from_backup_is_never_adopted(tmp_path):
    from test_smoke_findings import _ace_one_map_game
    from translator.rpgmaker_ace import RPGMakerAceParser
    game, live = _ace_one_map_game(tmp_path, ["こんにちは"])
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "Hello", "reviewed"
    p.save_project(str(game), entries)
    os.remove(game / "Data_original" / "Map001.rvdata2")
    before = _sha(live)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(game), entries)
    assert "Map001.rvdata2" in ei.value.changed
    assert not (game / "Data_original" / "Map001.rvdata2").exists()
    assert _sha(live) == before


def test_renpy_english_script_missing_from_backup_is_never_adopted(tmp_path):
    from translator.renpy import RenPyParser
    game = tmp_path / "RenGame"
    (game / "renpy").mkdir(parents=True)
    (game / "game").mkdir()
    s = game / "game" / "script.rpy"
    s.write_bytes('label start:\n    e "こんにちは"\n'.encode("utf-8"))
    p = RenPyParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "Hello", "reviewed"
    p.save_project(str(game), entries)
    os.remove(game / "game_original" / "script.rpy")
    before = _sha(s)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(game), entries)
    assert "script.rpy" in ei.value.changed
    assert not (game / "game_original" / "script.rpy").exists()
    assert _sha(s) == before


# ── MV/MZ install package (review H1) ────────────────────────────────

def _zip_json(zp, arc):
    import zipfile
    with zipfile.ZipFile(zp) as zf:
        if arc not in zf.namelist():
            return None
        return json.loads(zf.read(arc).decode("utf-8"))


def test_patch_zip_ships_data_files_only_the_live_game_has(mv_game, mv_parser,
                                                           tmp_path):
    """Review H1 probe: data_original/ with no manifest, Map002.json only in
    the live game -> it was left out of the zip, silently."""
    import shutil
    data = os.path.join(mv_game, "data")
    shutil.copytree(data, data + "_original")                 # 1.1.0 backup
    shutil.copy(os.path.join(data, "Map001.json"),
                os.path.join(data, "Map002.json"))             # update adds
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        if e.file == "Map002.json":
            e.translation, e.status = "EN " + e.id, "translated"
    assert any(e.file == "Map002.json" and e.translation for e in entries)
    zp = str(tmp_path / "p.zip")
    mv_parser.export_patch_zip(mv_game, entries, zp)
    shipped = _zip_json(zp, "_translation/data/Map002.json")
    assert shipped is not None
    assert "EN Map002.json" in json.dumps(shipped, ensure_ascii=False)


def test_patch_zip_reads_an_update_without_touching_the_backup(
        mv_game, mv_parser, tmp_path):
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    mv_parser.save_project(mv_game, entries)
    _update_items(mv_game)
    data = os.path.join(mv_game, "data")
    before = _tree_hashes(data, data + "_original")
    zp = str(tmp_path / "p.zip")
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.export_patch_zip(mv_game, entries, zp)
    assert ei.value.outside == ["Items.json"]
    assert _tree_hashes(data, data + "_original") == before   # read-only
    # neither the old backup (would roll the player back) nor the changed
    # file ships: install.bat leaves the player's own Items.json alone
    assert _zip_json(zp, "_translation/data/Items.json") is None
    assert _zip_json(zp, "_translation/data/Actors.json") is not None


def test_patch_zip_counts_lines_of_a_file_the_game_no_longer_has(
        mv_game, mv_parser, tmp_path):
    entries = mv_parser.load_project(mv_game)
    entries.append(_entry("Map077.json/1/0/0", "Map077.json", "dialog",
                          "消えた", "Gone"))
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.export_patch_zip(mv_game, entries, str(tmp_path / "p.zip"))
    assert ei.value.changed == {"Map077.json": 1}


def test_install_bat_never_removes_or_renames_data():
    from translator.rpgmaker_mv import RPGMakerMVParser
    for rel in ("data", "www/data"):
        dr = rel.replace("/", "\\")
        inst = RPGMakerMVParser._build_install_bat(rel, "js", 3, True, "T")
        un = RPGMakerMVParser._build_uninstall_bat(rel, "js", True, "T")
        assert f'rmdir /S /Q "{dr}"' not in inst
        assert f'ren "{dr}"' not in inst and f'move "_translation' not in inst
        assert f'rmdir /S /Q "{dr}"' not in un


@pytest.mark.skipif(os.name != "nt", reason="needs cmd.exe")
def test_install_bat_keeps_files_the_package_does_not_have(mv_game, mv_parser,
                                                           tmp_path):
    """The player's game has a map our package lacks (a newer version):
    install and uninstall must both keep it."""
    import shutil
    import subprocess
    import zipfile
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "translated"
    zp = str(tmp_path / "p.zip")
    mv_parser.export_patch_zip(mv_game, entries, zp, game_title="T")
    player = tmp_path / "player"
    shutil.copytree(os.path.join(mv_game, "data"), player / "data")
    (player / "data" / "Map099.json").write_text('{"x": "新"}',
                                                 encoding="utf-8")
    with zipfile.ZipFile(zp) as zf:
        zf.extractall(player)
    for script in ("install.bat", "uninstall.bat"):
        r = subprocess.run(["cmd", "/c", str(player / script)], cwd=player,
                           stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=60)
        out = r.stdout.decode("utf-8", "replace")
        assert r.returncode == 0 and "FAILED" not in out, out
        assert (player / "data" / "Map099.json").is_file(), script
        if script == "install.bat":
            items = json.loads((player / "data" / "Items.json")
                               .read_text(encoding="utf-8-sig"))
            assert items[1]["name"].startswith("EN ")
            assert (player / "data_original" / "Map099.json").is_file()
    items = json.loads((player / "data" / "Items.json")
                       .read_text(encoding="utf-8-sig"))
    assert not items[1]["name"].startswith("EN ")
    assert not (player / "data_original").exists()


# ── Restore result, leftovers, detection (review M1, L1, L5) ─────────

def test_restore_reports_success_when_only_the_cleanup_fails(tmp_path,
                                                             monkeypatch):
    """Review M1: the swap succeeded, only deleting the old translated copy
    failed (locked file): that is a note, not "Restore Failed"."""
    backup, live = tmp_path / "data_original", tmp_path / "data"
    backup.mkdir()
    live.mkdir()
    (backup / "a.json").write_text("JP")
    (live / "a.json").write_text("EN")
    real_rmtree = game_io.shutil.rmtree

    def locked_rmtree(path, *a, **k):
        if str(path).endswith(".rmt-old"):
            return                                  # nothing could be deleted
        return real_rmtree(path, *a, **k)

    def no_rename(path):
        raise PermissionError("in use")
    monkeypatch.setattr(game_io.shutil, "rmtree", locked_rmtree)
    monkeypatch.setattr(game_io, "_set_aside", no_rename)
    game_io.take_cleanup_notes()
    game_io.restore_tree(str(backup), str(live))       # must not raise
    assert (live / "a.json").read_text() == "JP"
    notes = game_io.take_cleanup_notes()
    assert len(notes) == 1 and "data.rmt-old" in notes[0]
    from translator.widgets.main_window import MainWindow
    game_io._cleanup_notes.append(notes[0])
    assert "Note:" in MainWindow._restore_cleanup_text()


def test_restore_does_not_keep_our_crashed_temp_files(tmp_path):
    """Review L1: a stale *.rmt-tmp from a crashed atomic write is not
    carried into the restored folder."""
    backup, live = tmp_path / "data_original", tmp_path / "data"
    backup.mkdir()
    live.mkdir()
    (backup / "a.json").write_text("JP")
    (live / "a.json").write_text("EN")
    (live / "a.json.rmt-tmp").write_text("half")
    (live / "Map099.json").write_text("update")        # still kept
    game_io.restore_tree(str(backup), str(live))
    assert not (live / "a.json.rmt-tmp").exists()
    assert (live / "Map099.json").read_text() == "update"


def test_kirikiri_detected_after_a_killed_restore_without_writing(tmp_path):
    """Review L5: a loose-files Kirikiri game (no startup.tjs, no XP3) whose
    restore was killed mid-swap is still detected, read-only."""
    from translator.kirikiri import KirikiriParser
    game = tmp_path / "Kag"
    scen = game / "data" / "scenario"
    scen.mkdir(parents=True)
    (scen / "a.ks").write_bytes("@name chara=誰\n【太郎】こんにちは\n".encode("cp932"))
    assert KirikiriParser.is_kirikiri_project(str(game))
    os.rename(scen, str(scen) + ".rmt-old")
    (game / "data" / "scenario.rmt-old" / game_io.SWAP_MARKER).write_text("x")
    assert KirikiriParser.is_kirikiri_project(str(game))
    assert not scen.exists()                            # detection is read-only


def test_wolf_unwritable_database_is_counted_once_with_its_cause(
        tmp_path, monkeypatch):
    """Review M2: an unsupported database's lines were reported as an
    unwritten file AND counted again as "game text changed"."""
    from types import SimpleNamespace as NS
    from translator import wolfrpg
    from test_smoke_findings import _WOLF2_DAT, _wolf_game
    game = _wolf_game(tmp_path, dat=_WOLF2_DAT,
                      mps=b"\x00" * 10 + b"WOLFM\x00" + b"\x00" * 8)
    p = wolfrpg.WolfRPGParser()

    def reload(backup_dir):
        p.maps, p.common_events = [], None
        p.databases = [NS(name="DataBase")]
        p._reload_failures = []

    def export_db(db, src, dst, trans, orig=None):
        p._db_changed, p._db_visited = 0, set()
        return "LZ4-compressed DB export not supported"
    monkeypatch.setattr(p, "_reload_from_backup", reload)
    monkeypatch.setattr(p, "_export_database", export_db)
    entries = [_entry("Database/DataBase/Type0/Data0/F0", "Database/DataBase",
                      "Type0/Data0/F0", "剣", "Sword"),
               _entry("Database/DataBase/Type0/Data0/F1", "Database/DataBase",
                      "Type0/Data0/F1", "盾", "Shield")]
    for e in entries:
        e.id = f"{e.file}/{e.field}"
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(game), entries)
    assert ei.value.changed == {}
    assert len(ei.value.unsupported) == 1 and not ei.value.errors
    assert "LZ4" in ei.value.unsupported[0]
    advice = game_io.export_advice(ei.value)
    assert "can't write" in advice and "new project" not in advice


# ── Fail closed: changed outside the translator (release review H1/M1/M2) ─

def test_editor_resave_is_reported_never_adopted_and_restore_gives_japanese(
        mv_game, mv_parser):
    """Blind review H1, end to end: export, RPG Maker re-saves a JSON
    (same content, compact form), export again.  The backup is unchanged,
    the file is reported and not written, Restore gives back Japanese."""
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    mv_parser.save_project(mv_game, entries)
    data = os.path.join(mv_game, "data")
    backup_before = _tree_hashes(data + "_original")
    actors = os.path.join(data, "Actors.json")
    with open(actors, encoding="utf-8-sig") as f:
        content = json.load(f)
    with open(actors, "w", encoding="utf-8") as f:              # the editor
        json.dump(content, f, ensure_ascii=False, separators=(",", ":"))
    resaved = _sha(actors)
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert ei.value.outside == ["Actors.json"]
    assert "Restore Originals first" in game_io.export_advice(ei.value)
    assert _tree_hashes(data + "_original") == backup_before
    assert _sha(actors) == resaved                                # not written
    mv_parser.restore_originals(mv_game)
    with open(actors, encoding="utf-8-sig") as f:
        names = [a["name"] for a in json.load(f) if a]
    assert names and not any(n.startswith("EN ") for n in names)
    mv_parser.save_project(mv_game, entries)                      # complete


def test_update_that_cuts_most_japanese_is_never_rolled_back(mv_game,
                                                             mv_parser):
    """Blind review M1: an update replacing most of a map's text used to be
    "refused" and then overwritten from the old backup."""
    from test_smoke_findings import _set_page
    _set_page(mv_game, [(401, "長い日本語の台詞" * 30)])
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    mv_parser.save_project(mv_game, entries)
    path = os.path.join(mv_game, "data", "Map001.json")
    with open(os.path.join(mv_game, "data_original", "Map001.json"),
              encoding="utf-8-sig") as f:
        m = json.load(f)
    m["events"][1]["pages"][0]["list"][1]["parameters"] = ["Short."]
    m["newKey"] = 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    updated = _sha(path)
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert ei.value.outside == ["Map001.json"]
    assert _sha(path) == updated                       # update kept as is


def test_project_from_110_falls_back_to_110_behaviour(mv_game, mv_parser):
    """No manifest (a 1.1.0 backup): nothing can be judged, so the file is
    exported from the backup as 1.1.0 did -- and the export records it, so
    the next change outside is caught."""
    import shutil
    data = os.path.join(mv_game, "data")
    shutil.copytree(data, data + "_original")            # 1.1.0 backup only
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    _update_items(mv_game, rename_first=False)
    mv_parser.save_project(mv_game, entries)             # 1.1.0 behaviour
    with open(os.path.join(data, "Items.json"), encoding="utf-8-sig") as f:
        assert not any(isinstance(i, dict) and i.get("name") == "アップデート品"
                       for i in json.load(f))
    _update_items(mv_game, rename_first=False)
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert ei.value.outside == ["Items.json"]


def test_new_file_this_app_never_wrote_is_backed_up_then_written(
        mv_game, mv_parser):
    """A map added after the first backup, in a game whose manifest was
    made with the backup: provably never written by us, so its original
    is backed up before the first write."""
    import shutil
    entries = mv_parser.load_project(mv_game)
    mv_parser.save_project(mv_game, entries)
    data = os.path.join(mv_game, "data")
    shutil.copy(os.path.join(data + "_original", "Map001.json"),
                os.path.join(data, "Map002.json"))
    jp = _sha(os.path.join(data, "Map002.json"))
    new = [e for e in mv_parser.load_project(mv_game)
           if e.file == "Map002.json"]
    assert new
    for e in new:
        e.translation, e.status = "EN " + e.id, "translated"
    mv_parser.save_project(mv_game, entries + new)
    assert _sha(os.path.join(data + "_original", "Map002.json")) == jp
    assert "EN Map002" in open(os.path.join(data, "Map002.json"),
                               encoding="utf-8-sig").read()


def test_outside_check_streams_large_files(tmp_path):
    """Blind review M2: no whole-file reads or heuristics; memory stays
    flat whatever the file size."""
    import tracemalloc
    live, bak = tmp_path / "data.dts", tmp_path / "data_original.dts"
    block = os.urandom(1 << 20)
    with open(bak, "wb") as f:
        for _ in range(48):
            f.write(block)
    with open(live, "wb") as f:
        for _ in range(48):
            f.write(block)
        f.write(b"x")
    m = game_io.ExportManifest(str(bak) + ".manifest.json")
    m.files["data.dts"] = {"size": 1, "sha256": "0"}
    m.finish()
    m = game_io.ExportManifest(str(bak) + ".manifest.json")
    tracemalloc.start()
    try:
        assert game_io.changed_outside(
            [("data.dts", str(live), str(bak))], m) == ["data.dts"]
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 8 * (1 << 20), peak
    assert not hasattr(game_io, "_looks_translated")
