"""Patch zips must carry every file they enable.

A zip's plugins.js enabled the TranslationSplash plugin, but the zip left out
js/plugins/TranslationSplash.js and the splash images, so a fresh install
failed with "Failed to load: js/plugins/TranslationSplash.js".  Also covers
the drop-in layout (files at the game's real paths, no .bat scripts).
"""

import hashlib
import json
import os
import re
import shutil
import zipfile

import pytest

from translator import splash_generator
from translator.rpgmaker_mv import RPGMakerMVParser

KEY = "00112233445566778899aabbccddeeff"


# ── helpers ──────────────────────────────────────────────────────────

def _names(zp):
    with zipfile.ZipFile(zp) as zf:
        return set(zf.namelist())


def _read(zp, name):
    with zipfile.ZipFile(zp) as zf:
        return zf.read(name)


def _tree(root):
    out = {}
    for d, _dirs, files in os.walk(root):
        for fn in files:
            p = os.path.join(d, fn)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = hashlib.sha256(f.read()).hexdigest()
    return out


def _set_system(content_root, **fields):
    path = os.path.join(content_root, "data", "System.json")
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    data.update(fields)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _make_game(tmp_path, mv_game, kind, encrypted=False):
    """kind: "mv_root" (js/ at the top), "mv_www" (deployed MV), "mz"."""
    game = str(tmp_path / f"game_{kind}")
    if kind == "mv_www":
        os.makedirs(game)
        shutil.copytree(mv_game, os.path.join(game, "www"))
        content = os.path.join(game, "www")
        shutil.move(os.path.join(content, "package.json"),
                    os.path.join(game, "package.json"))
    else:
        shutil.copytree(mv_game, game)
        content = game
    if kind == "mz":
        with open(os.path.join(content, "js", "rmmz_core.js"), "w") as f:
            f.write("// MZ")
    if encrypted:
        _set_system(content, hasEncryptedImages=True, encryptionKey=KEY)
    # the game's own plugins exist as files, like in a real game
    os.makedirs(os.path.join(content, "js", "plugins"), exist_ok=True)
    for name in ("TestMessagePlugin", "NestedPlugin"):
        with open(os.path.join(content, "js", "plugins", name + ".js"),
                  "w") as f:
            f.write("// plugin")
    with open(os.path.join(game, "Game.exe"), "wb") as f:
        f.write(b"MZ")
    return game, content


def _enabled_plugins(zp, plugins_arc):
    text = _read(zp, plugins_arc).decode("utf-8")
    arr = re.search(r"var\s+\$plugins\s*=\s*(\[.*\])\s*;", text, re.DOTALL)
    return [p["name"] for p in json.loads(arr.group(1)) if p.get("status")]


def _prefixed(layout, rel):
    return rel if layout == "dropin" else "_translation/" + rel


def _export(parser, game, tmp_path, layout, **kw):
    entries = parser.load_project(game)
    for e in entries:
        if e.original and e.field != "speaker_name":
            e.translation, e.status = "EN", "translated"
    zp = str(tmp_path / f"out_{layout}.zip")
    parser.export_patch_zip(game, entries, zp, layout=layout, **kw)
    return zp


def _assert_no_missing_plugin_files(zp, plugins_arc, content_rel, content,
                                    layout):
    """The regression: every enabled plugin's .js is in the zip or the game."""
    names = _names(zp)
    for name in _enabled_plugins(zp, plugins_arc):
        rel = f"{content_rel}js/plugins/{name}.js"
        in_zip = _prefixed(layout, rel) in names
        in_game = os.path.isfile(os.path.join(content, "js", "plugins",
                                              name + ".js"))
        assert in_zip or in_game, f"{name}.js is in neither zip nor game"


@pytest.fixture
def parser():
    return RPGMakerMVParser()


# ── item 1: splash files ship ────────────────────────────────────────

@pytest.mark.parametrize("layout", ["install", "dropin"])
@pytest.mark.parametrize("kind,rel", [("mv_root", ""), ("mv_www", "www/"),
                                      ("mz", "")])
def test_zip_ships_splash_files(tmp_path, mv_game, parser, layout, kind, rel):
    game, content = _make_game(tmp_path, mv_game, kind)
    zp = _export(parser, game, tmp_path, layout, show_splash=True)
    names = _names(zp)
    assert _prefixed(layout, rel + "js/plugins/TranslationSplash.js") in names
    assert _prefixed(layout, rel + "img/system/TranslationSplash.png") in names
    assert "TranslationSplash" in _enabled_plugins(
        zp, _prefixed(layout, rel + "js/plugins.js"))
    _assert_no_missing_plugin_files(
        zp, _prefixed(layout, rel + "js/plugins.js"), rel, content, layout)


