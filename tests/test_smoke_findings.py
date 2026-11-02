"""Regression tests for the findings of the 1.1.1 smoke test on real games.

Binary writers are checked with ``ruby_marshal_check`` -- an independent
Ruby Marshal decoder written from Ruby's rules, not with rubymarshal (the
library under test) -- against bytes laid out the way Ruby writes them.
"""

import json
import os
import zlib

import pytest

import ruby_marshal_check as rmc
from translator.project_model import TranslationEntry


def _entry(eid, file, field, original, translation, status="translated"):
    return TranslationEntry(id=eid, file=file, field=field, original=original,
                            translation=translation, status=status)


# ── VX Ace: Marshal object links (FIXED) ────────────────────────────
#
# Ruby's Marshal numbers every Float / String / Bignum / Regexp it writes,
# and "@n" links count them.  rubymarshal 1.2.10's writer does not, so any
# link written after a Float, or after a plain ``str`` (which is what a
# translated string is), pointed at the wrong object.  On "Liz ~The Tower
# and the Grimoire~" a plain read+write corrupted 247 of 413 data files and
# an export-style rewrite broke links in 315 (EventCommand slots holding
# MoveRoutes or Floats).

# ["x", Foo.new, <link to Foo>]: objects are array=0, "x"=1, Foo=2
_STR_THEN_LINK = b'\x04\x08[\x08I"\x06x\x06:\x06ETo:\x08Foo\x00@\x07'
# [1.5, Foo.new, <link to Foo>]: objects are array=0, 1.5=1, Foo=2
_FLOAT_THEN_LINK = b'\x04\x08[\x08f\x081.5o:\x08Foo\x00@\x07'


def test_ace_writer_keeps_links_after_a_translated_string():
    from translator.rpgmaker_ace import ruby_loads, ruby_writes
    data = ruby_loads(_STR_THEN_LINK)
    assert data[2] is data[1]
    data[0] = "translated"                 # what _set_attr stores
    out = ruby_loads(ruby_writes(data))
    assert out[2] is out[1]                # still the Foo object
    assert out[0] == "translated"


def test_ace_writer_keeps_links_after_a_float():
    from translator.rpgmaker_ace import ruby_loads, ruby_writes
    raw = ruby_writes(ruby_loads(_FLOAT_THEN_LINK))
    assert raw == _FLOAT_THEN_LINK         # byte-identical round trip


def test_ace_writer_round_trip_is_byte_identical_for_untouched_data():
    from translator.rpgmaker_ace import ruby_loads, ruby_writes
    for raw in (_STR_THEN_LINK, _FLOAT_THEN_LINK):
        assert ruby_writes(ruby_loads(raw)) == raw


# [1292597180 (Bignum on 32-bit Ruby), Foo.new, <link to Foo>]:
# Bignum is an object too: array=0, bignum=1, Foo=2
_BIGNUM_THEN_LINK = (b'\x04\x08[\x08l+\x07' + bytes.fromhex('bc770b4d')
                     + b'o:\x08Foo\x00@\x07')


def test_ace_writer_keeps_31_bit_overflow_as_bignum():
    """System.rvdata2's @version_id must stay a Bignum for RGSS3's
    32-bit Ruby, and still counts as an object for later links."""
    from translator.rpgmaker_ace import ruby_loads, ruby_writes
    data = ruby_loads(_BIGNUM_THEN_LINK)
    assert data[0] == 1292597180 and data[2] is data[1]
    assert ruby_writes(data) == _BIGNUM_THEN_LINK
    assert ruby_writes((1 << 30) - 1)[2:3] == b"i"     # largest Fixnum
    assert ruby_writes(1 << 30)[2:3] == b"l"           # first Bignum
    assert ruby_loads(ruby_writes([(1 << 30) - 1, -(1 << 30), 1 << 30, -(1 << 40)])) \
        == [(1 << 30) - 1, -(1 << 30), 1 << 30, -(1 << 40)]


def test_ace_export_keeps_shared_objects_in_a_map(tmp_path):
    """Export a map whose events share an object, with a Float and a
    translated line before the shared reference."""
    from rubymarshal.classes import RubyObject, RubyString
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_loads, ruby_writes

    route = RubyObject("RPG::MoveRoute", {"@repeat": False, "@list": []})

    def cmd(code, params):
        return RubyObject("RPG::EventCommand",
                          {"@code": code, "@indent": 0, "@parameters": params})
    page = RubyObject("RPG::Event::Page", {"@list": [
        cmd(101, [RubyString(""), 0, 0, 2]),
        cmd(401, [RubyString("こんにちは")]),
        cmd(230, [0.5]),                     # a Float before the link
        cmd(205, [-1, route]),
        cmd(505, [route]),                   # second reference -> "@n" link
        cmd(0, []),
    ]})
    event = RubyObject("RPG::Event", {"@id": 1, "@name": RubyString("EV001"),
                                      "@pages": [page]})
    game_map = RubyObject("RPG::Map", {"@display_name": RubyString(""),
                                       "@events": {1: event}})
    game = tmp_path / "Game"
    (game / "Data").mkdir(parents=True)
    (game / "Data" / "Map001.rvdata2").write_bytes(ruby_writes(game_map))

    parser = RPGMakerAceParser()
    entries = parser.load_project(str(game))
    assert entries, "fixture should yield the 401 line"
    for e in entries:
        e.translation, e.status = "Hello", "translated"
    parser.save_project(str(game), entries)

    raw = (game / "Data" / "Map001.rvdata2").read_bytes()
    out = ruby_loads(raw)
    lst = out.attributes["@events"][1].attributes["@pages"][0].attributes["@list"]
    assert lst[3].attributes["@parameters"][1] is lst[4].attributes["@parameters"][0]
    assert lst[4].attributes["@parameters"][0].ruby_class_name == "RPG::MoveRoute"
    assert str(lst[1].attributes["@parameters"][0]) == "Hello"
    # Same check with the independent decoder (Ruby's own numbering)
    m = rmc.load(raw)
    ev = dict(m.body)["@events"].body[0][1]
    cmds = dict(ev.body)["@pages"].body[0].body["@list"].body
    shared_a = cmds[3].body["@parameters"].body[1]
    shared_b = cmds[4].body["@parameters"].body[0]
    assert shared_a is shared_b and shared_a.cls == "RPG::MoveRoute"


# Scripts.rvdata2 laid out exactly as Ruby 1.9 writes it:
# [[1, "Main" (UTF-8), <zlib blob>], [2, "Other", <zlib blob>]]
_BLOB1, _BLOB2 = zlib.compress(b"p 1"), zlib.compress(b"p 2")
_SCRIPTS = (b'\x04\x08[\x07'
            + b'[\x08i\x06I"\x09Main\x06:\x06ET"' + bytes([len(_BLOB1) + 5]) + _BLOB1
            + b'[\x08i\x07I"\x0aOther\x06;\x00T"' + bytes([len(_BLOB2) + 5]) + _BLOB2)


def test_scripts_round_trip_is_byte_identical():
    from translator import ruby_marshal
    assert rmc.load(_SCRIPTS)          # the fixture itself is valid Marshal
    assert ruby_marshal.writes(ruby_marshal.loads(_SCRIPTS)) == _SCRIPTS


def test_plugin_injection_keeps_scripts_intact(tmp_path):
    from translator import vxace_plugins
    path = tmp_path / "Scripts.rvdata2"
    path.write_bytes(_SCRIPTS)
    assert vxace_plugins.inject_plugins(str(path), ["mouse"])
    rows = rmc.plain(rmc.load(path.read_bytes()))[1]
    names = [r[1][1][1] for r in rows]
    plugin_at = [i for i, n in enumerate(names)
                 if vxace_plugins._TAG.encode() in n]
    assert plugin_at and plugin_at[-1] < names.index(b"Main")   # before Main
    assert b"Other" in names
    # original script bodies untouched
    blobs = {r[1][1][1]: r[1][2][1] for r in rows}
    assert blobs[b"Main"] == _BLOB1 and blobs[b"Other"] == _BLOB2
    assert vxace_plugins.remove_plugins(str(path))
    assert path.read_bytes() == _SCRIPTS


