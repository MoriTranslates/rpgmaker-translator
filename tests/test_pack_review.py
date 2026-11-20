"""Review findings on the patch zips (install.bat, drop-in README, plugins.js).

1. install.bat copied only data/, plugins.js, word wrap and gamefont.css, then
   deleted _translation/: the splash files in the zip never reached the game.
2. "Create Patch from Game Folder" shipped a plugins.js enabling
   TranslatorWordWrap without its .js.
3. A UTF-8 BOM in System.json hid hasEncryptedImages / encryptionKey.
4. A YEP_MessageCore "Word Wrap Space" the user turned on was not packaged.
5. An unreadable plugins.js was silently left out of the zip.
6. The drop-in README told players to back up only data.
"""

import json
import os
import re
import shutil
import subprocess
import zipfile

import pytest
from test_patch_bundling import (KEY, _enabled_plugins, _export, _make_game,
                                 _names, _prefixed, _read, _set_system)

from translator import image_translator, splash_generator
from translator.rpgmaker_mv import RPGMakerMVParser

KINDS = [("mv_root", ""), ("mv_www", "www/"), ("mz", "")]


@pytest.fixture
def parser():
    return RPGMakerMVParser()


def _files(root):
    out = set()
    for d, _dirs, fs in os.walk(root):
        for fn in fs:
            out.add(os.path.relpath(os.path.join(d, fn), root)
                    .replace("\\", "/"))
    return out


def _copy_cmds(bat):
    """(source, dest) of every single-file `copy /Y "src" "dest"` in a bat."""
    return re.findall(r'^\s*copy /Y "([^"]+)" "([^"]+)"', bat, re.MULTILINE)


def _installed_by_bat(zp, rel_data):
    """The files install.bat copies out of _translation (everything but the
    data folder, which xcopy handles as a whole)."""
    bat = _read(zp, "install.bat").decode("utf-8")
    out = {}
    for src, dest in _copy_cmds(bat):
        if src.startswith("_translation\\"):
            out[src[len("_translation\\"):]] = dest
    return out


# ── 1. install.bat copies every file the package carries ─────────────

@pytest.mark.parametrize("encrypted", [False, True])
@pytest.mark.parametrize("splash", [True, False])
@pytest.mark.parametrize("wordwrap", [True, False])
@pytest.mark.parametrize("kind,rel", KINDS)
def test_install_bat_copies_every_zip_file(tmp_path, mv_game, parser, kind,
                                           rel, splash, wordwrap, encrypted):
    parser.game_font = "Courier New"
    game, _content = _make_game(tmp_path, mv_game, kind, encrypted=encrypted)
    zp = _export(parser, game, tmp_path, "install", show_splash=splash,
                 inject_wordwrap=wordwrap)
    data_prefix = rel + "data/"
    in_zip = {n[len("_translation/"):] for n in _names(zp)
              if n.startswith("_translation/")
              and not n[len("_translation/"):].startswith(data_prefix)}
    copied = {k.replace("\\", "/"): v.replace("\\", "/")
              for k, v in _installed_by_bat(zp, rel).items()}
    assert set(copied) == in_zip, (sorted(in_zip ^ set(copied)))
    assert all(k == v for k, v in copied.items())   # same place in the game
    if splash:
        assert rel + "js/plugins/TranslationSplash.js" in in_zip
    if wordwrap:
        assert rel + "js/plugins/TranslatorWordWrap.js" in in_zip
    # the data folder is still copied as a whole
    bat = _read(zp, "install.bat").decode("utf-8")
    assert f'xcopy "_translation\\{rel.replace("/", chr(92))}data"' in bat


