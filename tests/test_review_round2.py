"""Regression tests from the second review round (85054f3..babd7e0).

The first 12 are the blind reviewer's repros, kept as written: each one
asserts the correct behaviour and failed on babd7e0.
"""

import json
import os
from types import SimpleNamespace as NS

import pytest

import ruby_marshal_check as rmc
from translator.project_model import TranslationEntry, TranslationProject


def _entry(eid, file, field, original, translation, status="translated"):
    return TranslationEntry(id=eid, file=file, field=field, original=original,
                            translation=translation, status=status)


# 1. Marshal: Class / Module objects take an object-table slot in Ruby
#    (reader r_entry for 'c' and 'm'); the writer does not reserve one.
#    [Foo(class), "x"(ivar string), @2]  -> objects: arr=0, Foo=1, "x"=2
_CLASS_THEN_LINK = b'\x04\x08[\x08c\x08FooI"\x06x\x06:\x06ET@\x07'
_MODULE_THEN_LINK = b'\x04\x08[\x08m\x08FooI"\x06x\x06:\x06ET@\x07'


@pytest.mark.parametrize("blob", [_CLASS_THEN_LINK, _MODULE_THEN_LINK])
def test_marshal_link_after_class_or_module(blob):
    from translator.ruby_marshal import loads, writes
    before = rmc.plain(rmc.load(blob))
    assert before[1][2] == ("str", b"x")           # oracle: link -> "x"
    out = writes(loads(blob))
    after = rmc.plain(rmc.load(out))
    assert after[1][2] == ("str", b"x"), after      # BUG: link -> the class


# 2. Ren'Py: lines with a `with` / `id` / `nointeract` tail are now dialogue,
#    so every later line in that label is renumbered.  A project saved by
#    85054f3 (verified: the old _classify_line returns None for
#    `e "..." with dissolve`) loses its reviewed lines on resume.
def test_renpy_upgrade_renumbering_resets_reviewed_lines(tmp_path):
    from translator.renpy import RenPyParser
    game = tmp_path / "RenGame"
    (game / "renpy").mkdir(parents=True)
    (game / "game").mkdir()
    script = ('label start:\n'
              '    e "こんにちは" with dissolve\n'
              '    e "元気？"\n'
              '    e "さようなら"\n'
              'label other:\n' +
              ''.join(f'    e "台詞{i}"\n' for i in range(10)))
    (game / "game" / "script.rpy").write_bytes(script.encode("utf-8"))

    # IDs exactly as the 85054f3 parser produced them (tail line skipped)
    old = [_entry("script.rpy/start/dialog_0", "script.rpy", "dialog",
                  "元気？", "How are you?", "reviewed"),
           _entry("script.rpy/start/dialog_1", "script.rpy", "dialog",
                  "さようなら", "Goodbye", "reviewed")]
    old += [_entry(f"script.rpy/other/dialog_{i}", "script.rpy", "dialog",
                   f"台詞{i}", f"Line {i}", "reviewed") for i in range(10)]
    proj = TranslationProject(project_path=str(game), entries=old)
    stats = proj.merge_fresh_originals(RenPyParser().load_originals(str(game)))
    by = {e.id: e for e in proj.entries}
    # The game did not change at all; only the app was upgraded.
    assert stats["changed"] == 0, stats                       # BUG: 2
    assert by["script.rpy/start/dialog_1"].status == "reviewed"  # BUG: reset


# 3. Merge is purely by positional ID: a game update that inserts one line
#    resets every later translated line, even though its text is unchanged.
def test_merge_shifted_ids_keep_translation_of_unchanged_text():
    proj = TranslationProject(project_path="", entries=[
        _entry("M/Ev1/p0/msg1", "M", "msg", "おはよう", "Morning", "reviewed"),
        _entry("M/Ev1/p0/msg2", "M", "msg", "こんばんは", "Evening", "reviewed")]
        + [_entry(f"X/{i}", "X", "msg", f"他{i}", f"o{i}") for i in range(10)])
    fresh = [_entry("M/Ev1/p0/msg1", "M", "msg", "追加された台詞", "", "untranslated"),
             _entry("M/Ev1/p0/msg2", "M", "msg", "おはよう", "", "untranslated"),
             _entry("M/Ev1/p0/msg3", "M", "msg", "こんばんは", "", "untranslated")]
    proj.merge_fresh_originals(fresh)
    by = {e.id: e for e in proj.entries}
    assert by["M/Ev1/p0/msg2"].translation == "Morning"   # BUG: "" (orphaned)
    assert by["M/Ev1/p0/msg3"].translation == "Evening"   # BUG: "" (new, empty)


