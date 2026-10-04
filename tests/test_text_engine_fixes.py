"""Regression tests for text-engine parser fixes (Ren'Py, Tyrano, CSV, Kirikiri)."""

import csv
import io
import json
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402

from translator.csv_game import CSVGameParser  # noqa: E402
from translator.kirikiri import (KirikiriParser, _safe_xp3_target,  # noqa: E402
                                 extract_xp3, find_scenario_xp3)
from translator.renpy import RenPyParser  # noqa: E402
from translator.tyranoscript import TyranoScriptParser  # noqa: E402


def _write(path, text, encoding="utf-8", newline="\n"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding=encoding, newline=newline) as f:
        f.write(text)


def _read(path, encoding="utf-8"):
    with open(path, "r", encoding=encoding, newline="") as f:
        return f.read()


def _set(entries, mapping):
    by_id = {e.id: e for e in entries}
    for eid, text in mapping.items():
        by_id[eid].translation = text
        by_id[eid].status = "translated"


# ── Ren'Py ───────────────────────────────────────────────────────────

RPY = (
    'define e = Character("エミ")\n'
    '\n'
    'label start:\n'
    '    "ナレーション"\n'
    '    e "こんにちは \\"世界\\""\n'
    '    ""\n'
    '    e "さようなら"\n'
    '    menu:\n'
    '        "はい":\n'
    '            jump start\n'
)


@pytest.fixture
def rpy_game(tmp_path):
    _write(str(tmp_path / "game" / "script.rpy"), RPY)
    return str(tmp_path)


def test_renpy_ids_and_export(rpy_game):
    parser = RenPyParser()
    entries = parser.load_project(rpy_game)
    ids = [e.id for e in entries]
    assert ids == [
        "script.rpy/define/e",
        "script.rpy/start/dialog_0",
        "script.rpy/start/dialog_1",
        "script.rpy/start/dialog_2",   # empty "" line does not consume an index
        "script.rpy/start/choice_3",
    ]
    assert entries[2].original == 'こんにちは \\"世界\\"'

    _set(entries, {
        "script.rpy/define/e": 'Emi "E"',
        "script.rpy/start/dialog_0": "Narration",
        "script.rpy/start/dialog_1": 'Hello \\"World\\"',   # already escaped
        "script.rpy/start/dialog_2": 'Say "bye"',           # bare quotes
        "script.rpy/start/choice_3": 'Yes "sure"',
    })
    script = os.path.join(rpy_game, "game", "script.rpy")
    parser.save_project(rpy_game, entries)
    first = _read(script)
    lines = first.splitlines()
    assert lines[0] == 'define e = Character("Emi \\"E\\"")'
    assert lines[3] == '    "Narration"'
    assert lines[4] == '    e "Hello \\"World\\""'
    assert lines[5] == '    ""'
    assert lines[6] == '    e "Say \\"bye\\""'
    assert lines[8] == '        "Yes \\"sure\\"":'
    assert lines[9] == '            jump start'

    # Idempotent re-export
    parser.save_project(rpy_game, entries)
    assert _read(script) == first

    # Reverting all translations restores the original file content
    for e in entries:
        e.translation, e.status = "", "untranslated"
    parser.save_project(rpy_game, entries)
    assert _read(script) == RPY


def test_renpy_restore_without_backup_raises(rpy_game):
    with pytest.raises(FileNotFoundError):
        RenPyParser().restore_originals(rpy_game)


# ── TyranoScript ─────────────────────────────────────────────────────

KS = (
    "*start|スタート\n"
    '[chara_new name="akane" storage="a.png" jname="あかね"]\n'
    "[eval exp=\"f.me = 'わたし'\"]\n"
    "#akane:smile\n"
    "こんにちは[p]\n"
    '[glink text="はい" target="*start"]\n'
)


@pytest.fixture
def tyrano_game(tmp_path):
    _write(str(tmp_path / "data" / "scenario" / "first.ks"), KS)
    _write(str(tmp_path / "tyrano" / "lang.js"),
           'var tyrano_lang = {"word":{"go":"進む"}};\n')
    return str(tmp_path)