@pytest.mark.parametrize("layout", ["install", "dropin"])
@pytest.mark.parametrize("kind,rel,ext", [("mv_root", "", ".rpgmvp"),
                                          ("mv_www", "www/", ".rpgmvp"),
                                          ("mz", "", ".png_")])
def test_zip_ships_encrypted_splash_copy(tmp_path, mv_game, parser, layout,
                                         kind, rel, ext):
    game, _content = _make_game(tmp_path, mv_game, kind, encrypted=True)
    zp = _export(parser, game, tmp_path, layout, show_splash=True)
    enc = _prefixed(layout, f"{rel}img/system/TranslationSplash{ext}")
    assert enc in _names(zp)
    assert _read(zp, enc)[:5] == b"RPGMV"
    other = ".png_" if ext == ".rpgmvp" else ".rpgmvp"
    assert _prefixed(layout, f"{rel}img/system/TranslationSplash{other}") \
        not in _names(zp)


def test_encrypted_game_without_key_ships_without_the_splash(
        tmp_path, mv_game, parser):
    # 1.2.2 made a working package here; the splash setting mustn't stop it
    game, content = _make_game(tmp_path, mv_game, "mv_root", encrypted=True)
    _set_system(content, encryptionKey="")
    zp = _export(parser, game, tmp_path, "install", show_splash=True)
    assert "TranslationSplash" not in _enabled_plugins(
        zp, "_translation/js/plugins.js")
    assert not any("TranslationSplash" in n for n in _names(zp))


def test_encrypted_game_without_key_fails_instead_of_half_a_package(
        tmp_path, mv_game, parser):
    # plugins.js already enables the splash, but its image can't be made
    game, content = _make_game(tmp_path, mv_game, "mv_root", encrypted=True)
    _set_system(content, encryptionKey="00112233445566778899aabbccddeeff")
    assert splash_generator.inject_splash(game)
    _set_system(content, encryptionKey="")
    zp = str(tmp_path / "keep.zip")
    with open(zp, "wb") as f:
        f.write(b"previous good zip")
    with pytest.raises(RuntimeError):
        parser.export_patch_zip(game, parser.load_project(game), zp,
                                inject_wordwrap=True)
    with open(zp, "rb") as f:
        assert f.read() == b"previous good zip"      # atomic: untouched
    assert not os.path.exists(zp + ".rmt-tmp")


@pytest.mark.parametrize("layout", ["install", "dropin"])
def test_splash_off_adds_and_enables_nothing(tmp_path, mv_game, parser,
                                             layout):
    game, content = _make_game(tmp_path, mv_game, "mv_root")
    # a game that an earlier export gave the splash
    assert splash_generator.inject_splash(game)
    zp = _export(parser, game, tmp_path, layout, show_splash=False)
    names = _names(zp)
    assert not any("TranslationSplash" in n for n in names)
    assert "TranslationSplash" not in _read(
        zp, _prefixed(layout, "js/plugins.js")).decode("utf-8")


def test_splash_in_games_plugins_js_ships_by_default(tmp_path, mv_game,
                                                     parser):
    """show_splash=None (wizard / old callers): what plugins.js says goes."""
    game, _content = _make_game(tmp_path, mv_game, "mv_root")
    assert splash_generator.inject_splash(game)
    zp = _export(parser, game, tmp_path, "install")
    assert "_translation/js/plugins/TranslationSplash.js" in _names(zp)
    assert "_translation/img/system/TranslationSplash.png" in _names(zp)


@pytest.mark.parametrize("layout", ["install", "dropin"])
def test_game_folder_export_ships_splash_files(tmp_path, mv_game, parser,
                                               layout):
    game, _content = _make_game(tmp_path, mv_game, "mv_www", encrypted=True)
    assert splash_generator.inject_splash(game)
    zp = str(tmp_path / "gf.zip")
    parser.export_game_folder_as_patch(game, zp, layout=layout)
    names = _names(zp)
    for rel in ("www/js/plugins/TranslationSplash.js",
                "www/img/system/TranslationSplash.png",
                "www/img/system/TranslationSplash.rpgmvp"):
        assert _prefixed(layout, rel) in names


