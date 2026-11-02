"""Findings from the 1.1.1 smoke test on copies of real games.

Tests for bugs that are fixed on this branch are plain tests.  Bugs that
are only reported (not fixed here) are ``xfail(strict=True)`` repros: they
document the bug, keep the suite green, and turn into a failure ("XPASS")
the moment someone fixes the bug, so the marker gets removed with the fix.
"""

import json
import os

import pytest

from translator.project_model import TranslationEntry


def _entry(eid, file, field, original, translation, status="translated"):
    return TranslationEntry(id=eid, file=file, field=field, original=original,
                            translation=translation, status=status)


# ── VX Ace: Marshal object links (FIXED on this branch) ─────────────
#
# Ruby's Marshal numbers every Float / String / Bignum / Regexp it writes,
# and "@n" links count them.  rubymarshal 1.2.10's writer does not, so any
# link written after a Float, or after a plain ``str`` (which is what a
# translated string is), pointed at the wrong object.  On "Liz ~The Tower
# and the Grimoire~" 209 of 414 data files came back with scrambled event
# commands (EventCommand slots holding MoveRoutes, etc.).

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

    out = ruby_loads((game / "Data" / "Map001.rvdata2").read_bytes())
    lst = out.attributes["@events"][1].attributes["@pages"][0].attributes["@list"]
    assert lst[3].attributes["@parameters"][1] is lst[4].attributes["@parameters"][0]
    assert lst[4].attributes["@parameters"][0].ruby_class_name == "RPG::MoveRoute"
    assert str(lst[1].attributes["@parameters"][0]) == "Hello"


# ── MV/MZ: one 401 command holding several lines (NOT fixed) ─────────
#
# Games previously machine-translated (e.g. "Utopia" 1.0: 1,705 of 5,053
# Show Text commands) keep a whole message in ONE 401 command with "\n"
# inside.  The parser extracts it as a multi-line entry; the exporter then
# looks for that many consecutive 401 commands, finds one, and skips the
# translation silently -- "Export Complete", text stays untranslated.