@pytest.mark.parametrize("kind,rel", KINDS)
def test_uninstall_bat_removes_exactly_what_install_added(tmp_path, mv_game,
                                                          parser, kind, rel):
    game, _content = _make_game(tmp_path, mv_game, kind)
    zp = _export(parser, game, tmp_path, "install", show_splash=True,
                 inject_wordwrap=True)
    inst = _read(zp, "install.bat").decode("utf-8")
    un = _read(zp, "uninstall.bat").decode("utf-8")
    extras = [k for k in _installed_by_bat(zp, rel)
              if not k.endswith(("plugins.js", "gamefont.css"))]
    assert any("TranslationSplash.js" in k for k in extras)
    deleted = set(re.findall(r'del /F /Q "([^"]+)"', un))
    for k in extras:
        assert k in deleted                          # removed again...
        assert f'findstr /X /L /C:"{k}" "rmt_added_files.txt"' in un
        # ...but only when install.bat recorded that it added the file
        assert f'>>"rmt_added_files.txt" echo {k}' in inst
        assert f'if exist "{k}.rmt_bak"' in un       # or put the old one back
    # nothing else outside the data folder is deleted
    allowed = {k for k in extras} | {f"{k}.rmt_bak" for k in extras} \
        | {"rmt_added_files.txt"}
    stray = {d for d in deleted if not d.startswith(("data", "www\\data"))
             and d not in allowed and not d.endswith(("plugins.js",
                                                      "gamefont.css"))}
    assert not stray


def test_bat_refuses_unsafe_package_paths():
    with pytest.raises(ValueError):
        RPGMakerMVParser._build_install_bat(
            "data", "js", 1, False, "T", extra_files=["js/a&calc.js"])
    with pytest.raises(ValueError):
        RPGMakerMVParser._build_uninstall_bat(
            "data", "js", False, "T", extra_files=["../x.js"])


@pytest.mark.skipif(os.name != "nt", reason="needs cmd.exe")
@pytest.mark.parametrize("kind,rel", [("mv_www", "www/"), ("mz", "")])
def test_real_install_then_uninstall_leaves_the_game_as_it_was(
        tmp_path, mv_game, parser, kind, rel):
    game, _content = _make_game(tmp_path, mv_game, kind)
    zp = _export(parser, game, tmp_path, "install", show_splash=True,
                 inject_wordwrap=True)
    player = tmp_path / "player"
    shutil.copytree(game, player)
    # a file the zip carries that the player already has: must come back
    pre = player / (rel + "js/plugins/TranslatorWordWrap.js")
    pre.write_text("// the player's own", encoding="utf-8")
    before = {f: (player / f).read_bytes() for f in _files(player)}
    with zipfile.ZipFile(zp) as zf:
        zf.extractall(player)

    def run(script):
        r = subprocess.run(["cmd", "/c", str(player / script)], cwd=player,
                           stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=60)
        out = r.stdout.decode("utf-8", "replace")
        assert r.returncode == 0 and "FAILED" not in out, out
    run("install.bat")
    for name in ("js/plugins/TranslationSplash.js",
                 "img/system/TranslationSplash.png"):
        assert (player / (rel + name)).is_file(), name
    assert (player / (rel + "js/plugins/TranslatorWordWrap.js")
            ).read_text(encoding="utf-8") != "// the player's own"
    assert not (player / "_translation").exists()
    run("uninstall.bat")
    after = {f: (player / f).read_bytes() for f in _files(player)
             if f not in ("install.bat", "uninstall.bat", "README.txt")}
    assert after == before


# ── 2. every enabled plugin's .js ships or is in the original game ───

def _clean_files(content):
    return {f for f in _files(content) if f.startswith("js/plugins/")}


def _assert_plugins_present(zp, layout, rel, clean):
    """Every status-true plugin in the packaged plugins.js has its .js in
    the zip or among the original game's files."""
    names = _names(zp)
    arc = _prefixed(layout, rel + "js/plugins.js")
    for name in _enabled_plugins(zp, arc):
        js = f"js/plugins/{name}.js"
        in_zip = _prefixed(layout, rel + js) in names
        assert in_zip or js in clean, f"{name}.js: not in zip or original"


@pytest.mark.parametrize("layout", ["install", "dropin"])
@pytest.mark.parametrize("kind,rel", KINDS)
def test_game_folder_patch_ships_wordwrap_plugin(tmp_path, mv_game, parser,
                                                 layout, kind, rel):
    game, content = _make_game(tmp_path, mv_game, kind)
    clean = _clean_files(content)             # the original game, before us
    assert "js/plugins/TranslatorWordWrap.js" not in clean
    assert parser.inject_wordwrap_plugin(game, max_chars=40)
    assert splash_generator.inject_splash(game)
    zp = str(tmp_path / f"gf_{layout}.zip")
    parser.export_game_folder_as_patch(game, zp, layout=layout)
    enabled = _enabled_plugins(zp, _prefixed(layout, rel + "js/plugins.js"))
    assert "TranslatorWordWrap" in enabled and "TranslationSplash" in enabled
    _assert_plugins_present(zp, layout, rel, clean)
    assert _prefixed(layout, rel + "js/plugins/TranslatorWordWrap.js") \
        in _names(zp)