# 4. Wolf: the loose "message ends with the original" check accepts a
#    changed line, writes a half-Japanese message and does not count it.
def test_wolf_changed_message_ending_with_old_original_is_skipped():
    from translator import wolfrpg
    p = wolfrpg.WolfRPGParser()
    cmds = [NS(code=wolfrpg.CMD_MESSAGE,
               string_args=["それは違うと思う。\nはい"])]   # updated line
    tr = {"M/Ev1/p0/msg0": "Yes"}
    orig = {"M/Ev1/p0/msg0": "はい"}                     # old line was "はい"
    changed = {}
    p._apply_commands(cmds, "M/Ev1/p0", tr, orig, "M", changed)
    assert cmds[0].string_args[0] == "それは違うと思う。\nはい", cmds[0].string_args
    assert changed == {"M": 1}                            # BUG: {} and mixed text


# 5. MV patch zip ignores the skipped-line count: "complete" with lines unwritten.
def test_mv_patch_zip_reports_skipped_lines(mv_game, mv_parser, tmp_path):
    from translator import game_io
    path = os.path.join(mv_game, "data", "Map001.json")
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    data["events"][1]["pages"][0]["list"] = [
        {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2, ""]},
        {"code": 401, "indent": 0, "parameters": ["古い台詞"]},
        {"code": 0, "indent": 0, "parameters": []}]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        if e.original == "古い台詞":
            e.translation, e.status = "Old line", "translated"
    data["events"][1]["pages"][0]["list"][1]["parameters"] = ["新しい台詞"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    with pytest.raises(game_io.PartialExportError):        # BUG: no error
        mv_parser.export_patch_zip(mv_game, entries, str(tmp_path / "p.zip"))


# 6. MV: speaker_processing is not persisted; a resume with a different
#    value re-parses every namebox line differently and resets it.
def test_mv_merge_with_different_speaker_setting_keeps_work(mv_game, mv_parser):
    path = os.path.join(mv_game, "data", "Map001.json")
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    data["events"][1]["pages"][0]["list"] = [
        {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2, ""]},
        {"code": 401, "indent": 0, "parameters": ["\\N<村人>こんにちは"]},
        {"code": 0, "indent": 0, "parameters": []}]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    mv_parser.speaker_processing = False          # user's session setting
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    proj = TranslationProject(project_path=mv_game, entries=entries)
    from translator.rpgmaker_mv import RPGMakerMVParser
    fresh = RPGMakerMVParser().load_originals(mv_game)   # app restart: default True
    stats = proj.merge_fresh_originals(fresh)
    assert stats["changed"] == 0, stats                  # BUG: namebox line reset


# 7. Ace: a map file ADDED by a game update after the first export is never
#    copied into Data_original (refresh only covers files that already have
#    entries), so the resume merge never sees its lines, and Restore
#    Originals (full-folder swap) deletes it from the game.
def test_ace_new_map_from_update_is_merged_and_survives_restore(tmp_path):
    from test_smoke_findings import _ace_map, _ace_one_map_game
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_writes
    game, live = _ace_one_map_game(tmp_path, ["こんにちは"])
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "Hello", "reviewed"
    p.save_project(str(game), entries)                     # backup made
    new_map = game / "Data" / "Map002.rvdata2"             # game update
    new_map.write_bytes(ruby_writes(_ace_map(["新しいマップ"])))
    p.save_project(str(game), entries)                     # export again
    proj = TranslationProject(project_path=str(game), entries=entries)
    stats = proj.merge_fresh_originals(RPGMakerAceParser().load_originals(str(game)))
    merged_new = any(e.original == "新しいマップ" for e in proj.entries)
    RPGMakerAceParser().restore_originals(str(game))
    assert new_map.is_file(), "Restore Originals deleted the update's new map"
    assert merged_new, f"new map's line never merged: {stats}"