def test_tyrano_export(tyrano_game):
    parser = TyranoScriptParser()
    entries = parser.load_project(tyrano_game)
    ids = {e.id: e for e in entries}
    # label line is not extracted; '#akane:smile' is a speaker tag
    assert "first.ks/line_1" not in ids
    assert "first.ks/line_4" not in ids
    assert ids["first.ks/line_5"].context.startswith("[Speaker: あかね]")

    _set(entries, {
        "first.ks/jname/あかね": 'Akane "A"',
        "first.ks/eval/f.me/わたし": "I'm",
        "first.ks/line_5": "Hello[p]",
        "first.ks/choice/はい": "Yes [ok]",
        "_system/lang.js/go": 'Go "now"',
    })
    # An old save may hold a translation for the label line — must be ignored
    from translator.project_model import TranslationEntry
    entries.append(TranslationEntry(
        id="first.ks/line_1", file="first.ks", field="dialog",
        original="*start|スタート", translation="START", status="translated"))

    parser.save_project(tyrano_game, entries)
    ks = os.path.join(tyrano_game, "data", "scenario", "first.ks")
    lines = _read(ks).splitlines()
    assert lines[0] == "*start|スタート"
    assert lines[1] == '[chara_new name="akane" storage="a.png" jname="Akane \'A\'"]'
    assert lines[2] == "[eval exp=\"f.me = 'I\\'m'\"]"
    assert lines[4] == "Hello[p]"
    assert lines[5] == '[glink text="Yes&nbsp;ok" target="*start"]'

    lang = _read(os.path.join(tyrano_game, "tyrano", "lang.js"))
    obj = json.loads(lang[lang.index("{"):lang.rindex("}") + 1])
    assert obj["word"]["go"] == 'Go "now"'

    # Re-export is idempotent (lang.js read from backup)
    parser.save_project(tyrano_game, entries)
    assert _read(os.path.join(tyrano_game, "tyrano", "lang.js")) == lang

    parser.restore_originals(tyrano_game)
    assert _read(ks) == KS
    assert "進む" in _read(os.path.join(tyrano_game, "tyrano", "lang.js"))


def test_tyrano_crlf_line_ids_match_export(tmp_path):
    _write(str(tmp_path / "data" / "scenario" / "a.ks"),
           "[cm]\nあいう[p]\n\nかきく[p]\n", newline="\r\n")
    parser = TyranoScriptParser()
    entries = parser.load_project(str(tmp_path))
    assert [e.id for e in entries] == ["a.ks/line_2", "a.ks/line_4"]
    _set(entries, {"a.ks/line_2": "ABC[p]", "a.ks/line_4": "DEF[p]"})
    parser.save_project(str(tmp_path), entries)
    lines = _read(str(tmp_path / "data" / "scenario" / "a.ks")).splitlines()
    assert lines == ["[cm]", "ABC[p]", "", "DEF[p]"]


# ── CSV game ─────────────────────────────────────────────────────────

CSV_TEXT = ('com,hen1,hen2,hen2_en\r\n'
            's,s,"一行目\r\n二行目",\r\n'
            's,d,最後の行,\r\n')


@pytest.fixture
def csv_game(tmp_path):
    path = tmp_path / "data" / "script.x"
    os.makedirs(path.parent)
    path.write_bytes(CSV_TEXT.encode("utf-8"))
    return str(tmp_path)


def test_csv_multiline_cell_and_last_row(csv_game):
    parser = CSVGameParser()
    entries = parser.load_project(csv_game)
    assert [e.id for e in entries] == ["script.x/row_2/col_2",
                                       "script.x/row_3/col_2"]
    assert entries[0].original == "一行目\n二行目"
    _set(entries, {"script.x/row_2/col_2": "Line one\nline two",
                   "script.x/row_3/col_2": "Last line"})
    parser.save_project(csv_game, entries)

    raw = open(os.path.join(csv_game, "data", "script.x"), "rb").read()
    assert not raw.startswith(b"\xef\xbb\xbf")      # no BOM added
    assert raw.endswith(b"\r\n")
    rows = list(csv.reader(io.StringIO(raw.decode("utf-8"), newline="")))
    assert len(rows) == 3
    assert rows[1][3] == "Line one\nline two"
    assert rows[2][3] == "Last line"
    assert rows[1][2] == "一行目\r\n二行目"           # untouched cell preserved


def test_csv_does_not_overwrite_non_english_adjacent_column(tmp_path):
    text = "a,b,c\n1,日本語,データ\n2,テキスト,情報\n"
    _write(str(tmp_path / "data" / "s.x"), text)
    parser = CSVGameParser()
    entries = parser.load_project(str(tmp_path))
    assert entries and all(not e.translation for e in entries)
    for e in entries:
        e.translation, e.status = "X", "translated"
    parser.save_project(str(tmp_path), entries)
    assert _read(str(tmp_path / "data" / "s.x")) == text