def test_game_folder_export_without_splash_adds_none(tmp_path, mv_game,
                                                     parser):
    game, _content = _make_game(tmp_path, mv_game, "mv_root")
    zp = str(tmp_path / "gf.zip")
    parser.export_game_folder_as_patch(game, zp)
    assert not any("TranslationSplash" in n for n in _names(zp))


# ── item 2: drop-in layout ───────────────────────────────────────────

def test_dropin_layout_is_real_paths_without_bat_files(tmp_path, mv_game,
                                                       parser):
    game, _content = _make_game(tmp_path, mv_game, "mv_www")
    zp = _export(parser, game, tmp_path, "dropin", show_splash=True)
    names = _names(zp)
    assert "www/data/Actors.json" in names
    assert "www/js/plugins.js" in names
    assert "README.txt" in names
    assert "install.bat" not in names and "uninstall.bat" not in names
    assert not any(n.startswith("_translation/") for n in names)


def test_install_layout_unchanged(tmp_path, mv_game, parser):
    game, _content = _make_game(tmp_path, mv_game, "mv_www")
    zp = _export(parser, game, tmp_path, "install")
    names = _names(zp)
    assert {"install.bat", "uninstall.bat", "README.txt",
            "_translation/www/data/Actors.json"} <= names


def test_dropin_readme_names_the_real_exe(tmp_path, mv_game, parser):
    game, _content = _make_game(tmp_path, mv_game, "mv_www")
    os.remove(os.path.join(game, "Game.exe"))
    with open(os.path.join(game, "Hanabi no Mori.exe"), "wb") as f:
        f.write(b"MZ")
    with open(os.path.join(game, "unins000.exe"), "wb") as f:
        f.write(b"MZ")
    zp = _export(parser, game, tmp_path, "dropin")
    readme = _read(zp, "README.txt").decode("utf-8")
    assert "Hanabi no Mori.exe" in readme
    assert "Game.exe" not in readme
    assert '"www\\data"' in readme and "replace" in readme.lower()


@pytest.mark.parametrize("exes", [[], ["A.exe", "B.exe"]])
def test_dropin_readme_generic_when_exe_unknown(tmp_path, mv_game, parser,
                                                exes):
    game, _content = _make_game(tmp_path, mv_game, "mv_root")
    os.remove(os.path.join(game, "Game.exe"))
    for n in exes:
        with open(os.path.join(game, n), "wb") as f:
            f.write(b"MZ")
    zp = _export(parser, game, tmp_path, "dropin")
    readme = _read(zp, "README.txt").decode("utf-8")
    assert ".exe" in readme
    for n in exes + ["Game.exe"]:
        assert n not in readme
    assert '"data"' in readme       # root layout: the data folder
    assert '"js\\plugins.js"' in readme    # and the other files it replaces


def test_exports_never_write_into_the_game_folder(tmp_path, mv_game, parser):
    game, _content = _make_game(tmp_path, mv_game, "mv_www")
    assert splash_generator.inject_splash(game)
    before = _tree(game)
    for layout in ("install", "dropin"):
        _export(parser, game, tmp_path, layout, show_splash=True)
        gz = str(tmp_path / f"gf_{layout}.zip")
        parser.export_game_folder_as_patch(game, gz, layout=layout)
    assert _tree(game) == before


def test_zip_is_written_atomically(tmp_path, mv_game, parser, monkeypatch):
    game, _content = _make_game(tmp_path, mv_game, "mv_root")
    zp = str(tmp_path / "keep.zip")
    with open(zp, "wb") as f:
        f.write(b"previous good zip")

    def boom(_game):
        raise RuntimeError("disk full")
    monkeypatch.setattr(splash_generator, "splash_payload", boom)
    with pytest.raises(RuntimeError):
        parser.export_patch_zip(game, parser.load_project(game), zp,
                                layout="dropin", show_splash=True)
    with open(zp, "rb") as f:
        assert f.read() == b"previous good zip"
    assert os.listdir(tmp_path / "game_mv_root")           # game untouched
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".rmt-tmp")]


def test_unknown_layout_is_refused(tmp_path, mv_game, parser):
    game, _content = _make_game(tmp_path, mv_game, "mv_root")
    zp = str(tmp_path / "x.zip")
    with pytest.raises(ValueError):
        parser.export_patch_zip(game, parser.load_project(game), zp,
                                layout="nope")
    assert not os.path.exists(zp)
    assert not os.path.exists(zp + ".rmt-tmp")