@pytest.mark.parametrize("splash", [True, False, None])
@pytest.mark.parametrize("wordwrap", [True, False])
@pytest.mark.parametrize("layout", ["install", "dropin"])
@pytest.mark.parametrize("kind,rel", KINDS)
def test_every_zip_type_ships_enabled_plugins(tmp_path, mv_game, parser,
                                              layout, kind, rel, splash,
                                              wordwrap):
    game, content = _make_game(tmp_path, mv_game, kind)
    clean = _clean_files(content)
    parser.inject_wordwrap_plugin(game, max_chars=40)
    splash_generator.inject_splash(game)
    zp = _export(parser, game, tmp_path, layout, show_splash=splash,
                 inject_wordwrap=wordwrap)
    _assert_plugins_present(zp, layout, rel, clean)


# ── 3. BOM in System.json ────────────────────────────────────────────

def _add_bom(path):
    with open(path, "rb") as f:
        raw = f.read()
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf" + raw)


@pytest.mark.parametrize("kind,ext", [("mv_www", ".rpgmvp"), ("mz", ".png_")])
def test_bom_system_json_still_gets_encrypted_splash(tmp_path, mv_game, kind,
                                                     ext):
    game, content = _make_game(tmp_path, mv_game, kind, encrypted=True)
    _add_bom(os.path.join(content, "data", "System.json"))
    assert splash_generator._has_encrypted_images(content) is True
    assert image_translator.read_encryption_key(game) == KEY
    rels = [r for r, _d in splash_generator.splash_payload(game)]
    assert any(r.endswith("TranslationSplash" + ext) for r in rels), rels


def test_bom_system_json_export_zip_has_encrypted_splash(tmp_path, mv_game,
                                                         parser):
    game, content = _make_game(tmp_path, mv_game, "mv_www", encrypted=True)
    _add_bom(os.path.join(content, "data", "System.json"))
    zp = _export(parser, game, tmp_path, "dropin", show_splash=True)
    assert "www/img/system/TranslationSplash.rpgmvp" in _names(zp)


# ── 4. YEP_MessageCore Word Wrap Space matches the live game ─────────

def _yep(value):
    return {"name": "YEP_MessageCore", "status": True, "description": "",
            "parameters": {"Word Wrap Space": value}}


def _set_wrap_space(game, content, live, original):
    js = os.path.join(content, "js")
    base = RPGMakerMVParser._load_plugins_js(os.path.join(js, "plugins.js"))
    for name, val in (("plugins.js", live), ("plugins_original.js", original)):
        RPGMakerMVParser._write_plugins_js(os.path.join(js, name),
                                           base + [_yep(val)])
    with open(os.path.join(js, "plugins", "YEP_MessageCore.js"), "w") as f:
        f.write("// yep")


def _packaged_wrap_space(zp, layout, rel):
    text = _read(zp, _prefixed(layout, rel + "js/plugins.js")).decode("utf-8")
    arr = json.loads(re.search(r"var\s+\$plugins\s*=\s*(\[.*\])\s*;", text,
                               re.DOTALL).group(1))
    return [p for p in arr if p["name"] == "YEP_MessageCore"][0][
        "parameters"]["Word Wrap Space"]


@pytest.mark.parametrize("live,original", [("true", "false"),
                                           ("false", "true")])
@pytest.mark.parametrize("layout", ["install", "dropin"])
def test_patch_zip_carries_live_word_wrap_space(tmp_path, mv_game, parser,
                                                layout, live, original):
    game, content = _make_game(tmp_path, mv_game, "mv_root")
    _set_wrap_space(game, content, live, original)
    zp = _export(parser, game, tmp_path, layout, inject_wordwrap=True)
    assert _packaged_wrap_space(zp, layout, "") == live


def test_unreadable_live_plugins_keeps_the_backup_value(tmp_path, mv_game,
                                                        parser):
    game, content = _make_game(tmp_path, mv_game, "mv_root")
    _set_wrap_space(game, content, "true", "false")
    with open(os.path.join(content, "js", "plugins.js"), "w") as f:
        f.write("this is not a plugin list {{{")
    zp = _export(parser, game, tmp_path, "dropin", inject_wordwrap=True)
    assert _packaged_wrap_space(zp, "dropin", "") == "false"


