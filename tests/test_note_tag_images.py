"""Note-tag values that name image files are never translated.

Real case (Incubus Quest): the EnemyAnimetion plugin reads
``<baseName:ルキウス騎乗待機>`` and loads ルキウス騎乗待機1.png, 2.png ...
Exporting "Lucius Riding Wait" there broke every enemy animation.
"""

import json
import os

import pytest
from test_mv_export import _read, build_game

from translator.project_model import TranslationEntry
from translator.rpgmaker_mv import RPGMakerMVParser, _is_image_ref

FRAME_BASE = "ルキウス騎乗待機"


def _set_note(game, note):
    path = os.path.join(game, "data", "Actors.json")
    data = _read(path)
    data[1]["note"] = note
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _add_images(game, *names, folder="enemies"):
    d = os.path.join(game, "img", folder)
    os.makedirs(d, exist_ok=True)
    for n in names:
        open(os.path.join(d, n), "wb").close()


@pytest.fixture
def parser():
    p = RPGMakerMVParser()
    p.game_font = None
    return p


@pytest.fixture
def game(tmp_path):
    g = build_game(str(tmp_path / "game"))
    _set_note(g, f"<baseName:{FRAME_BASE}>\n<custom_mp_text:精力,体力>\n<pic:立ち絵>")
    _add_images(g, f"{FRAME_BASE}1.rpgmvp", f"{FRAME_BASE}12.rpgmvp")
    _add_images(g, "立ち絵.png", "精力ゲージ.png", folder="pictures")
    return g


def _note_ids(entries):
    return {e.id for e in entries if "/note/" in e.id}


def test_image_names_are_not_extracted(game, parser):
    ids = _note_ids(parser.load_project(game))
    assert "Actors.json/1/note/baseName/0" not in ids      # frame prefix
    assert "Actors.json/1/note/pic/0" not in ids           # exact file name
    # display text still offered, even though 精力ゲージ.png starts with 精力
    assert "Actors.json/1/note/custom_mp_text/0" in ids
    assert "Actors.json/1/note/custom_mp_text/1" in ids


def test_old_project_translation_of_image_name_is_not_exported(game, parser):
    entries = parser.load_project(game)
    # an entry saved by an older version, before image names were skipped
    entries.append(TranslationEntry(
        id="Actors.json/1/note/baseName/0", file="Actors.json",
        field="note_tag:baseName", original=FRAME_BASE,
        translation="Lucius Riding Wait", status="translated"))
    mp = next(e for e in entries if e.id == "Actors.json/1/note/custom_mp_text/0")
    mp.translation, mp.status = "Stamina", "translated"
    parser.save_project(game, entries)
    note = _read(os.path.join(game, "data", "Actors.json"))[1]["note"]
    assert f"<baseName:{FRAME_BASE}>" in note
    assert "<custom_mp_text:Stamina,体力>" in note


def test_www_layout_finds_images(tmp_path, parser):
    g = build_game(str(tmp_path / "g"), js_under_www=True)
    _set_note(os.path.join(g, "www"), f"<baseName:{FRAME_BASE}>")
    _add_images(os.path.join(g, "www"), f"{FRAME_BASE}_01.png")
    assert "Actors.json/1/note/baseName/0" not in _note_ids(parser.load_project(g))


def test_game_without_img_folder_still_extracts_tags(game, parser):
    import shutil
    shutil.rmtree(os.path.join(game, "img"))
    ids = _note_ids(parser.load_project(game))
    assert "Actors.json/1/note/baseName/0" in ids


@pytest.mark.parametrize("value,stems,expected", [
    ("待機", {"待機"}, True),
    ("待機", {"待機1"}, True),
    ("待機", {"待機_01"}, True),
    ("待機", {"待機-2-3"}, True),
    ("待機", {"待機中"}, False),          # a different word, not a frame
    ("待機", {"待機1a"}, False),
    ("待機", set(), False),
    ("[待機]", {"[待機]1"}, True),        # regex characters in names are literal
])
def test_is_image_ref(value, stems, expected):
    assert _is_image_ref(value, frozenset(stems)) is expected
