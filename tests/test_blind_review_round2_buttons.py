"""Regressions for the second blind review: the Redraw Buttons tool."""

import os
import time

import pytest
from PIL import Image

from qt_helpers import dispose, dispose_main_window
from test_button_redraw import KEY, _button, _ChatClient, _Client, _game
from translator import button_redraw as BR


@pytest.fixture(autouse=True)
def _no_ollama(monkeypatch):
    monkeypatch.setattr(BR, "vision_models", lambda *a, **k: [])


class _SlowClient(_Client):
    model = "sugoi"
    provider = "Ollama (Local)"
    base_url = "http://localhost:11434"

    def __init__(self):
        super().__init__()
        self.calls_after_close = 0
        self.closed = False
        self.unloads = 0

    def translate(self, text, context="", field=""):
        if self.closed:
            self.calls_after_close += 1
        time.sleep(0.05)
        return "Translated " + text

    def unload_models(self):
        self.unloads += 1
        return 1


# High: closing mid-run must stop every later phase
def test_closing_mid_run_stops_every_phase(qapp, tmp_path, monkeypatch):
    from translator.image_translator import ImageTranslator, TextRegion
    from translator.widgets.button_redraw_dialog import ButtonRedrawDialog
    img_dir, _ = _game(tmp_path)

    def slow_ocr(self, p):
        time.sleep(0.1)
        return [TextRegion("ボタン", (0, 0, 1, 1))]
    monkeypatch.setattr(ImageTranslator, "ocr_image", slow_ocr)
    main = _SlowClient()
    dlg = ButtonRedrawDialog(img_dir, KEY, main)
    try:
        dlg._ai = _SlowClient()                 # a separate image model
        dlg._run_all()
        assert dlg._worker.isRunning()
        dlg.done(0)
        main.closed = True
        unloads = main.unloads
        for _ in range(30):
            qapp.processEvents()
            time.sleep(0.02)
        assert main.calls_after_close == 0
        assert main.unloads == unloads
        assert not (dlg._worker and dlg._worker.isRunning())
    finally:
        dispose(dlg)


def _two_style_game(tmp_path):
    img_dir, names = _game(tmp_path)
    d = os.path.join(img_dir, "choices")
    for i in range(3):
        _button(i, w=200, h=50).save(os.path.join(d, f"C_小{i}.png"))
    return img_dir, names


def test_ticks_survive_style_switch(qapp, tmp_path):
    from PyQt6.QtCore import Qt

    from translator.widgets.button_redraw_dialog import COL_TICK, ButtonRedrawDialog
    img_dir, _ = _two_style_game(tmp_path)
    dlg = ButtonRedrawDialog(img_dir, KEY, _Client())
    try:
        assert dlg.style.count() == 2
        dlg.table.item(0, COL_TICK).setCheckState(Qt.CheckState.Unchecked)
        dlg.style.setCurrentIndex(1)
        dlg.style.setCurrentIndex(0)
        assert dlg.table.item(0, COL_TICK).checkState() == Qt.CheckState.Unchecked
    finally:
        dispose(dlg)


def test_table_is_locked_while_a_run_is_going(qapp, tmp_path):
    from PyQt6.QtWidgets import QAbstractItemView

    from translator.widgets.button_redraw_dialog import ButtonRedrawDialog
    img_dir, _ = _game(tmp_path)
    dlg = ButtonRedrawDialog(img_dir, KEY, _SlowClient())
    try:
        before = dlg.table.editTriggers()
        dlg._run_one(dlg._run_translate)
        assert dlg.table.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        dlg._worker.wait(5000)
        qapp.processEvents()
        assert dlg.table.editTriggers() == before
    finally:
        dispose(dlg)


def test_dialog_opens_on_the_detected_folder(qapp, tmp_path):
    from translator.widgets.button_redraw_dialog import ButtonRedrawDialog
    img = tmp_path / "img"
    (img / "animations").mkdir(parents=True)
    (img / "pictures").mkdir()
    for i in range(3):
        _button(i).save(img / "pictures" / f"選択{i}.png")
    dlg = ButtonRedrawDialog(str(img), "", _Client())
    try:
        assert dlg.folder.currentText() == "pictures"
    finally:
        dispose(dlg)
    (img / "pictures" / "選択0.png").unlink()
    (img / "pictures" / "選択1.png").unlink()
    dlg = ButtonRedrawDialog(str(img), "", _Client())
    try:
        assert dlg.folder.currentText() == "" and dlg.table.rowCount() == 0
    finally:
        dispose(dlg)