def test_window_scaler_keeps_scripts_intact(tmp_path):
    from translator import vxace_scaler
    path = tmp_path / "Scripts.rvdata2"
    path.write_bytes(_SCRIPTS)
    assert vxace_scaler.inject_scaler(str(path))
    rows = rmc.plain(rmc.load(path.read_bytes()))[1]
    assert len(rows) == 3
    assert vxace_scaler.remove_scaler(str(path))
    assert path.read_bytes() == _SCRIPTS


# ── MV/MZ: one 401 command holding several lines (FIXED) ─────────────
#
# Games previously machine-translated (e.g. "Utopia" 1.0: 1,705 of 5,053
# Show Text commands) keep a whole message in ONE 401 command with "\n"
# inside.  The parser extracts it as a multi-line entry; the exporter used
# to look for that many consecutive 401 commands, find one, and skip the
# translation silently -- "Export Complete", text stayed untranslated.

def _set_page(mv_game, cmds):
    path = os.path.join(mv_game, "data", "Map001.json")
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    data["events"][1]["pages"][0]["list"] = (
        [{"code": 101, "indent": 0, "parameters": ["", 0, 0, 2, ""]}]
        + [{"code": c, "indent": 0, "parameters": [t]} for c, t in cmds]
        + [{"code": 0, "indent": 0, "parameters": []}])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return path


def _page_texts(path):
    with open(path, encoding="utf-8-sig") as f:
        live = json.load(f)
    return [c["parameters"][0] for c in live["events"][1]["pages"][0]["list"]
            if c["code"] in (401, 405)]


def _translate_where(entries, pred, fn):
    hits = 0
    for e in entries:
        if pred(e):
            e.translation, e.status = fn(e), "translated"
            hits += 1
    return hits


def test_mv_single_401_with_embedded_newline_is_exported(mv_game, mv_parser):
    path = _set_page(mv_game, [(401, "一行目\n二行目")])
    entries = mv_parser.load_project(mv_game)
    assert _translate_where(entries, lambda e: e.original == "一行目\n二行目",
                            lambda e: "Line one\nLine two") == 1
    mv_parser.save_project(mv_game, entries)
    assert _page_texts(path) == ["Line one\nLine two"]   # same single command


def test_mv_mixed_layout_keeps_each_commands_line_count(mv_game, mv_parser):
    path = _set_page(mv_game, [(401, "あ\nい"), (401, "う")])
    entries = mv_parser.load_project(mv_game)
    assert _translate_where(entries, lambda e: e.original == "あ\nい\nう",
                            lambda e: "A\nB\nC\nD") == 1
    mv_parser.save_project(mv_game, entries)
    # first command keeps 2 lines, the extra line goes to the last command
    assert _page_texts(path) == ["A\nB", "C\nD"]


def test_mv_multiline_command_noop_export_is_unchanged(mv_game, mv_parser):
    path = _set_page(mv_game, [(401, "一行目\n二行目"), (401, "三行目")])
    entries = mv_parser.load_project(mv_game)
    assert _translate_where(entries, lambda e: "一行目" in e.original,
                            lambda e: e.original) == 1
    mv_parser.save_project(mv_game, entries)
    assert _page_texts(path) == ["一行目\n二行目", "三行目"]


def test_mv_changed_line_is_reported_and_keeps_game_text(mv_game, mv_parser):
    """A line whose text changed under it (game update / hand edit) is
    never overwritten, and the export never claims to be complete."""
    from translator import game_io
    path = _set_page(mv_game, [(401, "古い台詞")])
    entries = mv_parser.load_project(mv_game)
    assert _translate_where(entries, lambda e: e.original == "古い台詞",
                            lambda e: "Old line") == 1
    _set_page(mv_game, [(401, "新しい台詞")])        # changed before export
    with pytest.raises(game_io.PartialExportError) as ei:
        mv_parser.save_project(mv_game, entries)
    assert "Map001.json: 1 translated line(s) not written" in str(ei.value)
    assert _page_texts(path) == ["新しい台詞"]


# ── Restore killed between its two renames (FIXED) ───────────────────
#
# restore_tree renames data/ -> data.rmt-old, then data.rmt-restore -> data/.
# A kill between the two left no data/ folder: the game would not start,
# the engine was not detected and Restore said "Could not find the game's
# data/ folder".  Opening, exporting or restoring now puts it back.

def _kill_between_renames(data_dir):
    """Recreate the on-disk state of a restore killed mid-swap."""
    import shutil
    shutil.copytree(data_dir, data_dir + ".rmt-restore")   # finished copy
    os.rename(data_dir, data_dir + ".rmt-old")             # first rename


def test_mv_restore_recovers_after_kill_between_renames(mv_game, mv_parser):
    from translator.engine_handler import detect_engine, RPGMakerMVHandler
    mv_parser.save_project(mv_game, mv_parser.load_project(mv_game))
    data = os.path.join(mv_game, "data")
    _kill_between_renames(data)
    assert detect_engine(mv_game) is RPGMakerMVHandler      # still found
    assert os.path.isdir(data)
    assert not os.path.exists(data + ".rmt-old")
    assert not os.path.exists(data + ".rmt-restore")
    mv_parser.restore_originals(mv_game)                     # and restorable
    assert os.path.isdir(data) and not os.path.exists(data + ".rmt-old")


def test_mv_open_after_kill_between_renames(mv_game, mv_parser):
    data = os.path.join(mv_game, "data")
    with open(os.path.join(data, "System.json"), "rb") as f:
        before = f.read()
    _kill_between_renames(data)
    assert mv_parser.load_project(mv_game)
    with open(os.path.join(data, "System.json"), "rb") as f:
        assert f.read() == before


def _ace_game_dir(tmp_path):
    from translator.rpgmaker_ace import ruby_writes
    game = tmp_path / "AceGame"
    (game / "Data").mkdir(parents=True)
    (game / "Data" / "Map001.rvdata2").write_bytes(ruby_writes(["JP"]))
    return game


def test_ace_restore_recovers_after_kill_between_renames(tmp_path):
    from translator.engine_handler import detect_engine, RPGMakerAceHandler
    from translator.rpgmaker_ace import RPGMakerAceParser
    game = _ace_game_dir(tmp_path)
    RPGMakerAceParser().save_project(str(game), [])
    _kill_between_renames(str(game / "Data"))
    assert detect_engine(str(game)) is RPGMakerAceHandler
    assert (game / "Data").is_dir()
    RPGMakerAceParser().restore_originals(str(game))
    assert (game / "Data").is_dir()
    assert not os.path.exists(str(game / "Data") + ".rmt-old")


def test_ace_export_after_kill_between_renames(tmp_path):
    from translator.rpgmaker_ace import RPGMakerAceParser
    game = _ace_game_dir(tmp_path)
    RPGMakerAceParser().save_project(str(game), [])
    _kill_between_renames(str(game / "Data"))
    RPGMakerAceParser().save_project(str(game), [])          # no crash
    assert (game / "Data" / "Map001.rvdata2").is_file()


def test_recover_does_nothing_when_live_folder_exists(tmp_path):
    from translator import game_io
    live = tmp_path / "data"
    live.mkdir()
    (tmp_path / "data.rmt-old").mkdir()
    assert game_io.recover_interrupted_restore(str(live)) is False
    assert (tmp_path / "data.rmt-old").is_dir()      # left for restore_tree


# ── VX Ace: stale translation over an updated line (FIXED) ───────────
#
# Ace wrote dialogue by command index without checking that the original
# text was still there.  After a game update that changed a line, the next
# export wrote the OLD translation over the NEW Japanese line.  Now every
# write by position checks the original; a changed line keeps the game's
# text and the export reports it ("Export incomplete").