# 8. Ace / Ren'Py: a translation whose position no longer exists (block or
#    label removed by an update) is neither written nor counted -> the export
#    claims to be complete.
def test_ace_removed_block_is_reported(tmp_path):
    from translator import game_io
    from test_smoke_findings import _ace_map, _ace_one_map_game
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_writes
    game, live = _ace_one_map_game(tmp_path, ["一行目"])
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "Line one", "reviewed"
    live.write_bytes(ruby_writes(_ace_map([])))          # update removed it
    with pytest.raises(game_io.PartialExportError):       # BUG: "complete"
        p.save_project(str(game), entries)


def test_renpy_removed_label_is_reported(tmp_path):
    from translator import game_io
    from translator.renpy import RenPyParser
    game = tmp_path / "RenGame"
    (game / "renpy").mkdir(parents=True)
    (game / "game").mkdir()
    s = game / "game" / "script.rpy"
    s.write_bytes('label start:\n    "最初"\nlabel cut:\n    "削除される"\n'.encode())
    p = RenPyParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "EN", "translated"
    s.write_bytes('label start:\n    "最初"\n'.encode())
    with pytest.raises(game_io.PartialExportError):       # BUG: "complete"
        p.save_project(str(game), entries)


# 9. Marshal round trip is not lossless for a UTF-8-tagged string holding
#    invalid UTF-8 (rubymarshal falls back to "unicode-escape"): untouched
#    data in a rewritten file changes ("\n" even becomes a real newline).
def test_marshal_invalid_utf8_string_round_trips():
    from translator.ruby_marshal import loads, writes
    raw = b'\x82\xa0\n'                              # SJIS "a" + literal \n
    blob = b'\x04\x08I"' + bytes([len(raw) + 5]) + raw + b'\x06:\x06ET'
    out = writes(loads(blob))
    assert rmc.plain(rmc.load(out)) == rmc.plain(rmc.load(blob)), out


# 10. Ren'Py: a translation with an escaped backslash right before a quote
#     ("C:\\" then '"') leaves the quote unescaped -> the script line no
#     longer parses (Ren'Py refuses to start).
def test_renpy_backslash_before_quote_keeps_line_valid():
    from translator.renpy import _escape_rpy, _DIALOGUE_RE
    out = _escape_rpy('Path C:\\\\"x', "")
    assert _DIALOGUE_RE.match(f'    e "{out}"'), out


# ── Round-2 additions ────────────────────────────────────────────────

def test_scripts_with_invalid_utf8_string_can_take_plugins(tmp_path):
    """Liz's Scripts.rvdata2 holds a UTF-8-tagged string with invalid bytes:
    rubymarshal could not read it at all, so plugin injection failed.  It
    now reads, injects and removes losslessly."""
    import zlib
    from translator import vxace_plugins
    body = zlib.compress(b"p 1")
    bad = b"\x82\xa0\n"                       # SJIS + literal \n, tagged UTF-8
    blob = (b'\x04\x08[\x07'
            + b'[\x08i\x06I"\x09Main\x06:\x06ET"' + bytes([len(body) + 5]) + body
            # second :E is a symlink, exactly as Ruby writes it
            + b'[\x08i\x07I"' + bytes([len(bad) + 5]) + bad + b'\x06;\x00T"\x06x')
    assert rmc.load(blob)                                  # valid Marshal
    path = tmp_path / "Scripts.rvdata2"
    path.write_bytes(blob)
    assert vxace_plugins.inject_plugins(str(path), ["mouse"])
    assert vxace_plugins.remove_plugins(str(path))
    assert path.read_bytes() == blob


def _renpy_tail_game(tmp_path):
    game = tmp_path / "RenGame"
    (game / "renpy").mkdir(parents=True)
    (game / "game").mkdir()
    (game / "game" / "script.rpy").write_bytes(
        ('label start:\n'
         '    e "こんにちは" with dissolve\n'
         '    e "元気？"\n'
         '    e "さようなら"\n').encode("utf-8"))
    return game


