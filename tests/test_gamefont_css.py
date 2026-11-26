"""The game-font swap keeps the rest of gamefont.css and uses a bundled font.

Real case (My Happiness ≠ Her Happiness 2): gamefont.css also hid the
video play button for the IIV plugin, and the game shipped its font as
www/fonts/mplus-1m-regular.ttf.  The old swap replaced the whole file with
``local("M+ 1m")``, which only finds installed fonts: the video rule was
lost and players got Courier New.
"""

import os
import zipfile

import pytest
from test_mv_export import build_game

from translator.rpgmaker_mv import RPGMakerMVParser, build_gamefont_css

IIV_RULE = ("video::-webkit-media-controls-start-playback-button {\n"
            "    opacity: 0;\n}")
GAME_CSS = ('@font-face {\n    font-family: GameFont;\n'
            '    src: url("mplus-1m-regular.ttf");\n}\n\n' + IIV_RULE + "\n")


def test_other_rules_survive_and_face_is_replaced():
    css = build_gamefont_css(GAME_CSS, "Consolas", [])
    assert IIV_RULE in css
    assert css.count("@font-face") == 1
    assert 'local("Consolas")' in css and 'local("Courier New")' in css
    assert "mplus-1m-regular.ttf" not in css


def test_bundled_font_file_is_used_first():
    css = build_gamefont_css(GAME_CSS, "M+ 1m",
                             ["gamefont.css", "mplus-1m-regular.ttf"])
    assert 'src: url("mplus-1m-regular.ttf"), local("M+ 1m")' in css


def test_unrelated_font_file_is_not_used():
    css = build_gamefont_css("", "Arial", ["mplus-1m-regular.ttf", "notes.txt"])
    assert "url(" not in css


def test_css_without_a_gamefont_face_gets_one():
    css = build_gamefont_css(IIV_RULE, "Arial", [])
    assert css.startswith("@font-face")
    assert IIV_RULE in css


def test_other_font_faces_are_kept():
    other = '@font-face { font-family: Title; src: url("title.ttf"); }'
    css = build_gamefont_css(other + "\n" + GAME_CSS, "Arial", [])
    assert other in css
    assert css.count("GameFont") == 1


@pytest.fixture
def parser():
    p = RPGMakerMVParser()
    p.game_font = "M+ 1m"
    return p


@pytest.fixture
def game(tmp_path):
    g = build_game(str(tmp_path / "game"))
    fonts = os.path.join(g, "fonts")
    with open(os.path.join(fonts, "gamefont.css"), "w", encoding="utf-8") as f:
        f.write(GAME_CSS)
    open(os.path.join(fonts, "mplus-1m-regular.ttf"), "wb").close()
    return g


def _translate_all(entries):
    for e in entries:
        if e.status == "untranslated":
            e.translation, e.status = "EN", "translated"


def test_export_keeps_rules_uses_bundled_font_and_is_repeatable(game, parser):
    entries = parser.load_project(game)
    _translate_all(entries)
    parser.save_project(game, entries)
    parser.save_project(game, entries)           # second export: no growth
    with open(os.path.join(game, "fonts", "gamefont.css"), encoding="utf-8") as f:
        css = f.read()
    assert IIV_RULE in css
    assert css.count("@font-face") == 1
    assert 'url("mplus-1m-regular.ttf")' in css
    with open(os.path.join(game, "fonts", "gamefont_original.css"),
              encoding="utf-8") as f:
        assert f.read() == GAME_CSS              # original untouched


def test_patch_zip_gets_the_same_css(game, parser, tmp_path):
    entries = parser.load_project(game)
    _translate_all(entries)
    zp = str(tmp_path / "patch.zip")
    parser.export_patch_zip(game, entries, zp)
    with zipfile.ZipFile(zp) as z:
        name = next(n for n in z.namelist() if n.endswith("fonts/gamefont.css"))
        css = z.read(name).decode("utf-8")
    assert IIV_RULE in css
    assert 'url("mplus-1m-regular.ttf")' in css