def _ace_map(texts, choices=None):
    from rubymarshal.classes import RubyObject, RubyString

    def cmd(code, params):
        return RubyObject("RPG::EventCommand", {"@code": code, "@indent": 0,
                                                "@parameters": params})
    cmds = [cmd(101, [RubyString(""), 0, 0, 2])]
    cmds += [cmd(401, [RubyString(t)]) for t in texts]
    if choices:
        cmds.append(cmd(102, [[RubyString(c) for c in choices], 2]))
    cmds.append(cmd(0, []))
    page = RubyObject("RPG::Event::Page", {"@list": cmds})
    ev = RubyObject("RPG::Event", {"@id": 1, "@name": RubyString("EV"),
                                   "@pages": [page]})
    return RubyObject("RPG::Map", {"@display_name": RubyString(""),
                                   "@events": {1: ev}})


def _ace_cmds(path):
    from translator.rpgmaker_ace import ruby_loads
    out = ruby_loads(path.read_bytes())
    lst = out.attributes["@events"][1].attributes["@pages"][0].attributes["@list"]
    return lst


def _ace_one_map_game(tmp_path, texts, choices=None):
    from translator.rpgmaker_ace import ruby_writes
    game = tmp_path / "Game"
    (game / "Data").mkdir(parents=True)
    live = game / "Data" / "Map001.rvdata2"
    live.write_bytes(ruby_writes(_ace_map(texts, choices)))
    return game, live


def test_ace_export_skips_line_whose_original_changed(tmp_path):
    from translator import game_io
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_writes
    game, live = _ace_one_map_game(tmp_path, ["古い台詞"], ["はい", "いいえ"])
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    p.save_project(str(game), entries)
    # game update: the line and one choice change
    live.write_bytes(ruby_writes(_ace_map(["新しい台詞"], ["はい", "やめる"])))
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(game), entries)
    assert "Map001.rvdata2: 2 translated line(s) not written" in str(ei.value)
    lst = _ace_cmds(live)
    assert str(lst[1].attributes["@parameters"][0]) == "新しい台詞"
    choices = [str(c) for c in lst[2].attributes["@parameters"][0]]
    assert choices[0].startswith("EN ")       # unchanged choice still written
    assert choices[1] == "やめる"


NAMEBOX = "\\N<リズ>"          # backslash, N, <リズ>  (an RPG Maker namebox)


def test_ace_namebox_block_and_noop_export_are_not_flagged(tmp_path):
    from translator.rpgmaker_ace import RPGMakerAceParser
    game, live = _ace_one_map_game(tmp_path, [NAMEBOX, "こんにちは", ""])
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    dlg = [e for e in entries if e.field == "dialogue"]
    assert dlg and dlg[0].namebox == NAMEBOX
    for e in entries:
        e.translation, e.status = e.original, "translated"
    p.save_project(str(game), entries)            # no-op: nothing reported
    for e in dlg:
        e.translation = "Hello"
    p.save_project(str(game), entries)            # real translation lands
    texts = [str(c.attributes["@parameters"][0]) for c in _ace_cmds(live)
             if c.attributes["@code"] == 401]
    assert texts[:2] == [NAMEBOX, "Hello"]


def test_ace_database_field_changed_by_update_is_kept(tmp_path):
    from rubymarshal.classes import RubyObject, RubyString
    from translator import game_io
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_loads, ruby_writes

    def items(name):
        return [None, RubyObject("RPG::Item", {
            "@id": 1, "@name": RubyString(name),
            "@description": RubyString(""), "@note": RubyString("")})]
    game = tmp_path / "Game"
    (game / "Data").mkdir(parents=True)
    path = game / "Data" / "Items.rvdata2"
    path.write_bytes(ruby_writes(items("ポーション")))
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "Potion", "translated"
    p.save_project(str(game), entries)
    path.write_bytes(ruby_writes(items("ハイポーション")))      # game update
    with pytest.raises(game_io.PartialExportError):
        p.save_project(str(game), entries)
    out = ruby_loads(path.read_bytes())
    assert str(out[1].attributes["@name"]) == "ハイポーション"


# ── Ren'Py: lines that were never extracted, escapes (FIXED) ──────────
#
# Only game/*.rpy was read (real games keep scripts in subfolders), say
# lines ending in `with ...` / `nointeract` were skipped, a lone backslash
# in a translation became Ren'Py's "\ " (a space), and a character name
# containing \" was cut at the escaped quote.

def _renpy_game(tmp_path, files):
    game = tmp_path / "RenGame"
    (game / "renpy").mkdir(parents=True)
    for rel, text in files.items():
        p = game / "game" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
    return game


def _renpy_export(game, fn):
    from translator.renpy import RenPyParser
    p = RenPyParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = fn(e), "translated"
    p.save_project(str(game), entries)
    return entries


def test_renpy_scripts_in_subfolders_are_extracted(tmp_path):
    from translator.renpy import RenPyParser
    game = _renpy_game(tmp_path, {
        "script.rpy": 'label start:\n    "最初の台詞"\n',
        "chapters/ch2.rpy": 'label ch2:\n    "第二章の台詞"\n',
        "tl/english/script.rpy": 'translate english start_1:\n    "訳"\n',
    })
    entries = RenPyParser().load_project(str(game))
    by_orig = {e.original: e for e in entries}
    assert "第二章の台詞" in by_orig
    assert by_orig["第二章の台詞"].id == "chapters/ch2.rpy/ch2/dialog_0"
    assert "訳" not in by_orig                       # tl/ is not game script


def test_renpy_subfolder_export_backup_and_restore(tmp_path):
    from translator.renpy import RenPyParser
    src = 'label ch2:\n    "第二章の台詞"\n'
    game = _renpy_game(tmp_path, {"script.rpy": 'label start:\n    "一"\n',
                                  "chapters/ch2.rpy": src})
    _renpy_export(game, lambda e: "EN")
    live = game / "game" / "chapters" / "ch2.rpy"
    assert '"EN"' in live.read_text(encoding="utf-8")
    assert (game / "game_original" / "chapters" / "ch2.rpy").read_bytes() \
        == src.encode("utf-8")
    RenPyParser().restore_originals(str(game))
    assert live.read_bytes() == src.encode("utf-8")


def test_renpy_old_backup_gets_subfolder_scripts_before_writing(tmp_path):
    """A backup from a version that only copied game/*.rpy has no copy of
    subfolder scripts: they must be backed up before the first write."""
    src = 'label ch2:\n    "第二章の台詞"\n'
    game = _renpy_game(tmp_path, {"script.rpy": 'label start:\n    "一"\n',
                                  "chapters/ch2.rpy": src})
    (game / "game_original").mkdir()
    (game / "game_original" / "script.rpy").write_bytes(
        'label start:\n    "一"\n'.encode("utf-8"))
    _renpy_export(game, lambda e: "EN")
    assert (game / "game_original" / "chapters" / "ch2.rpy").read_bytes() \
        == src.encode("utf-8")


def test_renpy_say_with_trailing_clause_is_extracted(tmp_path):
    from translator.renpy import RenPyParser
    game = _renpy_game(tmp_path, {
        "script.rpy": ('define e = Character("エリカ")\n'
                       'label start:\n'
                       '    e "こんにちは" with dissolve\n'
                       '    e "待って" nointeract\n'
                       '    "語り" with Dissolve(0.5)\n'
                       '    e "台詞" id start_abc123\n'),
    })
    originals = {e.original for e in RenPyParser().load_project(str(game))}
    assert {"こんにちは", "待って", "語り", "台詞"} <= originals


def test_renpy_trailing_clause_is_written_back(tmp_path):
    game = _renpy_game(tmp_path, {
        "script.rpy": ('label start:\n'
                       '    e "こんにちは" with dissolve\n'
                       '    "語り" nointeract\n'),
    })
    _renpy_export(game, lambda e: "EN " + e.original)
    text = (game / "game" / "script.rpy").read_text(encoding="utf-8")
    assert '    e "EN こんにちは" with dissolve\n' in text
    assert '    "EN 語り" nointeract\n' in text