def test_renpy_scheme2_project_is_migrated_by_text(tmp_path):
    """Fix 1: a project saved with ID scheme 2 (tail lines skipped) is
    re-keyed by text on load, so its reviewed lines keep their work."""
    from translator.project_model import ID_SCHEME
    from translator.renpy import RenPyParser
    from translator.state_migration import migrate_project
    game = _renpy_tail_game(tmp_path)
    old = [_entry("script.rpy/start/dialog_0", "script.rpy", "dialog",
                  "元気？", "How are you?", "reviewed"),
           _entry("script.rpy/start/dialog_1", "script.rpy", "dialog",
                  "さようなら", "Goodbye", "reviewed")]
    proj = TranslationProject(project_path=str(game), project_type="renpy",
                              entries=old)
    proj.id_scheme = 2
    res = migrate_project(proj, parser=RenPyParser())
    assert res.status == "migrated" and proj.id_scheme == ID_SCHEME
    by = {e.id: e for e in proj.entries}
    assert (by["script.rpy/start/dialog_1"].translation,
            by["script.rpy/start/dialog_1"].status) == ("How are you?", "reviewed")
    assert by["script.rpy/start/dialog_2"].translation == "Goodbye"
    assert by["script.rpy/start/dialog_0"].status == "untranslated"   # tail line


def test_mv_scheme2_project_is_not_remigrated(mv_game, mv_parser):
    """MV IDs did not change in scheme 3: a scheme-2 project is just
    stamped, never re-matched."""
    from translator.project_model import ID_SCHEME
    from translator.state_migration import migrate_project
    entries = mv_parser.load_project(mv_game)
    proj = TranslationProject(project_path=mv_game, project_type="rpgmaker_mv",
                              entries=entries)
    proj.id_scheme = 2
    assert migrate_project(proj, parser=mv_parser).status == "not_needed"
    assert proj.id_scheme == ID_SCHEME and proj.entries is entries


def test_renpy_export_refuses_a_line_that_would_break(tmp_path, monkeypatch):
    """Safety net: whatever the escaper returns, a line that would not
    parse back is not written and the export says so."""
    from translator import game_io, renpy
    game = _renpy_tail_game(tmp_path)
    p = renpy.RenPyParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "EN", "translated"
    monkeypatch.setattr(renpy, "_escape_rpy", lambda t, o="": 'broken " quote')
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(game), entries)
    assert "would break the script line" in str(ei.value)
    text = (game / "game" / "script.rpy").read_text(encoding="utf-8")
    assert 'broken " quote' not in text


# ── Resume merge (fixes 2, 4-7, 17, 18; Gemini claim 3) ──────────────

def _window(qapp, monkeypatch, tmp_path, answers=None, shown=None):
    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    shown = shown if shown is not None else []

    def record(kind):
        def fn(_parent, title, text, *args, **kwargs):
            shown.append((kind, title, text))
        return fn
    monkeypatch.setattr(mw.QMessageBox, "information", record("information"))
    monkeypatch.setattr(mw.QMessageBox, "warning", record("warning"))
    answers = answers if answers is not None else []

    def question(*args, **kwargs):
        return answers.pop(0) if answers else mw.QMessageBox.StandardButton.No
    monkeypatch.setattr(mw.QMessageBox, "question", question)
    win = mw.MainWindow()
    return win, shown


def _close(win):
    win._closing = True
    win._autosave_timer.stop()
    win.hide()