def test_confirm_mentions_files_without_backup_and_overflow_warns(qapp, tmp_path,
                                                                  monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from translator.widgets.button_redraw_dialog import COL_EN, ButtonRedrawDialog
    img_dir, _ = _game(tmp_path)
    asked, warned = [], []

    def question(_p, _t, text, *a, **k):
        asked.append(text)
        return QMessageBox.StandardButton.Yes
    monkeypatch.setattr(QMessageBox, "question", question)
    monkeypatch.setattr(QMessageBox, "warning",
                        lambda _p, _t, text, *a, **k: warned.append(text))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dlg = ButtonRedrawDialog(img_dir, KEY, _Client())
    try:
        for r in range(4):
            dlg.table.item(r, COL_EN).setText("word " * 60 if r == 0 else "Fine")
        dlg._write()
        assert "4 of them have no backup yet" in asked[0]
        assert warned and "past the button edges" in warned[0]
    finally:
        dispose(dlg)


def test_csv_blank_values_keep_existing_text(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    from translator.widgets.button_redraw_dialog import COL_EN, ButtonRedrawDialog
    img_dir, names = _game(tmp_path)
    csv_path = str(tmp_path / "l.csv")
    BR.write_labels_csv(csv_path, [("choices/" + n, "", "") for n in names])
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (csv_path, ""))
    dlg = ButtonRedrawDialog(img_dir, KEY, _Client())
    try:
        dlg.table.item(0, COL_EN).setText("Keep me")
        dlg._load_csv()
        assert dlg.table.item(0, COL_EN).text() == "Keep me"
    finally:
        dispose(dlg)


def test_shorten_label_reply_of_only_quotes():
    assert BR.shorten_label(_ChatClient('""'), "x", "Long English text.") == "Long English text."


def test_oversized_image_is_reported_not_fatal(tmp_path, monkeypatch):
    img_dir, _ = _game(tmp_path, encrypted=False)
    Image.new("RGB", (400, 400)).save(os.path.join(img_dir, "choices", "big.png"))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 50000)
    _buttons, problems = BR.scan_folder(img_dir, "choices")
    assert len(problems) == 1 and "big.png" in problems[0]


def test_16bit_grey_loads_as_grey(tmp_path):
    p = str(tmp_path / "g.png")
    Image.new("I;16", (4, 4), 40000).save(p)
    r, g, b, a = BR.load_image(p).getpixel((0, 0))
    assert 150 <= r <= 160 and r == g == b and a == 255


@pytest.mark.parametrize("name, label", [
    ("C_H02_二人の_セックスを見たい。.png", "二人の_セックスを見たい。"),
    ("Button01朝_02会話.rpgmvp", "会話"),
])
def test_label_keeps_japanese_parts_joined_by_underscore(name, label):
    assert BR.label_from_filename(name) == label


def test_redraw_refused_while_engine_runs_and_busy_restored(qapp, monkeypatch, tmp_path,
                                                           mv_game, mv_parser):
    from test_release_111 import _wizard

    from translator.widgets import main_window as mw_mod
    win, wiz, _shown = _wizard(qapp, monkeypatch, tmp_path, mv_game, mv_parser)
    try:
        os.makedirs(os.path.join(mv_game, "img", "choices"), exist_ok=True)
        opened = []

        class Fake:
            def __init__(self, *a, **k):
                opened.append(win._busy)

            def exec(self):
                return 0

            def labels(self):
                return {}
        monkeypatch.setattr(mw_mod, "ButtonRedrawDialog", Fake)
        monkeypatch.setattr(type(win.engine), "is_running", property(lambda s: True))
        win._redraw_buttons()
        assert opened == []
        monkeypatch.setattr(type(win.engine), "is_running", property(lambda s: False))
        win._busy = False
        win._redraw_buttons()
        assert opened == [False] and win._busy is False
    finally:
        wiz._cancelled = True
        dispose(wiz)
        dispose_main_window(win)


def test_wizard_summary_rechecks_buttons(qapp, monkeypatch, tmp_path, mv_game, mv_parser):
    from test_release_111 import _wizard
    choices = os.path.join(mv_game, "img", "choices")
    os.makedirs(choices, exist_ok=True)
    for i in range(3):
        Image.new("RGB", (300, 60)).save(os.path.join(choices, f"C_選択{i}.png"))
    win, wiz, _shown = _wizard(qapp, monkeypatch, tmp_path, mv_game, mv_parser)
    try:
        assert wiz.buttons_found
        img_dir = os.path.join(mv_game, "img")
        for i in range(3):
            BR.install_button(img_dir, f"choices/C_選択{i}.png",
                              Image.new("RGBA", (300, 60), (1, 2, 3, 255)))
        wiz._finish()
        assert "Next:" not in wiz.detail_label.text()
    finally:
        wiz._cancelled = True
        dispose(wiz)
        dispose_main_window(win)