def test_renpy_lone_backslash_survives():
    from translator.renpy import _escape_rpy
    assert _escape_rpy("a \\ b") == "a \\\\ b"            # literal backslash
    assert _escape_rpy("end\\") == "end\\\\"
    assert _escape_rpy("C:\\dir") == "C:\\\\dir"
    # real escapes stay escapes
    assert _escape_rpy('say \\"hi\\"') == 'say \\"hi\\"'
    assert _escape_rpy("one\\ntwo") == "one\\ntwo"
    assert _escape_rpy("a\\\\b") == "a\\\\b"
    # "\ " is kept only when the original uses that escape
    assert _escape_rpy("x\\ y", original="元\\ 文") == "x\\ y"


def test_renpy_lone_backslash_round_trips_through_export(tmp_path):
    from translator.renpy import RenPyParser
    game = _renpy_game(tmp_path, {"script.rpy": 'label start:\n    "一"\n'})
    _renpy_export(game, lambda e: "a \\ b")
    text = (game / "game" / "script.rpy").read_text(encoding="utf-8")
    assert '    "a \\\\ b"\n' in text           # Ren'Py shows: a \ b
    again = RenPyParser().load_project(str(game))
    assert again[0].original == "a \\\\ b"


def test_renpy_character_name_with_escaped_quote(tmp_path):
    from translator.renpy import RenPyParser
    game = _renpy_game(tmp_path, {
        "script.rpy": ('define m = Character("ミスター\\"X\\"", color="#fff")\n'
                       'label start:\n    m "こんにちは"\n'),
    })
    names = [e for e in RenPyParser().load_project(str(game))
             if e.field == "name"]
    assert names and names[0].original == 'ミスター\\"X\\"'
    _renpy_export(game, lambda e: 'Mr. "X"' if e.field == "name" else "Hi")
    text = (game / "game" / "script.rpy").read_text(encoding="utf-8")
    assert 'define m = Character("Mr. \\"X\\"", color="#fff")' in text


def test_migration_recognises_subfolder_renpy_ids():
    from translator.state_migration import _parse_event_id
    assert _parse_event_id("chapters/ch2.rpy/ch2/dialog_3", "renpy") == \
        ("chapters/ch2.rpy/ch2", "dialog", "3", "")
    assert _parse_event_id("script.rpy/start/choice_0", "renpy") == \
        ("script.rpy/start", "choice", "0", "")


# ── Wolf: stale translation over an updated line (FIXED) ─────────────

def test_wolf_commands_skip_changed_lines_and_count_them():
    from types import SimpleNamespace as NS
    from translator import wolfrpg
    p = wolfrpg.WolfRPGParser()
    cmds = [NS(code=wolfrpg.CMD_MESSAGE, string_args=["アリス：\n新しい台詞"]),
            NS(code=wolfrpg.CMD_MESSAGE, string_args=["アリス：\n同じ台詞"]),
            NS(code=wolfrpg.CMD_CHOICES, string_args=["はい", "やめる"])]
    tr = {"M/Ev1/p0/msg0": "Old EN", "M/Ev1/p0/msg1": "Same EN",
          "M/Ev1/p0/choice2_0": "Yes", "M/Ev1/p0/choice2_1": "No"}
    orig = {"M/Ev1/p0/msg0": "古い台詞", "M/Ev1/p0/msg1": "同じ台詞",
            "M/Ev1/p0/choice2_0": "はい", "M/Ev1/p0/choice2_1": "いいえ"}
    changed = {}
    assert p._apply_commands(cmds, "M/Ev1/p0", tr, orig, "M", changed)
    assert cmds[0].string_args[0] == "アリス：\n新しい台詞"   # kept
    assert cmds[1].string_args[0] == "アリス：\nSame EN"     # written
    assert cmds[2].string_args == ["Yes", "やめる"]
    assert changed == {"M": 2}


def test_wolf_database_skips_changed_strings(tmp_path):
    import struct
    from pathlib import Path
    from translator import wolfrpg

    def wstr(s):
        b = s.encode("cp932") + b"\x00"
        return struct.pack("<I", len(b)) + b

    def db_bytes(row):
        out = bytearray(b"\x00" * 10) + b"\x01" + struct.pack("<I", 1)
        out += wolfrpg.WolfDatabase.DAT_TYPE_SEP + struct.pack("<I", 0)
        out += struct.pack("<I", 1 + len(row)) + struct.pack("<I", 0x03E8)
        for k in range(len(row)):
            out += struct.pack("<I", 0x07D0 + k)
        out += struct.pack("<I", 1) + struct.pack("<I", 100)
        for s in row:
            out += wstr(s)
        return bytes(out + b"\x01")
    src, dst = tmp_path / "s.dat", tmp_path / "d.dat"
    src.write_bytes(db_bytes(["大剣", "盾"]))          # updated game data
    db = wolfrpg.WolfDatabase(Path(src))
    db._types = [{"name": "t", "fields": []}]
    p = wolfrpg.WolfRPGParser()
    reason = p._export_database(db, src, dst,
                                {"Type0/Data0/F0": "Sword", "Type0/Data0/F1": "Shield"},
                                {"Type0/Data0/F0": "剣", "Type0/Data0/F1": "盾"})
    assert reason is None and p._db_changed == 1
    assert dst.read_bytes() == db_bytes(["大剣", "Shield"])


# ── Wolf RPG Editor 3.x: detected, refused, reported (FIXED) ─────────
#
# Asuka (a WOLF RPG Editor 3.x game shipped with GamePro.exe) was not
# detected; once opened, every map and common event failed to parse (only
# logged), the UTF-8 database was read as cp932 (garbled text) and an
# export patched cp932 text into it.

_WOLF2_DAT = b"\x00W\x00\x00OL\x00FM\x00" + b"\x00" * 6
_WOLF3_DAT = b"\x00W\x00\x00OLUFM\x00" + b"\x00" * 6
_WOLF3_MPS = b"\x00" * 10 + b"WOLFM\x00" + b"U\x00\x00\x00d\x00\x00\x00"


def _wolf_game(tmp_path, dat=_WOLF3_DAT, mps=_WOLF3_MPS, exe="GamePro.exe"):
    game = tmp_path / "WolfGame"
    (game / "Data" / "BasicData").mkdir(parents=True)
    (game / "Data" / "MapData").mkdir()
    (game / exe).write_bytes(b"MZ")
    (game / "Data" / "BasicData" / "DataBase.dat").write_bytes(dat)
    (game / "Data" / "MapData" / "Map001.mps").write_bytes(mps)
    return game


def test_wolf_detects_gamepro_exe(tmp_path):
    from translator.engine_handler import detect_engine, WolfRPGHandler
    game = _wolf_game(tmp_path)
    assert detect_engine(str(game)) is WolfRPGHandler


def test_wolf3_marker_tells_versions_apart(tmp_path):
    from translator import wolfrpg
    g3 = _wolf_game(tmp_path / "a")
    assert wolfrpg.wolf3_marker(g3 / "Data") == "BasicData/DataBase.dat"
    g3map = _wolf_game(tmp_path / "b", dat=_WOLF2_DAT)
    assert wolfrpg.wolf3_marker(g3map / "Data") == "MapData/Map001.mps"
    g2 = _wolf_game(tmp_path / "c", dat=_WOLF2_DAT,
                    mps=b"\x00" * 10 + b"WOLFM\x00" + b"\x00" * 8)
    assert wolfrpg.wolf3_marker(g2 / "Data") is None


def test_wolf3_load_offers_nothing_and_says_why(tmp_path):
    from translator import wolfrpg
    p = wolfrpg.WolfRPGParser()
    assert p.load_project(str(_wolf_game(tmp_path))) == []
    assert any("Wolf RPG Editor 3.x isn't supported yet" in w
               for w in p.load_warnings)