# ── 5. unreadable plugins.js is an error, never a silent omission ────

@pytest.mark.parametrize("bad", ["var $plugins = [ {broken ;", "garbage",
                                 ""])
@pytest.mark.parametrize("layout", ["install", "dropin"])
def test_unparseable_plugins_js_fails_the_export(tmp_path, mv_game, parser,
                                                 layout, bad):
    game, content = _make_game(tmp_path, mv_game, "mv_root")
    os.remove(os.path.join(content, "js", "plugins_original.js")) \
        if os.path.exists(os.path.join(content, "js",
                                       "plugins_original.js")) else None
    with open(os.path.join(content, "js", "plugins.js"), "w") as f:
        f.write(bad)
    zp = str(tmp_path / "bad.zip")
    entries = parser.load_project(game)
    with pytest.raises(RuntimeError, match="plugin list"):
        parser.export_patch_zip(game, entries, zp, layout=layout,
                                inject_wordwrap=True)
    assert not os.path.exists(zp)
    assert not [n for n in os.listdir(tmp_path) if n.endswith(".rmt-tmp")]


def test_unparseable_backup_plugins_js_fails_the_export(tmp_path, mv_game,
                                                        parser):
    game, content = _make_game(tmp_path, mv_game, "mv_root")
    with open(os.path.join(content, "js", "plugins_original.js"), "w") as f:
        f.write("var $plugins = [ {broken ;")
    zp = str(tmp_path / "bad.zip")
    with pytest.raises(RuntimeError, match="plugin list"):
        parser.export_patch_zip(game, parser.load_project(game), zp,
                                inject_wordwrap=True)
    assert not os.path.exists(zp)


def test_game_folder_patch_fails_on_unparseable_plugins_js(tmp_path, mv_game,
                                                           parser):
    game, content = _make_game(tmp_path, mv_game, "mv_root")
    with open(os.path.join(content, "js", "plugins.js"), "w") as f:
        f.write("var $plugins = [ {broken ;")
    zp = str(tmp_path / "bad.zip")
    with pytest.raises(RuntimeError, match="plugin list"):
        parser.export_game_folder_as_patch(game, zp)
    assert not os.path.exists(zp)


# ── 6. drop-in README lists exactly what the zip overwrites ──────────

def _readme_backup_items(readme):
    block = readme.split("HOW TO INSTALL:")[1].split("2. Extract")[0]
    return [m.replace("\\", "/") for m in re.findall(r'^\s+- "([^"]+)"',
                                                     block, re.MULTILINE)]


@pytest.mark.parametrize("splash", [True, False])
@pytest.mark.parametrize("kind,rel", KINDS)
def test_dropin_readme_lists_what_the_zip_overwrites(tmp_path, mv_game, parser,
                                                     kind, rel, splash):
    parser.game_font = "Courier New"
    game, _content = _make_game(tmp_path, mv_game, kind)
    zp = _export(parser, game, tmp_path, "dropin", show_splash=splash,
                 inject_wordwrap=True)
    items = _readme_backup_items(_read(zp, "README.txt").decode("utf-8"))
    names = _names(zp) - {"README.txt"}
    data_dir = rel + "data"
    # every zip file is covered by a listed folder or file...
    for n in names:
        assert any(n == i or n.startswith(i + "/") for i in items), n
    # ...and nothing is listed that the zip does not touch
    for i in items:
        assert any(n == i or n.startswith(i + "/") for n in names), i
    assert data_dir in items
    assert rel + "js/plugins.js" in items
    assert rel + "fonts/gamefont.css" in items
    if splash:
        assert rel + "img/system/TranslationSplash.png" in items


def test_game_folder_dropin_readme_lists_plugins_and_data(tmp_path, mv_game,
                                                          parser):
    game, content = _make_game(tmp_path, mv_game, "mv_www")
    parser.inject_wordwrap_plugin(game)
    zp = str(tmp_path / "gf.zip")
    parser.export_game_folder_as_patch(game, zp, layout="dropin")
    items = _readme_backup_items(_read(zp, "README.txt").decode("utf-8"))
    for n in _names(zp) - {"README.txt"}:
        assert any(n == i or n.startswith(i + "/") for i in items), n
