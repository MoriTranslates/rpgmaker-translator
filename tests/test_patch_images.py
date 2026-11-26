"""Patches ship the images the app translated (lesson from game 2: the
redrawn picture-choice buttons had to be added to the patch by hand).

Image export keeps originals in img_original/ and writes the translated
image into img/. A patch carries exactly the img/ files that have such a
backup and differ from it.
"""

import os
import shutil
import subprocess
import zipfile

import pytest
from test_patch_bundling import _export, _make_game, _names, _prefixed

from translator.rpgmaker_mv import RPGMakerMVParser

KINDS = [("mv_root", ""), ("mv_www", "www/"), ("mz", "")]


@pytest.fixture
def parser():
    p = RPGMakerMVParser()
    p.game_font = None
    return p


def _content(game, rel):
    return os.path.join(game, *rel.strip("/").split("/")) if rel else game


def _put(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _translate_images(game, rel):
    """As the image panel's export does: backup in img_original, new in img."""
    base = _content(game, rel)
    _put(os.path.join(base, "img_original", "choices", "C_はい.rpgmvp"), b"JP")
    _put(os.path.join(base, "img", "choices", "C_はい.rpgmvp"), b"EN")
    _put(os.path.join(base, "img_original", "system", "Title.png"), b"JPT")
    _put(os.path.join(base, "img", "system", "Title.png"), b"ENT")
    # backed up but put back unchanged: not shipped
    _put(os.path.join(base, "img_original", "pictures", "same.png"), b"X")
    _put(os.path.join(base, "img", "pictures", "same.png"), b"X")
    # backup whose live file is gone: skipped
    _put(os.path.join(base, "img_original", "pictures", "gone.png"), b"Y")
    # ordinary game art with no backup: never shipped
    _put(os.path.join(base, "img", "pictures", "art.png"), b"ART")


@pytest.mark.parametrize("layout", ["install", "dropin"])
@pytest.mark.parametrize("kind,rel", KINDS)
def test_project_patch_ships_translated_images_only(tmp_path, mv_game, parser,
                                                    layout, kind, rel):
    from translator.game_io import PartialExportError
    game, _ = _make_game(tmp_path, mv_game, kind)
    _translate_images(game, rel)
    if layout == "install":
        # The zip is written; the left-out image is reported, not hidden
        with pytest.raises(PartialExportError) as exc:
            _export(parser, game, tmp_path, layout)
        assert any("C_はい.rpgmvp" in p and "Drop-in" in p
                   for p in exc.value.problems)
        zp = str(tmp_path / f"out_{layout}.zip")
        with zipfile.ZipFile(zp) as zf:
            readme = zf.read("README.txt").decode("utf-8")
        assert "Not included: 1 translated image" in readme
    else:
        zp = _export(parser, game, tmp_path, layout)
    names = _names(zp)
    title = _prefixed(layout, rel + "img/system/Title.png")
    assert title in names
    with zipfile.ZipFile(zp) as zf:
        assert zf.read(title) == b"ENT"
    jp = _prefixed(layout, rel + "img/choices/C_はい.rpgmvp")
    # install.bat can't take a Japanese path: only the drop-in carries it
    assert (jp in names) is (layout == "dropin")
    for other in ("img/pictures/same.png", "img/pictures/gone.png",
                  "img/pictures/art.png"):
        assert _prefixed(layout, rel + other) not in names
    if layout == "dropin":
        with zipfile.ZipFile(zp) as zf:
            readme = zf.read("README.txt").decode("utf-8")
        assert "C_はい.rpgmvp" in readme          # listed for backup


@pytest.mark.parametrize("layout", ["install", "dropin"])
def test_game_folder_patch_ships_translated_images(tmp_path, mv_game, parser,
                                                   layout):
    game, _ = _make_game(tmp_path, mv_game, "mv_www")
    _translate_images(game, "www/")
    zp = str(tmp_path / f"gf_{layout}.zip")
    stats = parser.export_game_folder_as_patch(game, zp, layout=layout)
    names = _names(zp)
    assert _prefixed(layout, "www/img/system/Title.png") in names
    assert (_prefixed(layout, "www/img/choices/C_はい.rpgmvp") in names) is         (layout == "dropin")
    assert stats["images_left_out"] == (
        ["www/img/choices/C_はい.rpgmvp"] if layout == "install" else [])
    assert _prefixed(layout, "www/img/pictures/art.png") not in names


def test_no_backup_folder_means_no_images(tmp_path, mv_game, parser):
    game, _ = _make_game(tmp_path, mv_game, "mz")
    _put(os.path.join(game, "img", "pictures", "art.png"), b"ART")
    zp = _export(parser, game, tmp_path, "dropin")
    assert not any(n.startswith("img/pictures/") for n in _names(zp))


@pytest.mark.skipif(os.name != "nt", reason="needs cmd.exe")
def test_install_and_uninstall_round_trip_images(tmp_path, mv_game, parser):
    game, _ = _make_game(tmp_path, mv_game, "mz")
    base = game
    _put(os.path.join(base, "img_original", "system", "Title.png"), b"JPT")
    _put(os.path.join(base, "img", "system", "Title.png"), b"ENT")
    zp = _export(parser, game, tmp_path, "install")
    player = tmp_path / "player"
    shutil.copytree(game, player)
    # The player has the original Japanese title image
    (player / "img" / "system" / "Title.png").write_bytes(b"JPT")
    shutil.rmtree(player / "img_original")
    with zipfile.ZipFile(zp) as zf:
        zf.extractall(player)

    def run(script):
        r = subprocess.run(["cmd", "/c", str(player / script)], cwd=player,
                           stdin=subprocess.DEVNULL, capture_output=True,
                           timeout=60)
        out = r.stdout.decode("utf-8", "replace")
        assert r.returncode == 0 and "FAILED" not in out, out
    run("install.bat")
    assert (player / "img" / "system" / "Title.png").read_bytes() == b"ENT"
    run("uninstall.bat")
    assert (player / "img" / "system" / "Title.png").read_bytes() == b"JPT"
