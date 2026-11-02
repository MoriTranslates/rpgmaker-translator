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
@pytest.mark.xfail(strict=True, reason="round 2: fix pending")
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
@pytest.mark.xfail(strict=True, reason="round 2: fix pending")
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
@pytest.mark.xfail(strict=True, reason="round 2: fix pending")
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
@pytest.mark.xfail(strict=True, reason="round 2: fix pending")
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
@pytest.mark.xfail(strict=True, reason="round 2: fix pending")
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
@pytest.mark.xfail(strict=True, reason="round 2: fix pending")
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
@pytest.mark.xfail(strict=True, reason="round 2: fix pending")
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
