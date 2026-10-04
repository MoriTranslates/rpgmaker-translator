"""Parse -> translate -> export round trips for text-based engines."""

import csv
import io
import os

import pytest

import fake_ai


def translate_all(entries):
    for e in entries:
        e.translation = fake_ai.fake_translate(e.original)
        e.status = "translated"
    return entries


# ── Ren'Py ───────────────────────────────────────────────────────────

SCRIPT_RPY = '''\
define t = Character("先生")
define m = Character("主人公")

label start:
    scene bg room
    t "おはようございます"
    "学校"
    m "{i}了解{/i}"
    menu:
        "学校":
            jump school
        "家に帰る":
            jump home
    return

label school:
    t "明日も会おう"
    return
'''


@pytest.fixture
def renpy_game(tmp_path):
    root = tmp_path / "rp"
    (root / "game").mkdir(parents=True)
    (root / "renpy").mkdir()
    (root / "game" / "script.rpy").write_text(SCRIPT_RPY, encoding="utf-8")
    return root


def test_renpy_parse(renpy_game):
    from translator.renpy import RenPyParser
    assert RenPyParser.is_renpy_project(str(renpy_game))
    ents = {e.id: e for e in RenPyParser().load_project(str(renpy_game))}
    assert ents["script.rpy/define/t"].original == "先生"
    assert ents["script.rpy/start/dialog_0"].original == "おはようございます"
    assert ents["script.rpy/start/dialog_0"].context.startswith("[Speaker: 先生]")
    assert ents["script.rpy/start/dialog_1"].original == "学校"
    assert ents["script.rpy/start/dialog_2"].original == "{i}了解{/i}"
    assert ents["script.rpy/start/choice_3"].original == "学校"
    assert ents["script.rpy/start/choice_4"].original == "家に帰る"
    assert ents["script.rpy/school/dialog_0"].original == "明日も会おう"


def test_renpy_export_roundtrip(renpy_game):
    from translator.renpy import RenPyParser
    p = RenPyParser()
    entries = translate_all(p.load_project(str(renpy_game)))
    p.save_project(str(renpy_game), entries)
    out = (renpy_game / "game" / "script.rpy").read_text(encoding="utf-8")
    assert (renpy_game / "game_original" / "script.rpy").read_text(
        encoding="utf-8") == SCRIPT_RPY
    assert 'define t = Character("Teacher")' in out
    assert '    t "Good morning, sir"' in out
    assert '    "School"\n' in out
    assert '    m "{i}Understood{/i}"' in out
    assert '        "School":' in out
    assert '        "Go home":' in out
    assert '    t "See you tomorrow"' in out
    # Structure (non-text lines) untouched
    assert out.count("\n") == SCRIPT_RPY.count("\n")
    assert "    scene bg room\n" in out and "            jump school\n" in out
    # Idempotent re-export
    p.save_project(str(renpy_game), entries)
    assert (renpy_game / "game" / "script.rpy").read_text(encoding="utf-8") == out


def test_renpy_tags_protected_by_client(fake_llm):
    from translator.ai_client import AIClient
    c = AIClient()
    c.project_type = "renpy"
    assert c.translate("{i}了解{/i}") == "{i}Understood{/i}"
    assert "«CODE1»" in fake_llm.user_messages()[-1]


def test_renpy_escaped_quotes_roundtrip_identity(tmp_path):
    from translator.renpy import RenPyParser
    src = 'label start:\n    e "彼は\\"テスト\\"と言った"\n'
    root = tmp_path / "rp"
    (root / "game").mkdir(parents=True)
    (root / "game" / "script.rpy").write_text(src, encoding="utf-8")
    p = RenPyParser()
    entries = p.load_project(str(root))
    for e in entries:       # identity "translation"
        e.translation, e.status = e.original, "translated"
    p.save_project(str(root), entries)
    assert (root / "game" / "script.rpy").read_text(encoding="utf-8") == src