def test_wolf3_export_is_refused_before_touching_files(tmp_path):
    from translator import wolfrpg
    game = _wolf_game(tmp_path)
    before = {f: f.read_bytes() for f in game.rglob("*") if f.is_file()}
    entries = [_entry("Database/DataBase/Type0/Data0/F0", "Database/DataBase",
                      "Type0/Data0/F0", "剣", "Sword")]
    with pytest.raises(wolfrpg.WolfVersionUnsupported,
                       match="Wolf RPG Editor 3.x isn't supported yet"):
        wolfrpg.WolfRPGParser().save_project(str(game), entries)
    after = {f: f.read_bytes() for f in game.rglob("*") if f.is_file()}
    assert after == before                       # no backup, nothing written


def test_wolf2_map_parse_failure_is_a_load_warning(tmp_path):
    from translator import wolfrpg
    game = _wolf_game(tmp_path, dat=_WOLF2_DAT,
                      mps=b"\x00" * 10 + b"WOLFM\x00" + b"\x00" * 8, exe="Game.exe")
    (game / "Data" / "MapData" / "Map002.mps").write_bytes(b"garbage")
    p = wolfrpg.WolfRPGParser()
    p.load_project(str(game))
    assert any(w.startswith("MapData/Map002.mps") for w in p.load_warnings)


def test_open_project_shows_load_warnings(qapp, monkeypatch, tmp_path):
    from translator.widgets import main_window as mw
    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    shown = []
    monkeypatch.setattr(mw.QMessageBox, "warning",
                        lambda _p, title, text, *a, **k: shown.append((title, text)))
    win = mw.MainWindow()
    try:
        win._open_project_path(str(_wolf_game(tmp_path)))
    finally:
        win._closing = True
        win._autosave_timer.stop()
        win.hide()
    titles = [t for t, _ in shown]
    assert "Some Game Files Could Not Be Read" in titles
    text = dict(shown)["Some Game Files Could Not Be Read"]
    assert "Wolf RPG Editor 3.x isn't supported yet" in text


def test_ace_unreadable_file_is_a_load_warning(tmp_path):
    from translator.rpgmaker_ace import RPGMakerAceParser
    game, _live = _ace_one_map_game(tmp_path, ["こんにちは"])
    (game / "Data" / "Map002.rvdata2").write_bytes(b"\x04\x08garbage")
    (game / "Data" / "CommonEvents.rvdata2").write_bytes(b"not marshal")
    p = RPGMakerAceParser()
    assert p.load_project(str(game))            # the good map still loads
    assert any(w.startswith("Map002.rvdata2") for w in p.load_warnings)
    assert any(w.startswith("CommonEvents.rvdata2") for w in p.load_warnings)


# ── "Re-open to pick up new or changed lines" (FIXED) ────────────────
#
# The export note told users to re-open the project after a game update,
# but a resume never re-read the game, and a fresh open read the live
# files (already English).  Resuming now merges the game's ORIGINAL text
# (from the backup the export refreshed) into the project by entry ID.

def _proj(entries):
    from translator.project_model import TranslationProject
    return TranslationProject(project_path="", entries=entries)


def test_merge_keeps_unchanged_adds_new_resets_changed():
    proj = _proj([
        _entry("a", "F", "dialog", "同じ", "Same", "reviewed"),
        _entry("b", "F", "dialog", "古い", "Old", "translated"),
        _entry("c", "F", "dialog", "残る", "Kept", "translated"),   # not in parse
    ])
    fresh = [_entry("a", "F", "dialog", "同じ", "", "untranslated"),
             _entry("b", "F", "dialog", "新しい", "", "untranslated"),
             _entry("d", "F", "dialog", "追加", "", "untranslated")]
    stats = proj.merge_fresh_originals(fresh)
    assert stats == {"status": "merged", "new": 1, "changed": 1, "unchanged": 1}
    by = {e.id: e for e in proj.entries}
    assert (by["a"].translation, by["a"].status) == ("Same", "reviewed")
    assert (by["b"].original, by["b"].translation, by["b"].status) == \
        ("新しい", "", "untranslated")
    assert proj.migration_orphans[0]["translation"] == "Old"
    assert by["c"].translation == "Kept"          # never removed
    assert by["d"].status == "untranslated"


def test_merge_refuses_when_most_translated_lines_would_change():
    proj = _proj([_entry(str(i), "F", "dialog", f"原文{i}", f"EN{i}")
                  for i in range(4)])
    fresh = [_entry(str(i), "F", "dialog", f"English {i}", "", "untranslated")
             for i in range(3)]
    stats = proj.merge_fresh_originals(fresh)
    assert stats["status"] == "low_match"
    assert [e.translation for e in proj.entries] == ["EN0", "EN1", "EN2", "EN3"]
    assert not proj.migration_orphans


def test_mv_game_update_lines_are_merged_after_export(mv_game, mv_parser):
    from translator.project_model import TranslationProject
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    mv_parser.save_project(mv_game, entries)
    # game update: item 1 renamed, a new item added (live file replaced)
    items_path = os.path.join(mv_game, "data", "Items.json")
    with open(os.path.join(mv_game, "data_original", "Items.json"),
              encoding="utf-8-sig") as f:
        items = json.load(f)
    items[1]["name"] = "ハイポーション"
    items.append({"id": len(items), "name": "アップデート品",
                  "description": "", "note": ""})
    with open(items_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)
    with pytest.raises(OSError):                 # stale line is reported
        mv_parser.save_project(mv_game, entries)
    assert mv_parser.last_export_notes and \
        "Re-open the project" in mv_parser.last_export_notes[0]
    # resume: merge the refreshed originals
    proj = TranslationProject(project_path=mv_game, entries=entries)
    stats = proj.merge_fresh_originals(mv_parser.load_originals(mv_game))
    assert stats["status"] == "merged" and stats["changed"] == 1 and stats["new"] == 1
    by = {e.id: e for e in proj.entries}
    assert by["Items.json/1/name"].original == "ハイポーション"
    assert by["Items.json/1/name"].status == "untranslated"
    assert by["Items.json/2/name"].status == "reviewed"     # unchanged kept
    assert any(e.original == "アップデート品" for e in proj.entries)
    # the next export is complete again
    mv_parser.save_project(mv_game, proj.entries)


def test_resume_shows_merged_game_update(qapp, monkeypatch, tmp_path, mv_game, mv_parser):
    from translator.project_model import TranslationProject
    from translator.widgets import main_window as mw
    entries = mv_parser.load_project(mv_game)
    for e in entries:
        e.translation, e.status = "EN " + e.id, "translated"
    proj = TranslationProject(project_path=mv_game, project_type="rpgmaker_mv",
                              entries=entries)
    state = os.path.join(mv_game, "_translation_state.json")
    proj.save_state(state)
    # game updated before the first export: a new item in the live data
    items_path = os.path.join(mv_game, "data", "Items.json")
    with open(items_path, encoding="utf-8-sig") as f:
        items = json.load(f)
    items.append({"id": len(items), "name": "新アイテム", "description": "",
                  "note": ""})
    with open(items_path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False)

    settings = tmp_path / "_settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_SETTINGS_FILE", str(settings))
    monkeypatch.setattr(mw.AIClient, "is_available", lambda self: False)
    monkeypatch.setattr(mw.AIClient, "list_models", lambda self: [])
    shown = []
    monkeypatch.setattr(mw.QMessageBox, "information",
                        lambda _p, title, text, *a, **k: shown.append(title))
    win = mw.MainWindow()
    try:
        assert win._restore_from_state(state)
    finally:
        win._closing = True
        win._autosave_timer.stop()
        win.hide()
    assert "Game Update Merged" in shown
    assert any(e.original == "新アイテム" for e in win.project.entries)
    assert all(e.status == "translated" for e in win.project.entries
               if e.original != "新アイテム")


# ── Low items (FIXED) ────────────────────────────────────────────────

