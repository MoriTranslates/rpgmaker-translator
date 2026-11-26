"""Redraw Buttons (lessons item 17): labels, styles, new buttons, writing."""

import os
import random

import pytest
from PIL import Image, ImageDraw

from qt_helpers import dispose
from translator import button_redraw as BR
from translator.image_translator import decrypt_rpgmvp, encrypt_to_rpgmvp
from translator.project_model import TranslationProject

KEY = "00112233445566778899aabbccddeeff"
FONT = BR.default_font()


# ── Labels ───────────────────────────────────────────────────────

@pytest.mark.parametrize("name, label", [
    ("C_はい.rpgmvp", "はい"),
    ("C_H02_二人のセックスを見たい。.rpgmvp", "二人のセックスを見たい。"),
    ("Button01朝_02会話.rpgmvp", "会話"),
    ("Button02夕_01残業.png", "残業"),
    ("C_Yes.png", ""),
    ("cursor.rpgmvp", ""),
    ("回想H01.rpgmvp", "回想H01"),
])
def test_label_from_filename(name, label):
    assert BR.label_from_filename(name) == label


@pytest.mark.parametrize("text, jp, en", [
    ("会話\n-talk-", "会話", "Talk"),
    ("所持品 -Item-", "所持品", "Item"),
    ("Yes", "", "Yes"),
    ("はい", "はい", ""),
    ("", "", ""),
])
def test_split_ocr_text_uses_english_printed_on_the_button(text, jp, en):
    assert BR.split_ocr_text(text) == (jp, en)


def test_csv_round_trip_with_awkward_text(tmp_path):
    rows = [("choices/C_はい.rpgmvp", "はい", 'Yes, "really"'),
            ("choices/C_a,b.png", "あ,い", "Line one\nline two"),
            ("choices/C_x.png", "", "")]
    p = str(tmp_path / "labels.csv")
    BR.write_labels_csv(p, rows)
    got = BR.read_labels_csv(p)
    assert got["choices/C_はい.rpgmvp"] == ("はい", 'Yes, "really"')
    assert got["choices/C_a,b.png"] == ("あ,い", "Line one\nline two")
    assert not os.path.exists(p + ".tmp")


def test_csv_without_the_columns_is_refused(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        BR.read_labels_csv(str(p))


def test_clean_button_labels_drops_junk():
    raw = {"choices/a.png": ["はい", "Yes"], "b": "Yes", 5: ["x", "y"],
           "c": ["only one"], "d": [1, 2]}
    assert BR.clean_button_labels(raw) == {"choices/a.png": ["はい", "Yes"]}
    assert BR.clean_button_labels(None) == {}


def test_project_saves_and_loads_button_labels(tmp_path):
    p = TranslationProject(project_path=str(tmp_path))
    p.button_labels = {"choices/C_はい.rpgmvp": ["はい", "Yes"]}
    path = str(tmp_path / "state.json")
    p.save_state(path)
    assert TranslationProject.load_state(path).button_labels == p.button_labels
    # older saves have no field
    TranslationProject(project_path=str(tmp_path)).save_state(path)
    assert TranslationProject.load_state(path).button_labels == {}


# ── Synthetic buttons ────────────────────────────────────────────

BG_LEFT, BG_RIGHT, FRAME = (20, 40, 150), (90, 120, 220), (200, 200, 200)


def _button(text_seed: int, w=300, h=60, left=BG_LEFT, right=BG_RIGHT):
    """Gradient button with a frame and pseudo-text in the middle."""
    im = Image.new("RGBA", (w, h))
    px = im.load()
    for x in range(w):
        c = tuple(round(a + (b - a) * x / (w - 1)) for a, b in zip(left, right))
        for y in range(h):
            px[x, y] = c + (255,)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w - 1, h - 1), outline=FRAME, width=3)
    rnd = random.Random(text_seed)
    n = rnd.randint(2, 12)
    x0 = w // 2 - n * 9
    for i in range(n):            # "glyphs": white with a black outline
        gx = x0 + i * 18
        d.rectangle((gx, 16, gx + 12, 44), fill=(250, 250, 250),
                    outline=(10, 10, 20), width=3)
    return im