@pytest.mark.xfail(strict=True, reason="smoke finding: multi-line 401 skipped silently")
def test_mv_single_401_with_embedded_newline_is_exported(mv_game, mv_parser):
    path = os.path.join(mv_game, "data", "Map001.json")
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    data["events"][1]["pages"][0]["list"] = [
        {"code": 101, "indent": 0, "parameters": ["", 0, 0, 2, ""]},
        {"code": 401, "indent": 0, "parameters": ["一行目\n二行目"]},
        {"code": 0, "indent": 0, "parameters": []},
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    entries = mv_parser.load_project(mv_game)
    target = [e for e in entries if e.original == "一行目\n二行目"]
    assert target
    for e in entries:
        e.translation, e.status = "[EN] " + e.original, "translated"
    mv_parser.save_project(mv_game, entries)   # no error is reported...
    with open(path, encoding="utf-8") as f:
        live = json.load(f)
    texts = [c["parameters"][0] for c in live["events"][1]["pages"][0]["list"]
             if c["code"] == 401]
    assert any(t.startswith("[EN]") for t in texts)   # ...but it never lands


# ── Restore killed between its two renames (NOT fixed) ───────────────
#
# restore_tree renames data/ -> data.rmt-old, then data.rmt-restore -> data/.
# A kill between the two leaves no data/ folder.  restore_tree can recover
# from that, but MV/MZ and VX Ace restore_originals look up the data folder
# first and fail ("Could not find the game's data/ folder"), so the app
# can never run the recovery; the game will not start either.

@pytest.mark.xfail(strict=True, reason="smoke finding: interrupted restore not recoverable in-app")
def test_mv_restore_recovers_after_kill_between_renames(mv_game, mv_parser):
    mv_parser.save_project(mv_game, mv_parser.load_project(mv_game))
    data = os.path.join(mv_game, "data")
    os.rename(data, data + ".rmt-old")         # state after the kill
    mv_parser.restore_originals(mv_game)
    assert os.path.isdir(data)


@pytest.mark.xfail(strict=True, reason="smoke finding: interrupted restore not recoverable in-app")
def test_ace_restore_recovers_after_kill_between_renames(tmp_path):
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_writes
    game = tmp_path / "AceGame"
    (game / "Data").mkdir(parents=True)
    (game / "Data" / "Map001.rvdata2").write_bytes(ruby_writes(["JP"]))
    RPGMakerAceParser().save_project(str(game), [])
    os.rename(game / "Data", str(game / "Data") + ".rmt-old")
    RPGMakerAceParser().restore_originals(str(game))
    assert (game / "Data").is_dir()


# ── VX Ace: stale translation lands on an updated line (NOT fixed) ───
#
# Ace applies dialogue by command index without checking that the original
# text is still there (MV/MZ does, since 1.1.1).  After a game update that
# changes a line, the next export writes the OLD translation over the NEW
# Japanese line, and the backup refresh does not prevent it.

@pytest.mark.xfail(strict=True, reason="smoke finding: Ace export does not verify the original")
def test_ace_export_skips_line_whose_original_changed(tmp_path):
    from rubymarshal.classes import RubyObject, RubyString
    from translator.rpgmaker_ace import RPGMakerAceParser, ruby_loads, ruby_writes

    def game_map(text):
        cmds = [RubyObject("RPG::EventCommand", {"@code": 101, "@indent": 0,
                                                 "@parameters": [RubyString(""), 0, 0, 2]}),
                RubyObject("RPG::EventCommand", {"@code": 401, "@indent": 0,
                                                 "@parameters": [RubyString(text)]}),
                RubyObject("RPG::EventCommand", {"@code": 0, "@indent": 0,
                                                 "@parameters": []})]
        page = RubyObject("RPG::Event::Page", {"@list": cmds})
        ev = RubyObject("RPG::Event", {"@id": 1, "@name": RubyString("EV"),
                                       "@pages": [page]})
        return RubyObject("RPG::Map", {"@display_name": RubyString(""),
                                       "@events": {1: ev}})
    game = tmp_path / "Game"
    (game / "Data").mkdir(parents=True)
    live = game / "Data" / "Map001.rvdata2"
    live.write_bytes(ruby_writes(game_map("古い台詞")))
    p = RPGMakerAceParser()
    entries = p.load_project(str(game))
    for e in entries:
        e.translation, e.status = "Old line EN", "reviewed"
    p.save_project(str(game), entries)
    live.write_bytes(ruby_writes(game_map("新しい台詞")))    # game update
    p.save_project(str(game), entries)
    out = ruby_loads(live.read_bytes())
    lst = out.attributes["@events"][1].attributes["@pages"][0].attributes["@list"]
    assert str(lst[1].attributes["@parameters"][0]) == "新しい台詞"


# ── Ren'Py: lines that are never extracted (NOT fixed) ───────────────

def _renpy_game(tmp_path, files):
    game = tmp_path / "RenGame"
    (game / "renpy").mkdir(parents=True)
    for rel, text in files.items():
        p = game / "game" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return game


@pytest.mark.xfail(strict=True, reason="smoke finding: game/ subfolders are not scanned")
def test_renpy_scripts_in_subfolders_are_extracted(tmp_path):
    from translator.renpy import RenPyParser
    game = _renpy_game(tmp_path, {
        "script.rpy": 'label start:\n    "最初の台詞"\n',
        "chapters/ch2.rpy": 'label ch2:\n    "第二章の台詞"\n',
    })
    originals = {e.original for e in RenPyParser().load_project(str(game))}
    assert "第二章の台詞" in originals


@pytest.mark.xfail(strict=True, reason="smoke finding: say lines with a trailing clause are skipped")
def test_renpy_say_with_trailing_clause_is_extracted(tmp_path):
    from translator.renpy import RenPyParser
    game = _renpy_game(tmp_path, {
        "script.rpy": ('define e = Character("エリカ")\n'
                       'label start:\n'
                       '    e "こんにちは" with dissolve\n'),
    })
    originals = {e.original for e in RenPyParser().load_project(str(game))}
    assert "こんにちは" in originals