_GAUGE_PLUGIN = '''/*:
 * @plugindesc test
 * @param 形状
 * @type select
 * @option 四角い形
 * @option 斜めな形
 * @default 四角い形
 *
 * @param 内部名
 * @type select
 * @option 表示用の名前
 * @value 内部の値
 *
 * @param 見出し
 * @desc ふつうの表示テキスト
 *
 * @param フォント登録
 * @type struct<font>[]
 *
 * @command 何か
 * @arg 種類
 * @option 引数の選択肢
 */
/*~struct~font:
 * @param フォント名
 * @desc フォントの呼び出しに使う
 */
'''


def test_mz_plugin_choices_and_font_keys_are_not_offered(mv_game, mv_parser):
    plugins_js = os.path.join(mv_game, "js", "plugins.js")
    os.makedirs(os.path.join(mv_game, "js", "plugins"), exist_ok=True)
    with open(os.path.join(mv_game, "js", "plugins", "TestGauge.js"), "w",
              encoding="utf-8") as f:
        f.write(_GAUGE_PLUGIN)
    fonts = json.dumps([json.dumps({"フォント名": "刻", "フォントファイル": "a.ttf"})],
                       ensure_ascii=False)
    plugins = [{"name": "TestGauge", "status": True, "description": "",
                "parameters": {"形状": "斜めな形", "内部名": "内部の値",
                               "見出し": "ゲームの見出し", "フォント登録": fonts}}]
    with open(plugins_js, "w", encoding="utf-8") as f:
        f.write("var $plugins =\n" + json.dumps(plugins, ensure_ascii=False) + ";\n")
    ids = {e.id for e in mv_parser.load_project(mv_game) if e.file == "plugins.js"}
    assert "plugins.js/TestGauge/見出し" in ids                 # display text
    assert "plugins.js/TestGauge/形状" not in ids               # @option value
    assert "plugins.js/TestGauge/内部名" not in ids             # @value
    assert not any("フォント名" in i for i in ids)              # font key


def test_mz_plugin_option_not_in_list_is_still_offered(mv_game, mv_parser):
    """A combo value the author typed in (not one of the options) is text."""
    os.makedirs(os.path.join(mv_game, "js", "plugins"), exist_ok=True)
    with open(os.path.join(mv_game, "js", "plugins", "TestGauge.js"), "w",
              encoding="utf-8") as f:
        f.write(_GAUGE_PLUGIN)
    plugins = [{"name": "TestGauge", "status": True, "description": "",
                "parameters": {"形状": "自由入力の文章"}}]
    with open(os.path.join(mv_game, "js", "plugins.js"), "w", encoding="utf-8") as f:
        f.write("var $plugins =\n" + json.dumps(plugins, ensure_ascii=False) + ";\n")
    ids = {e.id for e in mv_parser.load_project(mv_game) if e.file == "plugins.js"}
    assert "plugins.js/TestGauge/形状" in ids


@pytest.mark.parametrize("text, is_path", [
    ("BGM/foo.mp3", True),
    ("SE2/着信01 (2).wav", True),
    ("Picture/ポップ（大）_Good.PNG", True),
    ("BattleEffect/03 アイス.png", True),
    ("回復薬を使った。", False),
    ("HPが50回復する。", False),
    ("説明文 (改).", False),
])
def test_wolf_path_filter_handles_spaces_and_brackets(text, is_path):
    from translator.wolfrpg import _DB_SKIP_RE
    assert bool(_DB_SKIP_RE.match(text)) is is_path


def test_mv_atomic_write_fsyncs_before_replace(tmp_path, monkeypatch):
    import translator.rpgmaker_mv as mv
    calls = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append("fsync"), real_fsync(fd)))
    monkeypatch.setattr(os, "replace", lambda a, b: (calls.append("replace"), real_replace(a, b)))
    target = tmp_path / "Map001.json"
    mv.RPGMakerMVParser._atomic_write_text(str(target), "{}")
    assert calls == ["fsync", "replace"]
    assert target.read_text(encoding="utf-8") == "{}"


def test_renpy_export_skips_line_whose_original_changed(tmp_path):
    from translator import game_io
    from translator.renpy import RenPyParser
    game = _renpy_game(tmp_path, {"script.rpy": ('define e = Character("エリカ")\n'
                                                 'label start:\n'
                                                 '    e "古い台詞"\n'
                                                 '    e "同じ台詞"\n')})
    p = RenPyParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "EN " + e.original, "reviewed"
    p.save_project(str(game), entries)
    live = game / "game" / "script.rpy"
    live.write_bytes(('define e = Character("エリカ")\nlabel start:\n'
                      '    e "新しい台詞"\n    e "同じ台詞"\n').encode("utf-8"))
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(game), entries)
    assert "script.rpy: 1 translated line(s) not written" in str(ei.value)
    text = live.read_text(encoding="utf-8")
    assert '    e "新しい台詞"\n' in text and '    e "EN 同じ台詞"\n' in text


def test_ace_blank_line_in_message_stays_in_place(tmp_path):
    """A message whose first 401 line is empty kept moving the blank line
    to the bottom, even on a no-op export (Liz: 6 maps)."""
    from translator.rpgmaker_ace import RPGMakerAceParser
    game, live = _ace_one_map_game(tmp_path, ["", "必要な堕落度: 0"])
    raw = live.read_bytes()
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = e.original, "translated"
    p.save_project(str(game), entries)
    assert rmc.plain(rmc.load(live.read_bytes())) == rmc.plain(rmc.load(raw))
    for e in entries:
        e.translation = "Required corruption: 0"
    p.save_project(str(game), entries)
    texts = [str(c.attributes["@parameters"][0]) for c in _ace_cmds(live)
             if c.attributes["@code"] == 401]
    assert texts == ["", "Required corruption: 0"]


# ── Every other engine: no translation over changed game text (FIXED) ─
#
# RPG Maker 2000/2003, Kirikiri, TyranoScript, SRPG Studio, CSV games and
# Crowd also wrote translations by position without checking the
# original, so after a game update the old translation replaced the new
# line.  Each now keeps the changed line and reports it.

def _rm2k_map(text, choice):
    from translator import rpgmaker_2k as rm

    def cmd(code, s=""):
        return rm.EventCommand(code, 0, s, rm._encode_str(s), [])
    cmds = [cmd(rm.CODE_SHOW_MESSAGE, text), cmd(rm.CODE_SHOW_CHOICE),
            cmd(rm.CODE_SHOW_CHOICE_OPT, choice),
            cmd(rm.CODE_SHOW_CHOICE_OPT, "いいえ"), cmd(0)]
    page = {rm.PAGE_COMMANDS: rm._write_commands(cmds)}
    ev = {rm.EVENT_NAME: rm._encode_str("EV"),
          rm.EVENT_PAGES: rm._write_array([(1, page)])}
    return rm._write_header("LcfMapUnit") + rm._write_chunks(
        {rm.MAP_EVENTS: rm._write_array([(1, ev)])}, terminate=False)


def test_rm2k_export_skips_lines_whose_original_changed(tmp_path):
    from translator import game_io
    from translator import rpgmaker_2k as rm
    (tmp_path / "RPG_RT.ldb").write_bytes(rm._write_header("LcfDataBase"))
    (tmp_path / "RPG_RT.lmt").write_bytes(b"")
    live = tmp_path / "Map0001.lmu"
    live.write_bytes(_rm2k_map("古い台詞", "はい"))
    p = rm.RPGMaker2KParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN " + e.original, "reviewed"
    p.save_project(str(tmp_path), entries)
    live.write_bytes(_rm2k_map("新しい台詞", "やめる"))      # game update
    (tmp_path / "Map0001_original.lmu").write_bytes(live.read_bytes())
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert "Map0001.lmu: 2 translated line(s) not written" in str(ei.value)
    got = {e.id: e.original for e in rm.RPGMaker2KParser().load_project(str(tmp_path))}
    assert got["Map0001.lmu/Ev1(EV)/p1/dialog_0"] == "新しい台詞"
    assert got["Map0001.lmu/Ev1(EV)/p1/choice_1_0"] == "やめる"
    assert got["Map0001.lmu/Ev1(EV)/p1/choice_1_1"] == "EN いいえ"  # unchanged: written