def test_group_styles_splits_by_size_and_colour():
    blue = [BR.ButtonFile(f"c/b{i}.png", _button(i)) for i in range(4)]
    red = [BR.ButtonFile(f"c/r{i}.png", _button(i, left=(200, 40, 40),
                                                 right=(240, 120, 90)))
           for i in range(3)]
    small = [BR.ButtonFile("c/s.png", _button(1, w=100, h=40))]
    groups = BR.group_styles(blue + red + small)
    assert [len(g) for g in groups] == [4, 3, 1]
    assert {b.rel for b in groups[1]} == {b.rel for b in red}


def test_blank_has_no_text_and_keeps_the_colours():
    blank = BR.build_blank([_button(i) for i in range(8)])
    bg = blank.background
    px = bg.load()
    w, h = bg.size
    # No text colours anywhere inside the frame
    inner = [px[x, y] for x in range(6, w - 6) for y in range(6, h - 6)]
    assert not any(min(p[:3]) > 230 for p in inner)          # no white fill
    assert not any(max(p[:3]) < 30 for p in inner)           # no black outline
    # Similar colours: left is the dark blue, right the light blue
    assert abs(px[10, h // 2][2] - BG_LEFT[2]) < 30
    assert abs(px[w - 10, h // 2][2] - BG_RIGHT[2]) < 30
    # Style measured from the originals
    assert min(blank.fill) > 220
    assert blank.outline is not None and sum(blank.outline) / 3 < 70   # dark
    assert bg.size == (300, 60)


def test_blank_from_few_buttons_still_works_and_says_so():
    blank = BR.build_blank([_button(3), _button(4)])
    assert any("only 2" in n for n in blank.notes)
    assert blank.background.size == (300, 60)


def test_blank_refuses_mixed_sizes_and_empty():
    with pytest.raises(ValueError):
        BR.build_blank([_button(1), _button(1, w=200)])
    with pytest.raises(ValueError):
        BR.build_blank([])


def test_render_centres_english_and_reports_fit():
    blank = BR.build_blank([_button(i) for i in range(6)])
    im, note = BR.render_button(blank, "Yes", FONT)
    assert im.size == (300, 60) and note == ""
    assert im.tobytes() != blank.background.tobytes()
    _im, note = BR.render_button(blank, "I want you two to love each other "
                                 "so much more than before", FONT)
    assert note                                  # shrunk / wrapped / too long
    im, note = BR.render_button(blank, "   ", FONT)
    assert note == "no English text" and im.tobytes() == blank.background.tobytes()


# ── Writing into the game ────────────────────────────────────────

def _game(tmp_path, encrypted=True):
    img = tmp_path / "www" / "img" / "choices"
    img.mkdir(parents=True)
    names = []
    for i in range(4):
        png = tmp_path / f"src{i}.png"
        _button(i).save(png)
        if encrypted:
            name = f"C_ボタン{i}.rpgmvp"
            encrypt_to_rpgmvp(str(png), str(img / name), KEY)
        else:
            name = f"C_ボタン{i}.png"
            _button(i).save(img / name)
        names.append(name)
    return str(tmp_path / "www" / "img"), names


def _read(path, key=KEY):
    return BR.load_image(path, key)


def test_install_backs_up_then_replaces_encrypted(tmp_path):
    img_dir, names = _game(tmp_path)
    rel = "choices/" + names[0]
    orig = open(os.path.join(img_dir, rel), "rb").read()
    new = Image.new("RGBA", (300, 60), (1, 2, 3, 255))
    BR.install_button(img_dir, rel, new, KEY)
    bak = os.path.join(BR.backup_root(img_dir), rel)
    assert open(bak, "rb").read() == orig
    assert _read(os.path.join(img_dir, rel)).getpixel((5, 5)) == (1, 2, 3, 255)
    # second run: backup still holds the Japanese original
    BR.install_button(img_dir, rel, Image.new("RGBA", (300, 60), (9, 9, 9, 255)), KEY)
    assert open(bak, "rb").read() == orig
    assert not [f for f in os.listdir(os.path.join(img_dir, "choices")) if "tmp" in f]


def test_install_plain_png(tmp_path):
    img_dir, names = _game(tmp_path, encrypted=False)
    rel = "choices/" + names[1]
    BR.install_button(img_dir, rel, Image.new("RGBA", (300, 60), (7, 7, 7, 255)))
    assert _read(os.path.join(img_dir, rel)).getpixel((5, 5)) == (7, 7, 7, 255)
    assert os.path.isfile(os.path.join(BR.backup_root(img_dir), rel))


def test_install_writes_nothing_if_backup_fails(tmp_path, monkeypatch):
    img_dir, names = _game(tmp_path)
    rel = "choices/" + names[0]
    before = open(os.path.join(img_dir, rel), "rb").read()

    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(BR.shutil, "copy2", boom)
    with pytest.raises(OSError):
        BR.install_button(img_dir, rel, Image.new("RGBA", (300, 60)), KEY)
    assert open(os.path.join(img_dir, rel), "rb").read() == before


def test_install_keeps_original_if_write_fails(tmp_path, monkeypatch):
    img_dir, names = _game(tmp_path)
    rel = "choices/" + names[2]
    before = open(os.path.join(img_dir, rel), "rb").read()

    def half_write(_png, out, _key):
        with open(out, "wb") as f:
            f.write(b"RPGMV")           # truncated file, then the disk fills
        raise OSError("disk full")
    monkeypatch.setattr(BR, "encrypt_to_rpgmvp", half_write)
    with pytest.raises(OSError):
        BR.install_button(img_dir, rel, Image.new("RGBA", (300, 60)), KEY)
    assert open(os.path.join(img_dir, rel), "rb").read() == before
    assert not [f for f in os.listdir(os.path.join(img_dir, "choices")) if "tmp" in f]


@pytest.mark.parametrize("rel, key, exc", [
    ("choices/missing.png", "", FileNotFoundError),
])
def test_install_refuses_missing_file(tmp_path, rel, key, exc):
    img_dir, _ = _game(tmp_path)
    with pytest.raises(exc):
        BR.install_button(img_dir, rel, Image.new("RGBA", (300, 60)), key)


def test_install_refuses_no_key_and_odd_formats(tmp_path):
    img_dir, names = _game(tmp_path)
    with pytest.raises(ValueError):
        BR.install_button(img_dir, "choices/" + names[0], Image.new("RGBA", (3, 3)), "")
    jpg = os.path.join(img_dir, "choices", "x.jpg")
    Image.new("RGB", (3, 3)).save(jpg)
    with pytest.raises(ValueError):
        BR.install_button(img_dir, "choices/x.jpg", Image.new("RGBA", (3, 3)))
    assert not os.path.exists(os.path.join(BR.backup_root(img_dir), "choices", "x.jpg"))


def test_scan_reads_originals_from_backup(tmp_path):
    img_dir, names = _game(tmp_path)
    rel = "choices/" + names[0]
    original = _read(os.path.join(img_dir, rel))
    BR.install_button(img_dir, rel, Image.new("RGBA", (300, 60), (1, 1, 1, 255)), KEY)
    buttons, problems = BR.scan_folder(img_dir, "choices", KEY)
    assert not problems and len(buttons) == 4
    b = next(x for x in buttons if x.rel == rel)
    assert b.replaced and b.image.tobytes() == original.tobytes()
    assert b.label == "ボタン0"
    assert not any(x.replaced for x in buttons if x.rel != rel)


def test_scan_reports_unreadable_files(tmp_path):
    img_dir, _ = _game(tmp_path)
    open(os.path.join(img_dir, "choices", "C_bad.rpgmvp"), "wb").write(b"junk")
    buttons, problems = BR.scan_folder(img_dir, "choices", KEY)
    assert len(buttons) == 4 and len(problems) == 1


def test_encrypted_round_trip_is_a_real_png(tmp_path):
    img_dir, names = _game(tmp_path)
    rel = "choices/" + names[3]
    BR.install_button(img_dir, rel, Image.new("RGBA", (300, 60), (5, 6, 7, 255)), KEY)
    raw = decrypt_rpgmvp(os.path.join(img_dir, rel), KEY)
    assert raw[:8] == bytes([0x89]) + b"PNG\r\n" + bytes([0x1A, 0x0A])


# ── Dialog ───────────────────────────────────────────────────────

class _Client:
    def __init__(self):
        self.contexts = []

    def translate(self, text, context="", field=""):
        self.contexts.append(context)
        return {"ボタン0": "Button zero"}.get(text, "Other")


@pytest.fixture(autouse=True)
def _no_ollama(monkeypatch):
    """Dialog tests never contact a real Ollama."""
    monkeypatch.setattr(BR, "vision_models", lambda *a, **k: [])


def test_dialog_translates_and_writes_ticked(qapp, tmp_path, monkeypatch):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QMessageBox

    from translator.widgets.button_redraw_dialog import (COL_EN, COL_JP,
                                                         COL_NOTE, COL_TICK,
                                                         ButtonRedrawDialog)
    img_dir, names = _game(tmp_path)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg = ButtonRedrawDialog(img_dir, KEY, _Client(),
                             saved={"choices/" + names[1]: ["ボタン1", "Saved one"]})
    try:
        t = dlg.table
        assert t.rowCount() == 4
        assert t.item(0, COL_JP).text() == "ボタン0"
        assert t.item(1, COL_EN).text() == "Saved one"
        dlg._run_translate()
        dlg._worker.wait(5000)
        qapp.processEvents()
        assert t.item(0, COL_EN).text() == "Button zero"
        assert t.item(1, COL_EN).text() == "Saved one"        # not overwritten
        t.item(3, COL_TICK).setCheckState(Qt.CheckState.Unchecked)
        dlg._write()
        assert t.item(0, COL_NOTE).text().startswith("written")
        assert t.item(3, COL_NOTE).text() == ""
        bak = BR.backup_root(img_dir)
        assert sorted(os.listdir(os.path.join(bak, "choices"))) == sorted(names[:3])
        assert dlg.labels()["choices/" + names[0]] == ["ボタン0", "Button zero"]
    finally:
        dispose(dlg)


def test_dialog_ocr_fills_english_hint_and_unticks_english_only(qapp, tmp_path,
                                                                monkeypatch):
    from PyQt6.QtCore import Qt

    from translator.image_translator import ImageTranslator, TextRegion
    from translator.widgets.button_redraw_dialog import (COL_EN, COL_FILE,
                                                         COL_JP, COL_NOTE,
                                                         COL_TICK,
                                                         ButtonRedrawDialog)
    img_dir, names = _game(tmp_path)
    for n in names[:2]:                      # file names without Japanese
        os.rename(os.path.join(img_dir, "choices", n),
                  os.path.join(img_dir, "choices", n.replace("ボタン", "B")))
    replies = iter([[TextRegion("会話\n-talk-", (0, 0, 1, 1))],
                    [TextRegion("Yes", (0, 0, 1, 1))]] + [[]] * 5)
    monkeypatch.setattr(ImageTranslator, "ocr_image", lambda self, p: next(replies))
    dlg = ButtonRedrawDialog(img_dir, KEY, _Client())
    try:
        t = dlg.table
        dlg._run_ocr()
        dlg._worker.wait(5000)
        qapp.processEvents()
        rows = {t.item(r, COL_FILE).text(): r for r in range(t.rowCount())}
        r0, r1 = rows["C_B0.rpgmvp"], rows["C_B1.rpgmvp"]
        assert (t.item(r0, COL_JP).text(), t.item(r0, COL_EN).text()) == ("会話", "Talk")
        assert t.item(r1, COL_TICK).checkState() == Qt.CheckState.Unchecked
        assert "already English" in t.item(r1, COL_NOTE).text()
    finally:
        dispose(dlg)


# ── Button prompt, model choice, button detection ────────────────

def test_translate_label_asks_for_a_short_button_label():
    c = _Client()
    assert BR.translate_label(c, "ボタン0") == "Button zero"
    assert "short button label" in c.contexts[0]


@pytest.mark.parametrize("models, pick", [
    (["llava:7b", "qwen3.5:9b", "qwen2.5vl:7b"], "qwen3.5:9b"),
    (["llava:7b", "qwen2.5vl:7b"], "qwen2.5vl:7b"),
    (["llava:7b"], "llava:7b"),
    ([], ""),
])
def test_pick_image_model_prefers_qwen35(models, pick):
    assert BR.pick_image_model(models) == pick


def test_vision_models_reads_capabilities(monkeypatch):
    import requests

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"models": [
                {"name": "sugoi", "capabilities": ["completion"]},
                {"name": "qwen3.5:9b", "capabilities": ["completion", "vision"]},
                {"name": "old"}, "junk"]}
    monkeypatch.undo()          # undo the autouse stub for this test
    monkeypatch.setattr(requests, "get", lambda *a, **k: R())
    assert BR.vision_models() == ["qwen3.5:9b"]

    def down(*a, **k):
        raise requests.ConnectionError("no ollama")
    monkeypatch.setattr(requests, "get", down)
    assert BR.vision_models() == []


@pytest.mark.parametrize("size, n, ok", [
    ((533, 80), 58, True), ((239, 102), 5, True),
    ((280, 210), 3, False),      # scene thumbnails
    ((430, 823), 4, False),      # status screens
    ((533, 80), 1, False),       # a single image isn't a set
])
def test_looks_like_buttons(size, n, ok):
    group = [BR.ButtonFile(f"c/{i}.png", Image.new("RGBA", size)) for i in range(n)]
    assert BR.looks_like_buttons(group) is ok


def test_dialog_read_and_translate_runs_ocr_then_translation(qapp, tmp_path,
                                                             monkeypatch):
    from translator.image_translator import ImageTranslator, TextRegion
    from translator.widgets.button_redraw_dialog import (COL_EN, COL_FILE,
                                                         ButtonRedrawDialog)
    img_dir, names = _game(tmp_path)
    seen = []

    def ocr(self, path):
        seen.append(path)
        return [TextRegion("ボタン", (0, 0, 1, 1))]
    monkeypatch.setattr(ImageTranslator, "ocr_image", ocr)
    dlg = ButtonRedrawDialog(img_dir, KEY, _Client())
    try:
        dlg._run_all()
        for _ in range(50):
            if dlg._worker:
                dlg._worker.wait(5000)
            qapp.processEvents()
            if all(dlg.table.item(r, COL_EN).text() for r in range(dlg.table.rowCount())):
                break
        assert len(seen) == 4
        rows = {dlg.table.item(r, COL_FILE).text(): r for r in range(4)}
        assert dlg.table.item(rows[names[0]], COL_EN).text() == "Button zero"
    finally:
        dispose(dlg)


def test_dialog_only_writes_after_confirm(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from translator.widgets.button_redraw_dialog import COL_EN, ButtonRedrawDialog
    img_dir, _names = _game(tmp_path)
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    dlg = ButtonRedrawDialog(img_dir, KEY, _Client())
    try:
        for r in range(dlg.table.rowCount()):
            dlg.table.item(r, COL_EN).setText("Hi")
        dlg._write()
        assert not os.path.exists(BR.backup_root(img_dir))
    finally:
        dispose(dlg)


# ── Detection (wizard) ───────────────────────────────────────────

def test_image_size_reads_plain_and_encrypted_headers(tmp_path):
    img_dir, names = _game(tmp_path)
    assert BR.image_size(os.path.join(img_dir, "choices", names[0])) == (300, 60)
    png = tmp_path / "p.png"
    Image.new("RGB", (17, 9)).save(png)
    assert BR.image_size(str(png)) == (17, 9)
    junk = tmp_path / "j.rpgmvp"
    junk.write_bytes(b"not an image at all, not even close")
    assert BR.image_size(str(junk)) is None
    assert BR.image_size(str(tmp_path / "missing.png")) is None


def test_detect_buttons_finds_sets_and_skips_the_rest(tmp_path):
    img_dir, names = _game(tmp_path)                      # 4 choice buttons
    pics = os.path.join(img_dir, "pictures")
    os.makedirs(pics)
    for i in range(3):
        Image.new("RGB", (280, 210)).save(os.path.join(pics, f"回想{i}.png"))  # thumbs
        Image.new("RGB", (300, 60)).save(os.path.join(pics, f"Button{i}.png"))  # English
    Image.new("RGB", (300, 60)).save(os.path.join(pics, "ボタン.png"))  # single
    faces = os.path.join(img_dir, "faces")
    os.makedirs(faces)
    for i in range(3):
        Image.new("RGB", (300, 60)).save(os.path.join(faces, f"顔{i}.png"))
    found = BR.detect_buttons(img_dir)
    assert [(d.subdir, d.size, d.count) for d in found] == [("choices", (300, 60), 4)]
    assert "4 button images" in BR.describe_detected(found)
    # once redrawn, a button no longer counts
    for n in names[:3]:
        BR.install_button(img_dir, "choices/" + n,
                          Image.new("RGBA", (300, 60), (1, 1, 1, 255)), KEY)
    assert BR.detect_buttons(img_dir) == []                # only 1 left: no set
    assert BR.detect_buttons(str(tmp_path / "nope")) == []


def test_wizard_shows_detected_buttons(qapp, monkeypatch, tmp_path, mv_game,
                                       mv_parser):
    from test_release_111 import _wizard
    choices = os.path.join(mv_game, "img", "choices")
    os.makedirs(choices, exist_ok=True)
    for i in range(3):
        Image.new("RGB", (300, 60)).save(os.path.join(choices, f"C_選択{i}.png"))
    win, wiz, _shown = _wizard(qapp, monkeypatch, tmp_path, mv_game, mv_parser)
    try:
        assert [d.count for d in wiz.buttons_found] == [3]
        texts = " ".join(lbl.text() for lbl in wiz.findChildren(type(wiz.detail_label)))
        assert "Buttons detected" in texts
        wiz._finish()
        assert "Redraw Buttons" in wiz.detail_label.text()
    finally:
        dispose(wiz)
        from qt_helpers import dispose_main_window
        dispose_main_window(win)


def test_wizard_without_buttons_says_nothing(qapp, monkeypatch, tmp_path,
                                             mv_game, mv_parser):
    from test_release_111 import _wizard
    win, wiz, _shown = _wizard(qapp, monkeypatch, tmp_path, mv_game, mv_parser)
    try:
        assert wiz.buttons_found == []
        texts = " ".join(lbl.text() for lbl in wiz.findChildren(type(wiz.detail_label)))
        assert "Buttons detected" not in texts
    finally:
        dispose(wiz)
        from qt_helpers import dispose_main_window
        dispose_main_window(win)


# ── Choice context from the script ───────────────────────────────

def _te(eid, field, original, context=""):
    from translator.project_model import TranslationEntry
    return TranslationEntry(eid, "CommonEvents.json", field, original, "", "skipped", context)


def test_choice_scenes_give_lines_before_and_all_options():
    es = [_te("CE/1/dialog_1", "dialog", "送ろうか？"),
          _te("CE/1/choice_1_0", "choice", "C_送っておく|350|200", "忘れない間に送ろうか？"),
          _te("CE/1/choice_1_1", "choice", "C_後でいいだろう|350|300", "忘れない間に送ろうか？"),
          _te("CE/2/choice_1_0", "choice", "C_送っておく|350|200", ""),   # later use, no context
          _te("CE/3/choice_1_0", "choice", "普通の選択肢"),                 # ordinary text choice
          _te("CE/4/choice_1_0", "choice", "img/x|1|2")]                   # path: ignored
    sc = BR.choice_scenes(es)
    assert sc["C_送っておく"].before == "忘れない間に送ろうか？"
    assert sc["C_送っておく"].options == ["送っておく", "後でいいだろう"]
    assert "img/x" not in sc
    assert BR.choice_scenes([]) == {}


def test_choice_scene_context_is_capped():
    es = [_te("CE/1/choice_1_0", "choice", "C_a|1|1", "長" * 5000)]
    assert len(BR.choice_scenes(es)["C_a"].before) == BR._SCENE_MAX


def test_translate_label_sends_scene_and_player():
    c = _Client()
    scene = BR.ChoiceScene("忘れない間に送ろうか？", ["送っておく", "後でいいだろう"])
    BR.translate_label(c, "送っておく", scene, "Mamoru")
    ctx = c.contexts[-1]
    assert "short button label" in ctx and "Mamoru" in ctx
    assert "送っておく / 後でいいだろう" in ctx and "忘れない間に送ろうか？" in ctx
    BR.translate_label(c, "はい")                      # no scene: hint only
    assert "lines just before" not in c.contexts[-1]


def test_dialog_sends_the_scene_for_each_button(qapp, tmp_path):
    from translator.widgets.button_redraw_dialog import COL_EN, ButtonRedrawDialog
    img_dir, names = _game(tmp_path)
    stem = os.path.splitext(names[0])[0]
    c = _Client()
    dlg = ButtonRedrawDialog(img_dir, KEY, c, player="Mamoru",
                             scenes={stem: BR.ChoiceScene("前の台詞", ["ボタン0", "ボタン1"])})
    try:
        dlg._run_translate()
        dlg._worker.wait(5000)
        qapp.processEvents()
        assert any("前の台詞" in x and "Mamoru" in x for x in c.contexts)
        assert all(dlg.table.item(r, COL_EN).text() for r in range(4))
    finally:
        dispose(dlg)