def test_resume_after_update_reads_the_update_not_the_stale_backup(
        qapp, monkeypatch, tmp_path, mv_game, mv_parser):
    """Gemini claim 3 (real): the game is updated AFTER an export and the
    project is resumed before any new export.  load_originals used to read
    the old data_original/ and see nothing."""
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "translated"
    mv_parser.save_project(mv_game, entries)
    proj = TranslationProject(project_path=mv_game, project_type="rpgmaker_mv",
                              entries=entries)
    state = os.path.join(mv_game, "_translation_state.json")
    proj.save_state(state)
    # game update: live Items.json = the original plus a new item
    with open(os.path.join(mv_game, "data_original", "Items.json"),
              encoding="utf-8-sig") as f:
        items = json.load(f)
    items.append({"id": len(items), "name": "更新アイテム", "description": "",
                  "note": ""})
    with open(os.path.join(mv_game, "data", "Items.json"), "w",
              encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    win, shown = _window(qapp, monkeypatch, tmp_path)
    try:
        assert win._restore_from_state(state)
    finally:
        _close(win)
    assert any(e.original == "更新アイテム" for e in win.project.entries)
    with open(os.path.join(mv_game, "data_original", "Items.json"),
              encoding="utf-8-sig") as f:
        assert any(isinstance(i, dict) and i.get("name") == "更新アイテム"
                   for i in json.load(f))        # backup refreshed
    assert any(t == "Game Update Merged" for _k, t, _x in shown)


def test_parse_options_are_saved_and_applied_on_resume(
        qapp, monkeypatch, tmp_path, mv_game):
    proj = TranslationProject(project_path=mv_game, project_type="rpgmaker_mv",
                              entries=[])
    proj.parse_options = {"speaker_processing": False}
    state = os.path.join(mv_game, "_translation_state.json")
    proj.save_state(state)
    assert TranslationProject.load_state(state).parse_options == \
        {"speaker_processing": False}
    win, _shown = _window(qapp, monkeypatch, tmp_path)
    try:
        win.handler.parser.speaker_processing = True    # app default
        assert win._restore_from_state(state)
        assert win.handler.parser.speaker_processing is False
    finally:
        _close(win)


def test_low_match_asks_and_can_merge_anyway(qapp, monkeypatch, tmp_path,
                                             mv_game, mv_parser):
    from translator.widgets import main_window as mw
    entries = mv_parser.load_project(mv_game)
    # 30 translated lines that are not in this game at all
    fake = [_entry(f"Items.json/{i}/name", "Items.json", "name", f"無い{i}",
                   f"EN{i}") for i in range(100, 130)]
    proj = TranslationProject(project_path=mv_game, project_type="rpgmaker_mv",
                              entries=fake)
    state = os.path.join(mv_game, "_translation_state.json")
    proj.save_state(state)
    win, _shown = _window(qapp, monkeypatch, tmp_path,
                          answers=[mw.QMessageBox.StandardButton.No])
    try:
        assert win._restore_from_state(state)
        fake_now = [e for e in win.project.entries if e.original.startswith("無い")]
        assert [e.translation for e in fake_now] == \
            [f"EN{i}" for i in range(100, 130)]    # No: unchanged
        assert not win.project.migration_orphans
    finally:
        _close(win)
    win, _shown = _window(qapp, monkeypatch, tmp_path,
                          answers=[mw.QMessageBox.StandardButton.Yes])
    try:
        assert win._restore_from_state(state)
        assert len(win.project.migration_orphans) == 30   # kept, not lost
        assert len(win.project.entries) == len(entries)
    finally:
        _close(win)


def test_resume_shows_load_warnings(qapp, monkeypatch, tmp_path, mv_game):
    with open(os.path.join(mv_game, "data", "Map001.json"), "w") as f:
        f.write("{broken")
    proj = TranslationProject(project_path=mv_game, project_type="rpgmaker_mv",
                              entries=[_entry("Map001.json/displayName",
                                              "Map001.json", "displayName",
                                              "村", "Village", "reviewed")])
    state = os.path.join(mv_game, "_translation_state.json")
    proj.save_state(state)
    win, shown = _window(qapp, monkeypatch, tmp_path)
    try:
        assert win._restore_from_state(state)
    finally:
        _close(win)
    assert any(t == "Some Game Files Could Not Be Read" for _k, t, _x in shown)
    # the unreadable file's entries are kept untouched
    assert any(e.id == "Map001.json/displayName" and e.status == "reviewed"
               for e in win.project.entries)


def test_line_removed_by_update_is_retired_and_exports_complete(mv_game, mv_parser):
    """Fix 4: a deleted line no longer keeps every export "Incomplete"."""
    from test_smoke_findings import _set_page
    _set_page(mv_game, [(401, "一行目"), (401, "")])
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "translated"
    mv_parser.save_project(mv_game, entries)
    # the update (built from the ORIGINAL file) removes the line
    with open(os.path.join(mv_game, "data_original", "Map001.json"),
              encoding="utf-8-sig") as f:
        m = json.load(f)
    m["events"][1]["pages"][0]["list"] = [{"code": 0, "indent": 0, "parameters": []}]
    for d in ("data", "data_original"):
        with open(os.path.join(mv_game, d, "Map001.json"), "w", encoding="utf-8") as f:
            json.dump(m, f, ensure_ascii=False)
    proj = TranslationProject(project_path=mv_game, entries=entries)
    stats = proj.merge_fresh_originals(mv_parser.load_originals(mv_game))
    assert stats["retired"] == 1
    assert any(o["original"].startswith("一行目") for o in proj.migration_orphans)
    mv_parser.save_project(mv_game, proj.entries)        # complete again


def test_renamed_mv_event_keeps_its_translations(mv_game, mv_parser):
    """Fix 4: an event renamed by an update (its name is in the ID) keeps
    its translations instead of getting duplicate empty entries."""
    from test_smoke_findings import _set_page
    _set_page(mv_game, [(401, "一行目"), (401, "")])
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    data = os.path.join(mv_game, "data", "Map001.json")
    with open(data, encoding="utf-8-sig") as f:
        m = json.load(f)
    m["events"][1]["name"] = "改名されたイベント"
    with open(data, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False)
    proj = TranslationProject(project_path=mv_game, entries=entries)
    stats = proj.merge_fresh_originals(mv_parser.load_originals(mv_game))
    assert stats["changed"] == 0 and stats["new"] == 0
    line = [e for e in proj.entries if e.original.startswith("一行目")]
    assert len(line) == 1 and line[0].status == "reviewed"
    assert "改名されたイベント" in line[0].id


def test_ace_refresh_backups_then_originals_see_the_update(tmp_path):
    """Gemini claim 3 for VX Ace: resume after an update, before any export."""
    from test_smoke_findings import _ace_map, _ace_one_map_game
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_writes
    game, live = _ace_one_map_game(tmp_path, ["古い台詞"])
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "EN", "reviewed"
    p.save_project(str(game), entries)
    live.write_bytes(ruby_writes(_ace_map(["新しい台詞"])))        # update
    (game / "Data" / "Map002.rvdata2").write_bytes(ruby_writes(_ace_map(["新マップ"])))
    assert sorted(p.refresh_backups(str(game))) == ["Map001.rvdata2", "Map002.rvdata2"]
    originals = {e.original for e in p.load_originals(str(game))}
    assert {"新しい台詞", "新マップ"} <= originals


def test_refresh_backups_never_takes_our_own_output(tmp_path):
    """The resume refresh follows the export's rule: our own last export
    is never mistaken for a game update."""
    from test_smoke_findings import _ace_one_map_game
    from translator.rpgmaker_ace import RPGMakerAceParser
    game, _live = _ace_one_map_game(tmp_path, ["台詞"])
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "EN", "reviewed"
    p.save_project(str(game), entries)
    assert p.refresh_backups(str(game)) == []
    assert {e.original for e in p.load_originals(str(game))} == {"台詞"}


def test_renpy_refresh_backups_then_originals_see_the_update(tmp_path):
    from translator.renpy import RenPyParser
    game = tmp_path / "RenGame"
    (game / "renpy").mkdir(parents=True)
    (game / "game").mkdir()
    s = game / "game" / "script.rpy"
    s.write_bytes('label start:\n    "古い"\n'.encode("utf-8"))
    p = RenPyParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "EN", "reviewed"
    p.save_project(str(game), entries)
    s.write_bytes('label start:\n    "新しい"\n'.encode("utf-8"))      # update
    assert p.refresh_backups(str(game)) == ["script.rpy"]
    assert {e.original for e in p.load_originals(str(game))} == {"新しい"}


# ── Lines whose place no longer exists are counted (fix 10) ──────────

def test_mv_deleted_db_row_and_note_tag_are_reported(mv_game, mv_parser):
    from translator import game_io
    entries = [
        _entry("Items.json/99/name", "Items.json", "name", "消えた品", "Gone"),
        _entry("Items.json/1/note/Tag/0", "Items.json", "note_tag:Tag",
               "消えたタグ", "Gone tag"),
    ]
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert "Items.json: 2 translated line(s) not written" in str(ei.value)


def test_mv_normal_export_counts_nothing(mv_game, mv_parser):
    """Guard against false alarms: a plain export of every entry is complete."""
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "translated"
    mv_parser.save_project(mv_game, entries)
    assert mv_parser.last_export_notes == []


def test_wolf_message_whose_command_is_gone_is_counted():
    from types import SimpleNamespace as NS
    from translator import wolfrpg
    p = wolfrpg.WolfRPGParser()
    p._visited_keys = set()
    cmds = [NS(code=wolfrpg.CMD_MESSAGE, string_args=["残る"])]
    tr = {"M/Ev1/p0/msg0": "Stays", "M/Ev1/p0/msg5": "Removed"}
    orig = {"M/Ev1/p0/msg0": "残る", "M/Ev1/p0/msg5": "消えた"}
    p._apply_commands(cmds, "M/Ev1/p0", tr, orig, "M", {})
    assert p._visited_keys == {"M/Ev1/p0/msg0"}       # msg5 never looked at


def test_rm2k_removed_message_is_reported(tmp_path):
    from test_smoke_findings import _rm2k_map
    from translator import game_io
    from translator import rpgmaker_2k as rm
    (tmp_path / "RPG_RT.ldb").write_bytes(rm._write_header("LcfDataBase"))
    (tmp_path / "RPG_RT.lmt").write_bytes(b"")
    live = tmp_path / "Map0001.lmu"
    live.write_bytes(_rm2k_map("台詞", "はい"))
    p = rm.RPGMaker2KParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN", "reviewed"
    # an extra entry whose message no longer exists in the event
    entries.append(_entry("Map0001.lmu/Ev1(EV)/p1/dialog_7", "Map0001.lmu",
                          "dialog", "消えた", "Gone"))
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert "Map0001.lmu: 1 translated line(s) not written" in str(ei.value)


# ── MZ plugin identifiers (fix 8) ────────────────────────────────────

_PLUGIN_HEADER = """/*:
 * @param 形状
 * @type select
 * @option 四角い形
 * @option 斜めな形
 *
 * @param 表示名
 * @type combo
 * @option 勇者
 * @option 魔王
 *
 * @param 共有名
 * @type select
 * @option 選択肢
 */
/*~struct~other:
 * @param 共有名
 * @desc a free-text field that shares the name
 */
"""


def _plugin_game(mv_game, params):
    os.makedirs(os.path.join(mv_game, "js", "plugins"), exist_ok=True)
    with open(os.path.join(mv_game, "js", "plugins", "TestP.js"), "w",
              encoding="utf-8") as f:
        f.write(_PLUGIN_HEADER)
    plugins = [{"name": "TestP", "status": True, "description": "",
                "parameters": params}]
    with open(os.path.join(mv_game, "js", "plugins.js"), "w", encoding="utf-8") as f:
        f.write("var $plugins =\n" + json.dumps(plugins, ensure_ascii=False) + ";\n")


def test_plugin_combo_value_stays_translatable(mv_game, mv_parser):
    _plugin_game(mv_game, {"形状": "斜めな形", "表示名": "勇者", "共有名": "選択肢"})
    ids = {e.id for e in mv_parser.load_project(mv_game) if e.file == "plugins.js"}
    assert "plugins.js/TestP/形状" not in ids          # select: identifier
    assert "plugins.js/TestP/表示名" in ids            # combo: free text
    assert "plugins.js/TestP/共有名" in ids            # also a plain string


def test_plugin_identifier_in_old_project_is_not_exported(mv_game, mv_parser):
    """A project made before the filter still holds the translated select
    value: export must leave the plugin's value alone."""
    _plugin_game(mv_game, {"形状": "斜めな形"})
    old = [_entry("plugins.js/TestP/形状", "plugins.js", "TestP/形状",
                  "斜めな形", "Diagonal")]
    mv_parser.save_project(mv_game, old)
    plugins = mv_parser._load_plugins_js(os.path.join(mv_game, "js", "plugins.js"))
    assert plugins[0]["parameters"]["形状"] == "斜めな形"
    assert mv_parser.plugin_identifiers_skipped == 1


# ── Tyrano lang.js / Config.tjs .bak follow game updates (fix 12) ────

def test_tyrano_lang_js_update_is_not_rolled_back(tmp_path):
    from test_smoke_findings import _tyrano_game
    from translator.tyranoscript import TyranoScriptParser
    _ks, lang = _tyrano_game(tmp_path, "*start\n台詞です[p]\n")
    p = TyranoScriptParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN", "translated"
    p.save_project(str(tmp_path), entries)
    # game update: lang.js gains a key (the .bak still has the old file)
    lang.write_text('var tyrano_lang = {"word":{"go":"進む","back":"戻る"}};\n',
                    encoding="utf-8")
    p.save_project(str(tmp_path), entries)
    text = lang.read_text(encoding="utf-8")
    assert '"back":"戻る"' in text                 # update kept
    assert '"go":"EN"' in text                     # translation still applied
    assert '"back":"戻る"' in (lang.parent / "lang.js.bak").read_text(encoding="utf-8")


def test_tyrano_old_export_lang_js_is_never_taken_as_update(tmp_path):
    """A lang.js an older version translated (not in the manifest) must not
    be copied over the original .bak."""
    from test_smoke_findings import _tyrano_game
    from translator.tyranoscript import TyranoScriptParser
    _ks, lang = _tyrano_game(tmp_path, "*start\n台詞です[p]\n")
    p = TyranoScriptParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN", "translated"
    p.save_project(str(tmp_path), entries)
    manifest = tmp_path / "data" / "scenario_original.manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["files"].pop("_system/lang.js")             # as an older version left it
    manifest.write_text(json.dumps(data), encoding="utf-8")
    p.save_project(str(tmp_path), entries)
    assert '"go":"進む"' in (lang.parent / "lang.js.bak").read_text(encoding="utf-8")


# ── Wolf 3 refusal after unpacking Data.wolf; encrypted 3.x (fix 15) ─

def test_wolf3_refusal_removes_what_it_unpacked(tmp_path, monkeypatch):
    from translator import wolfrpg
    game = tmp_path / "W"
    game.mkdir()
    (game / "Game.exe").write_bytes(b"MZ")
    (game / "Data.wolf").write_bytes(b"archive")

    def fake_unpack(game_dir):
        data = game_dir / "Data" / "BasicData"
        data.mkdir(parents=True)
        (data / "DataBase.dat").write_bytes(b"\x00W\x00\x00OLUFM\x00" + b"\x00" * 6)
        (game_dir / "Data" / "MapData").mkdir()
        return game_dir / "Data"
    monkeypatch.setattr(wolfrpg, "unpack_data_wolf", fake_unpack)
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(game)) == []
    assert "Your game files were not changed" in p.load_warnings[0]
    assert sorted(x.name for x in game.iterdir()) == ["Data.wolf", "Game.exe"]