def test_rm2k_resume_merge_reads_original_backups(tmp_path):
    from translator import rpgmaker_2k as rm
    from translator.project_model import TranslationProject
    (tmp_path / "RPG_RT.ldb").write_bytes(rm._write_header("LcfDataBase"))
    (tmp_path / "RPG_RT.lmt").write_bytes(b"")
    live = tmp_path / "Map0001.lmu"
    live.write_bytes(_rm2k_map("古い台詞", "はい"))
    p = rm.RPGMaker2KParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN " + e.original, "reviewed"
    p.save_project(str(tmp_path), entries)              # live is English now
    (tmp_path / "Map0001_original.lmu").write_bytes(     # refreshed backup
        _rm2k_map("新しい台詞", "はい"))
    proj = TranslationProject(project_path=str(tmp_path), entries=entries)
    stats = proj.merge_fresh_originals(p.load_originals(str(tmp_path)))
    assert stats == {"status": "merged", "new": 0, "changed": 1, "unchanged": 2}
    by = {e.id: e for e in proj.entries}
    assert by["Map0001.lmu/Ev1(EV)/p1/dialog_0"].status == "untranslated"
    assert by["Map0001.lmu/Ev1(EV)/p1/choice_1_0"].status == "reviewed"


def _kirikiri_game(tmp_path, body: bytes):
    ks = tmp_path / "data" / "scenario" / "a.ks"
    ks.parent.mkdir(parents=True, exist_ok=True)
    ks.write_bytes(body)
    return ks


def test_kirikiri_export_skips_line_whose_original_changed(tmp_path):
    from translator import game_io
    from translator.kirikiri import KirikiriParser
    ks = _kirikiri_game(tmp_path, "*start\r\n古い台詞です[p]\r\n\r\n同じ台詞です[p]\r\n"
                        .encode("cp932"))
    p = KirikiriParser()
    entries = p.load_project(str(tmp_path))
    assert len(entries) == 2
    for e in entries:
        e.translation, e.status = "EN line", "reviewed"
    p.save_project(str(tmp_path), entries)
    update = "*start\r\n新しい台詞です[p]\r\n\r\n同じ台詞です[p]\r\n".encode("cp932")
    ks.write_bytes(update)
    (tmp_path / "data" / "scenario_original" / "a.ks").write_bytes(update)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert "a.ks: 1 translated line(s) not written" in str(ei.value)
    text = ks.read_bytes().decode("cp932")
    assert "新しい台詞です" in text and "EN line" in text


def test_kirikiri_undecodable_byte_is_not_a_change(tmp_path):
    from translator.kirikiri import KirikiriParser
    body = "*start\r\n".encode("cp932") + b"\x81\x20" + "台詞です[p]\r\n".encode("cp932")
    ks = _kirikiri_game(tmp_path, body)
    p = KirikiriParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN line", "translated"
    p.save_project(str(tmp_path), entries)          # no "not written" error
    assert b"EN line" in ks.read_bytes()


def test_kirikiri_resume_merge_reads_backup(tmp_path):
    from translator.kirikiri import KirikiriParser
    from translator.project_model import TranslationProject
    _kirikiri_game(tmp_path, "*start\r\n古い台詞です[p]\r\n\r\n二つ目[p]\r\n\r\n三つ目[p]\r\n"
                   .encode("cp932"))
    p = KirikiriParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN line", "reviewed"
    p.save_project(str(tmp_path), entries)
    (tmp_path / "data" / "scenario_original" / "a.ks").write_bytes(
        "*start\r\n新しい台詞です[p]\r\n\r\n二つ目[p]\r\n\r\n三つ目[p]\r\n".encode("cp932"))
    proj = TranslationProject(project_path=str(tmp_path), entries=entries)
    stats = proj.merge_fresh_originals(p.load_originals(str(tmp_path)))
    assert stats == {"status": "merged", "new": 0, "changed": 1, "unchanged": 2}
    assert any(e.original.startswith("新しい台詞") and e.status == "untranslated"
               for e in proj.entries)
    assert sum(e.status == "reviewed" for e in proj.entries) == 2


def _tyrano_game(tmp_path, ks_text, lang_value="進む"):
    ks = tmp_path / "data" / "scenario" / "first.ks"
    ks.parent.mkdir(parents=True, exist_ok=True)
    ks.write_text(ks_text, encoding="utf-8")
    lang = tmp_path / "tyrano" / "lang.js"
    lang.parent.mkdir(parents=True, exist_ok=True)
    lang.write_text('var tyrano_lang = {"word":{"go":"%s"}};\n' % lang_value,
                    encoding="utf-8")
    return ks, lang


def test_tyrano_export_skips_line_whose_original_changed(tmp_path):
    from translator import game_io
    from translator.tyranoscript import TyranoScriptParser
    ks, _lang = _tyrano_game(tmp_path, "*start\n古い台詞です[p]\n同じ台詞です[p]\n")
    p = TyranoScriptParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN " + e.id, "reviewed"
    p.save_project(str(tmp_path), entries)
    update = "*start\n新しい台詞です[p]\n同じ台詞です[p]\n"
    ks.write_text(update, encoding="utf-8")
    (tmp_path / "data" / "scenario_original" / "first.ks").write_text(
        update, encoding="utf-8")
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert "first.ks: 1 translated line(s) not written" in str(ei.value)
    text = ks.read_text(encoding="utf-8")
    assert "新しい台詞です" in text and "EN first.ks/line_3" in text


def test_tyrano_lang_js_value_changed_is_kept(tmp_path):
    from translator import game_io
    from translator.tyranoscript import TyranoScriptParser
    _ks, lang = _tyrano_game(tmp_path, "*start\n台詞です[p]\n")
    p = TyranoScriptParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN", "translated"
    p.save_project(str(tmp_path), entries)
    # the original lang.js (its .bak) now holds a different value
    (lang.parent / "lang.js.bak").write_text(
        'var tyrano_lang = {"word":{"go":"次へ"}};\n', encoding="utf-8")
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert "tyrano/lang.js: 1 translated line(s) not written" in str(ei.value)
    assert '"go":"次へ"' in lang.read_text(encoding="utf-8")


def test_tyrano_resume_merge_reads_backup(tmp_path):
    from translator.tyranoscript import TyranoScriptParser
    from translator.project_model import TranslationProject
    _tyrano_game(tmp_path, "*start\n一つ目[p]\n二つ目[p]\n三つ目[p]\n")
    p = TyranoScriptParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN", "reviewed"
    p.save_project(str(tmp_path), entries)
    (tmp_path / "data" / "scenario_original" / "first.ks").write_text(
        "*start\n新しい一つ目[p]\n二つ目[p]\n三つ目[p]\n", encoding="utf-8")
    proj = TranslationProject(project_path=str(tmp_path), entries=entries)
    stats = proj.merge_fresh_originals(p.load_originals(str(tmp_path)))
    assert stats["status"] == "merged" and stats["changed"] == 1 and stats["new"] == 0
    assert sum(e.status == "reviewed" for e in proj.entries) == len(proj.entries) - 1


def _srpg_dts(texts):
    """Unencrypted data.dts whose project blob holds *texts* (UTF-16 strings)."""
    import struct
    from translator import srpgstudio as sr
    blob = b"".join(b"\x01\x02\x03\x04" + struct.pack("<I", len((t + "\x00").encode("utf-16-le")))
                    + (t + "\x00").encode("utf-16-le") for t in texts) + b"END!"
    head = (sr._SDTS_MAGIC + struct.pack("<IIII", 0, 1000, 0, 0)
            + struct.pack("<I", 0) + b"\x00" * (4 * sr._SECTION_COUNT))
    head += b"\x00" * (sr._HEADER_SIZE - len(head))
    return head + blob


