"""Show Choices options that are image file names are not machine-translated.

Real case (My Happiness ≠ Her Happiness 2): kz_PictureChoices reads the
choice text ``C_はい|350|200`` as "show img/choices/C_はい.png at 350,200".
Translating it to ``C_Yes|350|200`` made the game stop at the first choice
with "Failed to load: img/choices/C_Yes.png".  Such options are listed as
skipped; only a Reviewed translation is written on export.
"""

import json
import os

import pytest
from test_mv_export import _read, build_game

from translator.rpgmaker_mv import RPGMakerMVParser, _is_picture_choice

PIC = "C_はい|350|200"


def _set_choices(game, options):
    path = os.path.join(game, "data", "Map001.json")
    data = _read(path)
    for cmd in data["events"][1]["pages"][0]["list"]:
        if cmd["code"] == 102:
            cmd["parameters"][0] = options
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _add_image(game, name, folder="choices"):
    d = os.path.join(game, "img", folder)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, name), "wb").close()


def _choices_in_game(game):
    data = _read(os.path.join(game, "data", "Map001.json"))
    for cmd in data["events"][1]["pages"][0]["list"]:
        if cmd["code"] == 102:
            return cmd["parameters"][0]
    raise AssertionError("no Show Choices command")


def _choice(entries, original):
    return next(e for e in entries if e.field == "choice" and e.original == original)


@pytest.fixture
def parser():
    p = RPGMakerMVParser()
    p.game_font = None
    return p


@pytest.fixture
def game(tmp_path):
    g = build_game(str(tmp_path / "game"))
    _set_choices(g, [PIC, "いいえ"])
    _add_image(g, "C_はい.rpgmvp")
    return g


def test_helper_needs_an_exact_file_name():
    stems = frozenset({"C_はい", "はい2"})
    assert _is_picture_choice("C_はい|350|200", stems)
    assert _is_picture_choice("C_はい", stems)            # no coordinates
    assert not _is_picture_choice("はい", stems)          # only a prefix of はい2
    assert not _is_picture_choice("C_いいえ|1|2", stems)  # no such image
    assert not _is_picture_choice("|350|200", stems)      # empty name
    assert not _is_picture_choice("C_はい|1", frozenset())


def test_picture_choice_is_listed_as_skipped(game, parser):
    entries = parser.load_project(game)
    pic = _choice(entries, PIC)
    assert pic.status == "skipped"
    assert "Picture choice" in pic.context
    # an ordinary text choice next to it is still offered
    assert _choice(entries, "いいえ").status == "untranslated"


def test_old_project_translation_of_picture_choice_is_kept(game, parser):
    entries = parser.load_project(game)
    # as saved by an older version, before picture choices were skipped
    pic = _choice(entries, PIC)
    pic.translation, pic.status = "C_Yes|350|200", "translated"
    txt = _choice(entries, "いいえ")
    txt.translation, txt.status = "No", "translated"
    parser.save_project(game, entries)
    assert _choices_in_game(game) == [PIC, "No"]
    assert parser.image_names_kept == 1


def test_reviewed_translation_of_picture_choice_is_written(game, parser):
    entries = parser.load_project(game)
    pic = _choice(entries, PIC)
    # the user redrew the button as C_Yes.png and says so explicitly
    pic.translation, pic.status = "C_Yes|350|200", "reviewed"
    parser.save_project(game, entries)
    assert _choices_in_game(game)[0] == "C_Yes|350|200"


def test_choice_sharing_only_a_prefix_with_an_image_is_translatable(tmp_path, parser):
    g = build_game(str(tmp_path / "game"))
    _set_choices(g, ["はい", "いいえ"])
    _add_image(g, "はい2.png", folder="pictures")
    entries = parser.load_project(g)
    assert _choice(entries, "はい").status == "untranslated"


def test_game_without_img_folder_behaves_as_before(tmp_path, parser):
    g = build_game(str(tmp_path / "game"))
    _set_choices(g, [PIC, "いいえ"])
    entries = parser.load_project(g)
    assert _choice(entries, PIC).status == "untranslated"