def test_renpy_choice_with_quote_is_escaped(renpy_game):
    from translator.renpy import RenPyParser
    p = RenPyParser()
    entries = translate_all(p.load_project(str(renpy_game)))
    choice = next(e for e in entries if e.id == "script.rpy/start/choice_3")
    choice.translation = 'Say "hi"'
    p.save_project(str(renpy_game), entries)
    out = (renpy_game / "game" / "script.rpy").read_text(encoding="utf-8")
    assert '        "Say \\"hi\\"":' in out


def test_renpy_numeric_narration_does_not_shift_ids(tmp_path):
    from translator.renpy import RenPyParser
    src = ('label start:\n    "100"\n    t "おはようございます"\n'
           '    t "明日も会おう"\n')
    root = tmp_path / "rp"
    (root / "game").mkdir(parents=True)
    (root / "game" / "script.rpy").write_text(src, encoding="utf-8")
    p = RenPyParser()
    entries = translate_all(p.load_project(str(root)))
    p.save_project(str(root), entries)
    out = (root / "game" / "script.rpy").read_text(encoding="utf-8")
    assert out == ('label start:\n    "100"\n    t "Good morning, sir"\n'
                   '    t "See you tomorrow"\n')


# ── CSV game (.x files) ──────────────────────────────────────────────

CSV_ROWS = [
    ["com", "hen1", "hen2", "hen2_en"],
    ["s", "s", "おはようございます", ""],
    ["s", "d", "了解", ""],
    ["str_01", "", "学校", ""],
    ["s", "s", "明日も会おう", ""],
]


def _write_csv(path, rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")


def _read_csv(path):
    return list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))


@pytest.fixture
def csv_game(tmp_path):
    root = tmp_path / "csvgame"
    (root / "data").mkdir(parents=True)
    _write_csv(root / "data" / "scene01.x", CSV_ROWS)
    return root


def test_csv_parse(csv_game):
    from translator.csv_game import CSVGameParser
    assert CSVGameParser.is_csv_game_project(str(csv_game))
    ents = CSVGameParser().load_project(str(csv_game))
    assert [e.original for e in ents] == ["おはようございます", "了解", "学校", "明日も会おう"]
    assert [e.id for e in ents] == [f"scene01.x/row_{i}/col_2" for i in (2, 3, 4, 5)]
    assert ents[2].field == "narration"
    assert all(e.status == "untranslated" for e in ents)


def test_csv_export_backup_and_idempotency(csv_game):
    from translator.csv_game import CSVGameParser
    p = CSVGameParser()
    entries = translate_all(p.load_project(str(csv_game)))
    p.save_project(str(csv_game), entries)
    assert _read_csv(csv_game / "data_original" / "scene01.x") == CSV_ROWS
    first = (csv_game / "data" / "scene01.x").read_bytes()
    p.save_project(str(csv_game), entries)
    assert (csv_game / "data" / "scene01.x").read_bytes() == first
    rows = _read_csv(csv_game / "data" / "scene01.x")
    assert rows[1][3] == "Good morning, sir"
    assert rows[2][3] == "Understood"
    # JP column is untouched
    assert [r[2] for r in rows] == [r[2] for r in CSV_ROWS]


def test_csv_export_includes_last_row(csv_game):
    from translator.csv_game import CSVGameParser
    p = CSVGameParser()
    entries = translate_all(p.load_project(str(csv_game)))
    p.save_project(str(csv_game), entries)
    rows = _read_csv(csv_game / "data" / "scene01.x")
    assert rows[4][3] == "See you tomorrow"


def test_csv_reload_picks_up_existing_translations(csv_game):
    from translator.csv_game import CSVGameParser
    p = CSVGameParser()
    p.save_project(str(csv_game), translate_all(p.load_project(str(csv_game))))
    again = {e.id: e for e in p.load_project(str(csv_game))}
    assert again["scene01.x/row_2/col_2"].translation == "Good morning, sir"
    assert again["scene01.x/row_2/col_2"].status == "translated"
