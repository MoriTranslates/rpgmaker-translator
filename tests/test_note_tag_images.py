"""Note-tag values that name image files are not machine-translated.

Real case (Incubus Quest): the EnemyAnimetion plugin reads
``<baseName:ルキウス騎乗待機>`` and loads ルキウス騎乗待機1.png, 2.png ...
Exporting "Lucius Riding Wait" there broke every enemy animation.
Such values are listed as skipped; only a Reviewed translation is written.
"""

import json
import os
import zipfile

import pytest
from test_mv_export import _read, build_game

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


def _status(entries):
    return {e.id: e.status for e in entries if "/note/" in e.id}


def _get(entries, eid):
    return next(e for e in entries if e.id == eid)


def test_image_names_are_listed_as_skipped(game, parser):
    entries = parser.load_project(game)
    st = _status(entries)
    assert st["Actors.json/1/note/baseName/0"] == "skipped"     # frame prefix
    assert st["Actors.json/1/note/pic/0"] == "skipped"          # exact file name
    assert "Image file name" in _get(entries, "Actors.json/1/note/pic/0").context
    # display text still offered, even though 精力ゲージ.png starts with 精力
    assert st["Actors.json/1/note/custom_mp_text/0"] == "untranslated"
    assert st["Actors.json/1/note/custom_mp_text/1"] == "untranslated"


def test_old_project_machine_translation_of_image_name_is_kept_and_counted(game, parser):
    entries = parser.load_project(game)
    # as saved by an older version, before image names were skipped
    bn = _get(entries, "Actors.json/1/note/baseName/0")
    bn.translation, bn.status = "Lucius Riding Wait", "translated"
    mp = _get(entries, "Actors.json/1/note/custom_mp_text/0")
    mp.translation, mp.status = "Stamina", "translated"
    parser.save_project(game, entries)
    note = _read(os.path.join(game, "data", "Actors.json"))[1]["note"]
    assert f"<baseName:{FRAME_BASE}>" in note
    assert "<custom_mp_text:Stamina,体力>" in note
    assert parser.image_names_kept == 1


def test_reviewed_image_name_is_written(game, parser):
    # a display word that shares an image's name: the user reviews it by hand
    entries = parser.load_project(game)
    pic = _get(entries, "Actors.json/1/note/pic/0")
    pic.translation, pic.status = "Portrait", "reviewed"
    parser.save_project(game, entries)
    note = _read(os.path.join(game, "data", "Actors.json"))[1]["note"]
    assert "<pic:Portrait>" in note
    assert parser.image_names_kept == 0


def test_patch_zip_keeps_image_name(game, parser, tmp_path):
    entries = parser.load_project(game)
    bn = _get(entries, "Actors.json/1/note/baseName/0")
    bn.translation, bn.status = "Lucius Riding Wait", "translated"
    zp = str(tmp_path / "patch.zip")
    parser.export_patch_zip(game, entries, zp, game_title="T")
    with zipfile.ZipFile(zp) as zf:
        name = next(n for n in zf.namelist() if n.endswith("Actors.json"))
        actors = json.loads(zf.read(name).decode("utf-8-sig"))
    assert f"<baseName:{FRAME_BASE}>" in actors[1]["note"]
    assert parser.image_names_kept == 1


def test_www_layout_finds_images(tmp_path, parser):
    g = build_game(str(tmp_path / "g"), js_under_www=True)
    _set_note(os.path.join(g, "www"), f"<baseName:{FRAME_BASE}>")
    _add_images(os.path.join(g, "www"), f"{FRAME_BASE}_01.png")
    entries = parser.load_project(g)
    assert _status(entries)["Actors.json/1/note/baseName/0"] == "skipped"
    bn = _get(entries, "Actors.json/1/note/baseName/0")
    bn.translation, bn.status = "Lucius Riding Wait", "translated"
    parser.save_project(g, entries)
    note = _read(os.path.join(g, "www", "data", "Actors.json"))[1]["note"]
    assert f"<baseName:{FRAME_BASE}>" in note


def test_game_without_img_folder_still_extracts_tags(game, parser):
    import shutil
    shutil.rmtree(os.path.join(game, "img"))
    st = _status(parser.load_project(game))
    assert st["Actors.json/1/note/baseName/0"] == "untranslated"


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