def _srpg_texts(path):
    """Strings in a data.dts built by _srpg_dts (reads its exact layout)."""
    import struct
    from translator import srpgstudio as sr
    blob = path.read_bytes()[sr._HEADER_SIZE:]
    out, pos = [], 0
    while blob[pos:pos + 4] == b"\x01\x02\x03\x04":
        n = struct.unpack_from("<I", blob, pos + 4)[0]
        out.append(blob[pos + 8:pos + 8 + n].decode("utf-16-le").rstrip("\x00"))
        pos += 8 + n
    return out


def test_srpg_duplicate_lines_keep_their_own_translation(tmp_path):
    """Two identical strings with different translations each keep theirs
    (the text-keyed map gave both the last one)."""
    from translator.srpgstudio import SRPGStudioParser
    (tmp_path / "data.dts").write_bytes(_srpg_dts(["はい", "はい"]))
    p = SRPGStudioParser()
    entries = p.load_project(str(tmp_path))
    assert [e.original for e in entries] == ["はい", "はい"]
    entries[0].translation, entries[0].status = "Yes", "translated"
    entries[1].translation, entries[1].status = "Sure", "reviewed"
    p.save_project(str(tmp_path), entries)
    got = _srpg_texts(tmp_path / "data.dts")
    assert got == ["Yes", "Sure"]


def test_srpg_export_reports_line_whose_original_is_gone(tmp_path):
    from translator import game_io
    from translator.srpgstudio import SRPGStudioParser
    (tmp_path / "data.dts").write_bytes(_srpg_dts(["古い台詞", "同じ台詞"]))
    p = SRPGStudioParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN " + e.original, "reviewed"
    p.save_project(str(tmp_path), entries)
    update = _srpg_dts(["新しい台詞", "同じ台詞"])
    (tmp_path / "data.dts").write_bytes(update)
    (tmp_path / "data_original.dts").write_bytes(update)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert "data.dts: 1 translated line(s) not written" in str(ei.value)
    got = _srpg_texts(tmp_path / "data.dts")
    assert got == ["新しい台詞", "EN 同じ台詞"]


def test_srpg_resume_merge_reads_backup(tmp_path):
    from translator.srpgstudio import SRPGStudioParser
    from translator.project_model import TranslationProject
    (tmp_path / "data.dts").write_bytes(_srpg_dts(["一つ目", "二つ目", "三つ目"]))
    p = SRPGStudioParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN", "reviewed"
    p.save_project(str(tmp_path), entries)
    (tmp_path / "data_original.dts").write_bytes(_srpg_dts(["新しい一つ目", "二つ目", "三つ目"]))
    proj = TranslationProject(project_path=str(tmp_path), entries=entries)
    stats = proj.merge_fresh_originals(p.load_originals(str(tmp_path)))
    assert stats == {"status": "merged", "new": 0, "changed": 1, "unchanged": 2}


def test_no_control_bytes_in_source_files():
    """Raw control bytes in source (a NUL or backspace typed through shell
    escaping) fail silently or confuse tools -- write escapes instead."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    files = [*root.joinpath("translator").rglob("*.py"),
             *root.joinpath("tests").glob("*.py"), root / "main.py"]
    bad = {}
    for f in files:
        n = len(re.findall(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", f.read_bytes()))
        if n:
            bad[str(f.relative_to(root))] = n
    assert bad == {}


def _csv_game(tmp_path, rows):
    path = tmp_path / "data" / "script.x"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "com,hen1,hen2,hen2_en\r\n" + "".join(f"s,s,{r},\r\n" for r in rows)
    path.write_bytes(text.encode("utf-8"))
    return path


def _csv_rows(path):
    import csv
    import io
    return list(csv.reader(io.StringIO(path.read_bytes().decode("utf-8"), newline="")))


def test_csv_inserted_row_does_not_shift_translations(tmp_path):
    """A game update that inserts a row moves every later line down: the
    old translations must not land on the wrong rows."""
    from translator import game_io
    from translator.csv_game import CSVGameParser
    path = _csv_game(tmp_path, ["一行目", "二行目"])
    p = CSVGameParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN " + e.original, "reviewed"
    p.save_project(str(tmp_path), entries)
    update_rows = ["一行目", "新しい行", "二行目"]
    path.write_bytes(_csv_game(tmp_path, update_rows).read_bytes())
    (tmp_path / "data_original" / "script.x").write_bytes(path.read_bytes())
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert "script.x: 1 translated line(s) not written" in str(ei.value)
    rows = _csv_rows(path)
    assert rows[1][3] == "EN 一行目"
    assert rows[2][2:] == ["新しい行", ""]     # never got "EN 二行目"
    assert rows[3][3] == ""                    # row_3's entry was 二行目, now row 4


def test_csv_resume_merge_reads_backup(tmp_path):
    from translator.csv_game import CSVGameParser
    from translator.project_model import TranslationProject
    _csv_game(tmp_path, ["一行目", "二行目", "三行目"])
    p = CSVGameParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN", "reviewed"
    p.save_project(str(tmp_path), entries)
    (tmp_path / "data_original" / "script.x").write_bytes(
        "com,hen1,hen2,hen2_en\r\ns,s,変更,\r\ns,s,二行目,\r\ns,s,三行目,\r\n".encode("utf-8"))
    proj = TranslationProject(project_path=str(tmp_path), entries=entries)
    stats = proj.merge_fresh_originals(p.load_originals(str(tmp_path)))
    assert stats == {"status": "merged", "new": 0, "changed": 1, "unchanged": 2}


def _crowd_sce(lines):
    from translator import crowd
    body = "$ main $" + "".join(
        f"  {i} w00000{i + 1}a@!アリス@n{t}" for i, t in enumerate(lines))
    body += f"  {len(lines)} CF 1-2 "
    return bytes(crowd.encrypt_sce(body.encode("cp932")))


def _crowd_texts(path):
    """Decrypted script text of a .sce file."""
    from translator import crowd
    return bytes(crowd.decrypt_sce(path.read_bytes())).decode("cp932", "replace")


def test_crowd_export_skips_line_whose_original_changed(tmp_path):
    from translator import crowd, game_io
    sce = tmp_path / "game.sce"
    sce.write_bytes(_crowd_sce(["古い台詞", "同じ台詞"]))
    p = crowd.CrowdParser()
    entries = p.load_project(str(tmp_path))
    assert len(entries) == 2
    for e in entries:
        e.translation, e.status = "EN line", "reviewed"
    p.save_project(str(tmp_path), entries)
    update = _crowd_sce(["新しい台詞", "同じ台詞"])
    sce.write_bytes(update)
    (tmp_path / "sce_original" / "game.sce").write_bytes(update)
    with pytest.raises(game_io.PartialExportError) as ei:
        p.save_project(str(tmp_path), entries)
    assert "game.sce: 1 translated line(s) not written" in str(ei.value)
    text = _crowd_texts(sce)
    assert "新しい台詞" in text and "EN line" in text


def test_crowd_resume_merge_reads_backup(tmp_path):
    from translator import crowd
    from translator.project_model import TranslationProject
    (tmp_path / "game.sce").write_bytes(_crowd_sce(["一つ目", "二つ目", "三つ目"]))
    p = crowd.CrowdParser()
    entries = p.load_project(str(tmp_path))
    for e in entries:
        e.translation, e.status = "EN", "reviewed"
    p.save_project(str(tmp_path), entries)
    (tmp_path / "sce_original" / "game.sce").write_bytes(
        _crowd_sce(["新しい一つ目", "二つ目", "三つ目"]))
    proj = TranslationProject(project_path=str(tmp_path), entries=entries)
    stats = proj.merge_fresh_originals(p.load_originals(str(tmp_path)))
    assert stats == {"status": "merged", "new": 0, "changed": 1, "unchanged": 2}