def test_wolf3_refusal_keeps_a_data_folder_that_was_already_there(tmp_path):
    from translator import wolfrpg
    game = tmp_path / "W"
    (game / "Data" / "BasicData").mkdir(parents=True)
    (game / "Data" / "MapData").mkdir()
    (game / "Data" / "BasicData" / "DataBase.dat").write_bytes(
        b"\x00W\x00\x00OLUFM\x00" + b"\x00" * 6)
    (game / "Game.exe").write_bytes(b"MZ")
    wolfrpg.WolfRPGParser().load_project(str(game))
    assert (game / "Data" / "BasicData" / "DataBase.dat").is_file()


def test_wolf3_encrypted_dat_is_detected_and_wolf2_encrypted_is_not(tmp_path):
    from translator import wolfrpg
    basic = tmp_path / "Data" / "BasicData"
    basic.mkdir(parents=True)
    (basic / "DataBase.dat").write_bytes(b"\x00P\x00\x00\x00\x57" + b"\x00" * 10)
    assert "encrypted" in wolfrpg.wolf3_marker(tmp_path / "Data")
    (basic / "DataBase.dat").write_bytes(b"\x00P\x00\x00\x00\x50" + b"\x00" * 10)
    assert wolfrpg.wolf3_marker(tmp_path / "Data") is None