def test_csv_wide_rows_no_index_error(tmp_path):
    _write(str(tmp_path / "data" / "s.x"),
           "a,b\n1,x,日本語,\n2,y,テキスト,\n")
    entries = CSVGameParser().load_project(str(tmp_path))
    assert [e.original for e in entries] == ["日本語", "テキスト"]


# ── Kirikiri ─────────────────────────────────────────────────────────

def _chunk(name, payload):
    return name + struct.pack("<Q", len(payload)) + payload


def _build_xp3(path, files):
    magic = b"XP3\x0D\x0A\x20\x0A\x1A\x8B\x67\x01"
    body = b""
    index = _chunk(b"hnfn", b"\x00" * 10)  # unknown chunk first — must be skipped
    offset = 11 + 8
    for name, data in files:
        seg_off = offset + len(body)
        body += data
        p = name.encode("utf-16le")
        info = struct.pack("<IQQH", 0, len(data), len(data), len(name)) + p
        segm = struct.pack("<IQQQ", 0, seg_off, len(data), len(data))
        index += _chunk(b"File", _chunk(b"info", info) + _chunk(b"segm", segm))
    index_offset = offset + len(body)
    blob = (magic + struct.pack("<Q", index_offset) + body
            + b"\x00" + struct.pack("<Q", len(index)) + index)
    with open(path, "wb") as f:
        f.write(blob)


def test_xp3_skips_traversal_and_unknown_chunks(tmp_path):
    xp3 = str(tmp_path / "data.xp3")
    _build_xp3(xp3, [("scenario/a.ks", b"ok"),
                     ("../evil.ks", b"bad"),
                     ("C:/evil2.ks", b"bad")])
    out = tmp_path / "out"
    extracted = extract_xp3(xp3, str(out))
    assert extracted == ["scenario/a.ks"]
    assert (out / "scenario" / "a.ks").read_bytes() == b"ok"
    assert not (tmp_path / "evil.ks").exists()
    assert find_scenario_xp3(str(tmp_path)) == xp3


def test_safe_xp3_target(tmp_path):
    base = str(tmp_path)
    assert _safe_xp3_target(base, "a/b.ks") == os.path.join(base, "a", "b.ks")
    for bad in ("../x", "a/../../x", "C:\\x", "..\\x", ""):
        assert _safe_xp3_target(base, bad) is None
    # leading slashes are treated as archive-relative, kept inside base
    assert _safe_xp3_target(base, "/abs/x") == os.path.join(base, "abs", "x")


KAG = '@name chara="アキ"\n一行目\n\n二行目\n@e\n'


@pytest.mark.parametrize("encoding,bom", [("cp932", b""),
                                           ("utf-16-le", b"\xff\xfe")])
def test_kirikiri_blank_line_block_and_encoding(tmp_path, encoding, bom):
    ks = tmp_path / "data" / "scenario" / "a.ks"
    os.makedirs(ks.parent)
    ks.write_bytes(bom + KAG.encode(encoding))
    parser = KirikiriParser()
    entries = parser.load_project(str(tmp_path))
    assert [e.id for e in entries] == ["a.ks/dialogue/1"]
    assert entries[0].original == "一行目\n二行目"
    _set(entries, {"a.ks/dialogue/1": "First \u2014 line\nSecond line"})
    parser.save_project(str(tmp_path), entries)

    raw = ks.read_bytes()
    assert raw.startswith(bom)
    text = raw[len(bom):].decode(encoding)
    lines = text.replace("\r\n", "\n").split("\n")
    assert lines[:5] == ['@name chara="アキ"',
                         "First -- line" if encoding == "cp932"
                         else "First \u2014 line",
                         "", "Second line", "@e"]

    # Reverting resets the file from backup
    entries[0].translation, entries[0].status = "", "untranslated"
    parser.save_project(str(tmp_path), entries)
    assert ks.read_bytes() == bom + KAG.encode(encoding)


def test_kirikiri_restore_without_backup_raises(tmp_path):
    os.makedirs(tmp_path / "data" / "scenario")
    with pytest.raises(FileNotFoundError):
        KirikiriParser().restore_originals(str(tmp_path))
