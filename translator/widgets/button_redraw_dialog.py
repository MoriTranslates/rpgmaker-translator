"""Redraw Buttons: new English buttons for games with text drawn in images.

1. Read and translate, in phases with one model loaded at a time: the
   image model (Qwen) reads the buttons, the main model (e.g. Sugoi)
   translates them fully with the lines before each choice, and the image
   model shortens only the English too long for its button.
2. The translator checks each row (original button, Japanese, English,
   new button) and edits anything. Nothing is written yet.
3. Continue: make buttons writes the ticked ones, one new button design
   per style, keeping the originals in img_original/.
Labels can also be saved to / loaded from a CSV.
"""

from __future__ import annotations

import os

from PIL.ImageQt import ImageQt
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)

from .. import button_redraw as BR

# Left to right as the work goes: original -> Japanese -> English -> new button
COL_TICK, COL_ORIG, COL_JP, COL_EN, COL_NEW, COL_FILE, COL_NOTE = range(7)
THUMB_W = 200


class LabelWorker(QThread):
    """OCR or translate a list of rows off the GUI thread."""
    row_done = pyqtSignal(int, str)        # row, text
    row_failed = pyqtSignal(int, str)      # row, error
    progress = pyqtSignal(int, int)

    def __init__(self, fn, jobs, parent=None, prepare=None):
        """``fn(payload) -> str``; ``jobs`` = [(row, payload)].

        ``prepare`` runs first in the thread (unloading the other model).
        """
        super().__init__(parent)
        self._prepare = prepare
        self._fn = fn
        self._jobs = jobs
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        if self._prepare:
            self._prepare()
        for i, (row, payload) in enumerate(self._jobs):
            if self._stop:
                break
            try:
                self.row_done.emit(row, self._fn(payload) or "")
            except Exception as e:  # noqa: BLE001 — shown on the row, run goes on
                self.row_failed.emit(row, str(e))
            self.progress.emit(i + 1, len(self._jobs))


class ButtonRedrawDialog(QDialog):
    def __init__(self, img_dir: str, key: str, client, saved: dict | None = None,
                 parent=None, scenes: dict | None = None, player: str = ""):
        """``saved``: {rel: [japanese, english]} from the project.

        ``scenes`` ({file stem: ChoiceScene}, see BR.choice_scenes) and
        ``player`` give the translation the lines before each choice and
        who is choosing.
        """
        super().__init__(parent)
        self._scenes = scenes or {}
        self._player = player
        self.setWindowTitle("Redraw Buttons")
        self.setMinimumSize(1000, 640)
        self._img_dir = img_dir
        self._key = key
        self._client = client
        self._ai = client          # replaced by the image model picked below
        self._phases: list = []    # Read and translate: the phases still to run
        self._stopped = False
        self._has_models = False
        self._saved = dict(saved or {})
        self._buttons: list[BR.ButtonFile] = []
        self._style_of: dict[str, int] = {}     # rel -> style index
        self._styles: list[list[BR.ButtonFile]] = []
        self._blanks: dict[int, BR.Blank] = {}
        self._font = BR.default_font()
        self._worker: LabelWorker | None = None
        self._build_ui()
        self._fill_models()
        self._load_folder()

    # ── UI ──────────────────────────────────────────────────────────
    def _build_ui(self):
        lay = QVBoxLayout(self)
        intro = QLabel(
            "1. Read and translate: the image model reads each button "
            "(or takes the Japanese from its file name) and translates it "
            "as a short label.  2. Check each row: original button, Japanese, "
            "English (edit anything), and the new button it makes. Nothing is "
            "written yet.  3. Continue: make buttons writes the ticked ones "
            "into the game; originals are kept in img_original/.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        top = QHBoxLayout()
        top.addWidget(QLabel("Folder:"))
        self.folder = QComboBox()
        subdirs = sorted(d for d in os.listdir(self._img_dir)
                         if os.path.isdir(os.path.join(self._img_dir, d)))
        self.folder.addItems(subdirs)
        if "choices" in subdirs:
            self.folder.setCurrentText("choices")
        self.folder.currentTextChanged.connect(self._load_folder)
        top.addWidget(self.folder)
        top.addWidget(QLabel("Style:"))
        self.style = QComboBox()
        self.style.currentIndexChanged.connect(self._fill_table)
        top.addWidget(self.style, 1)
        self.font_btn = QPushButton("Font…")
        self.font_btn.clicked.connect(self._pick_font)
        top.addWidget(self.font_btn)
        top.addWidget(QLabel("Image model:"))
        self.model = QComboBox()
        self.model.setToolTip("Reads and translates the buttons. qwen3.5:9b is "
                              "recommended; Sugoi and other text-only models "
                              "can't read images.")
        self.model.currentTextChanged.connect(self._model_changed)
        top.addWidget(self.model)
        lay.addLayout(top)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["", "Original button", "Japanese (OCR)", "English",
             "New button", "File", "Note"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(COL_TICK, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_ORIG, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_NEW, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(COL_FILE, QHeaderView.ResizeMode.Interactive)
        hh.setSectionResizeMode(COL_JP, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(COL_EN, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(COL_NOTE, QHeaderView.ResizeMode.Interactive)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self._preview)
        self.table.itemChanged.connect(self._cell_changed)
        lay.addWidget(self.table, 1)

        prev = QHBoxLayout()
        self.before = QLabel()
        self.after = QLabel()
        for lbl in (self.before, self.after):
            lbl.setMinimumHeight(60)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            prev.addWidget(lbl, 1)
        lay.addLayout(prev)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.hide()
        lay.addWidget(self.bar)

        row = QHBoxLayout()
        self.auto_btn = QPushButton("1. Read and translate")
        self.auto_btn.setToolTip("OCR for buttons still missing Japanese or "
                                 "English, then translate the empty English")
        self.auto_btn.clicked.connect(self._run_all)
        self.ocr_btn = QPushButton("Read text (OCR)")
        self.ocr_btn.clicked.connect(lambda: self._run_one(self._run_ocr))
        self.tr_btn = QPushButton("Translate empty")
        self.tr_btn.clicked.connect(lambda: self._run_one(self._run_translate))
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.clicked.connect(self._stop_worker)
        self.stop_btn.setEnabled(False)
        save_csv = QPushButton("Save CSV…")
        save_csv.clicked.connect(self._save_csv)
        load_csv = QPushButton("Load CSV…")
        load_csv.clicked.connect(self._load_csv)
        self.write_btn = QPushButton("Continue: make buttons")
        self.write_btn.setToolTip("Write the ticked buttons into the game")
        self.write_btn.clicked.connect(self._write)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        for b in (self.auto_btn, self.ocr_btn, self.tr_btn, self.stop_btn,
                  save_csv, load_csv):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(self.write_btn)
        row.addWidget(close)
        lay.addLayout(row)

    # ── Data ────────────────────────────────────────────────────────
    def _load_folder(self, *_):
        sub = self.folder.currentText()
        self._buttons, problems = ([], []) if not sub else BR.scan_folder(
            self._img_dir, sub, self._key)
        groups = BR.group_styles(self._buttons)
        # Button-like styles first; the rest (thumbnails, screens) last
        self._styles = ([g for g in groups if BR.looks_like_buttons(g)]
                        + [g for g in groups if not BR.looks_like_buttons(g)])
        self._style_of = {b.rel: i for i, g in enumerate(self._styles) for b in g}
        self._blanks.clear()
        self.style.blockSignals(True)
        self.style.clear()
        for g in self._styles:
            w, h = g[0].image.size
            first = os.path.basename(g[0].rel)
            tag = "" if BR.looks_like_buttons(g) else "  (not buttons?)"
            self.style.addItem(f"{len(g)} images, {w}×{h} (e.g. {first}){tag}")
        self.style.blockSignals(False)
        msg = f"{len(self._buttons)} images in {len(self._styles)} styles."
        if problems:
            msg += f" {len(problems)} couldn't be read: " + "; ".join(problems[:3])
        self.status.setText(msg)
        self._fill_table()

    def _current_style(self) -> list[BR.ButtonFile]:
        i = self.style.currentIndex()
        return self._styles[i] if 0 <= i < len(self._styles) else []

    def _fill_table(self, *_):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        style = self._current_style()
        is_buttons = bool(style) and BR.looks_like_buttons(style)
        for b in style:
            jp, en = self._saved.get(b.rel, [b.label, ""])
            r = self.table.rowCount()
            self.table.insertRow(r)
            tick = QTableWidgetItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            # Already-replaced files start unticked: they may hold hand work
            tick.setCheckState(Qt.CheckState.Unchecked if b.replaced or not is_buttons
                               else Qt.CheckState.Checked)
            self.table.setItem(r, COL_TICK, tick)
            f = QTableWidgetItem(os.path.basename(b.rel))
            f.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            f.setData(Qt.ItemDataRole.UserRole, b.rel)
            self.table.setItem(r, COL_FILE, f)
            self.table.setItem(r, COL_JP, QTableWidgetItem(jp or b.label))
            self.table.setItem(r, COL_EN, QTableWidgetItem(en))
            note = QTableWidgetItem("already replaced: tick to redraw" if b.replaced else "")
            note.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(r, COL_NOTE, note)
            for col in (COL_ORIG, COL_NEW):
                pic = QTableWidgetItem()
                pic.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, col, pic)
            self.table.item(r, COL_ORIG).setData(
                Qt.ItemDataRole.DecorationRole, self._thumb(b.image))
        self.table.blockSignals(False)
        self._refresh_new()
        self.table.resizeRowsToContents()
        if self.table.rowCount():
            self.table.selectRow(0)

    def _rel(self, row: int) -> str:
        return self.table.item(row, COL_FILE).data(Qt.ItemDataRole.UserRole)

    def _cell_changed(self, item):
        if item.column() in (COL_JP, COL_EN):
            r = item.row()
            self._saved[self._rel(r)] = [self.table.item(r, COL_JP).text().strip(),
                                         self.table.item(r, COL_EN).text().strip()]
            if item.column() == COL_EN:
                self._refresh_new([r])
                if r == self.table.currentRow():
                    self._preview()

    def labels(self) -> dict:
        """{rel: [japanese, english]} for saving in the project."""
        return {k: v for k, v in self._saved.items() if any(v)}

    # ── Blank + preview ─────────────────────────────────────────────
    def _blank(self) -> BR.Blank | None:
        i = self.style.currentIndex()
        if i < 0:
            return None
        if i not in self._blanks:
            self._blanks[i] = BR.build_blank([b.image for b in self._styles[i]])
        return self._blanks[i]

    def _preview(self):
        r = self.table.currentRow()
        if r < 0:
            return
        b = next(x for x in self._buttons if x.rel == self._rel(r))
        self._show(self.before, b.image)
        try:
            blank = self._blank()
        except ValueError as e:
            self.after.setText(f"Can't make a button for this style: {e}")
            return
        im, note = BR.render_button(blank, self.table.item(r, COL_EN).text(), self._font)
        self._show(self.after, im)
        notes = "; ".join(blank.notes)
        self.status.setText((note + ". " if note else "") + notes)

    @staticmethod
    def _thumb(im) -> QPixmap:
        pm = QPixmap.fromImage(ImageQt(im.convert("RGBA")))
        return pm.scaled(THUMB_W, max(1, round(THUMB_W * im.height / im.width)),
                         Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)

    def _refresh_new(self, rows=None):
        """Redraw the New column (all rows, or ``rows``) from the English."""
        if not self.table.rowCount():
            return
        try:
            blank = self._blank()
        except ValueError:
            return                   # the preview pane explains why
        if blank is None:
            return
        self.table.blockSignals(True)
        for r in (range(self.table.rowCount()) if rows is None else rows):
            im, _note = BR.render_button(blank, self.table.item(r, COL_EN).text(),
                                         self._font)
            self.table.item(r, COL_NEW).setData(Qt.ItemDataRole.DecorationRole,
                                                self._thumb(im))
        self.table.blockSignals(False)

    @staticmethod
    def _show(label: QLabel, im):
        pm = QPixmap.fromImage(ImageQt(im.convert("RGBA")))
        if pm.width() > 480:
            pm = pm.scaledToWidth(480, Qt.TransformationMode.SmoothTransformation)
        label.setPixmap(pm)

    def _pick_font(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Font for the English", os.path.dirname(self._font or ""),
            "Fonts (*.ttf *.otf *.ttc)")
        if path:
            self._font = path
            self.font_btn.setText(f"Font: {os.path.basename(path)}")
            self._refresh_new()
            self._preview()

    # ── OCR / translate / shorten ───────────────────────────────────
    def _start(self, fn, jobs, col, model) -> bool:
        """Run ``fn`` over ``jobs`` in a worker; False if there was nothing to do.

        ``model`` is the client this phase uses: any other model is unloaded
        first, so the 12 GB card never holds two at once.
        """
        if not jobs:
            return False
        self._worker = LabelWorker(fn, jobs, self, prepare=lambda: self._only(model))
        # Bound methods, not lambdas: Qt drops them if the dialog is gone
        # before a queued signal arrives (a lambda would touch a deleted table)
        self._phase_col = col
        self._worker.row_done.connect(self._row_done)
        self._worker.row_failed.connect(self._row_failed)
        self._worker.progress.connect(self._progress)
        self._worker.finished.connect(self._worker_done)
        for b in (self.auto_btn, self.ocr_btn, self.tr_btn, self.write_btn,
                  self.folder, self.style, self.model):
            b.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.bar.setValue(0)
        self.bar.show()
        self._worker.start()
        return True

    def _only(self, model):
        """Unload Ollama models before a phase that uses a different model.

        Runs in the worker thread (unloading can take seconds). Only for
        local Ollama, and only when the image model differs from the main
        one; a failed unload is logged by the client and the phase still runs.
        """
        if (self._ai is self._client
                or getattr(self._client, "provider", "") != "Ollama (Local)"):
            return
        if getattr(self, "_loaded", None) is not model:
            self._client.unload_models()
            self._loaded = model

    def _row_done(self, row: int, text: str):
        if self._phase_col is None:
            self._ocr_result(row, text)
        else:
            self._set_cell(row, self._phase_col, text)

    def _row_failed(self, row: int, error: str):
        if row < self.table.rowCount():
            self.table.item(row, COL_NOTE).setText(f"failed: {error}")

    def _progress(self, done: int, total: int):
        self.bar.setMaximum(total)
        self.bar.setValue(done)

    def _set_cell(self, row, col, text):
        if row < self.table.rowCount():
            self.table.item(row, col).setText(text.strip())

    def _worker_done(self):
        for b in (self.auto_btn, self.ocr_btn, self.tr_btn, self.write_btn,
                  self.folder, self.style):
            b.setEnabled(True)
        self.model.setEnabled(self._has_models)
        self.stop_btn.setEnabled(False)
        self.bar.hide()
        self._next_phase()

    def _stop_worker(self):
        self._stopped = True
        self._phases = []
        if self._worker:
            self._worker.stop()

    def _run_all(self):
        """Step 1, in phases: read (image model), translate fully (main
        model), then shorten only the labels too long for their button
        (image model)."""
        self._stopped = False
        self._phases = [self._run_ocr, self._run_translate, self._run_shorten]
        self._next_phase()

    def _run_one(self, phase):
        self._stopped = False
        self._phases = [phase]
        self._next_phase()

    def _next_phase(self):
        while self._phases and not self._stopped:
            if self._phases.pop(0)():
                return                     # a worker is running this phase
        if not (self._worker and self._worker.isRunning()):
            self.status.setText("Done. Check the English, then Continue: make buttons.")

    def _fill_models(self):
        """Image-capable Ollama models; qwen3.5:9b preferred."""
        self.model.blockSignals(True)
        self.model.clear()
        local = getattr(self._client, "provider", "Ollama (Local)") == "Ollama (Local)"
        models = BR.vision_models(getattr(self._client, "base_url",
                                          "http://localhost:11434")) if local else []
        self._has_models = bool(models)
        if models:
            self.model.addItems(models)
            self.model.setCurrentText(BR.pick_image_model(models))
        else:
            self.model.addItem(getattr(self._client, "model", "") or "main model")
            self.model.setEnabled(False)
            if local:
                self.model.setToolTip("No installed model can read images. "
                                      "Install one: ollama pull " + BR.IMAGE_MODEL)
        self.model.blockSignals(False)
        self._model_changed(self.model.currentText())

    def _model_changed(self, name: str):
        """Use ``name`` for OCR and translation here; the main model is untouched."""
        from ..ai_client import AIClient
        if (not name or not self.model.isEnabled()
                or name == getattr(self._client, "model", None)):
            self._ai = self._client
            return
        ai = AIClient(base_url=self._client.base_url, model=name)
        for attr in ("glossary", "target_language"):
            if hasattr(self._client, attr):
                setattr(ai, attr, getattr(self._client, attr))
        self._ai = ai

    def _run_ocr(self):
        """Read rows missing Japanese or English from the image itself.

        English printed on the button (``会話 -talk-``) becomes the English;
        a button with only English is marked as such and unticked.
        """
        from ..image_translator import ImageTranslator
        tr = ImageTranslator(self._ai, self._key)
        jobs = []
        for r in range(self.table.rowCount()):
            if (self.table.item(r, COL_JP).text().strip()
                    and self.table.item(r, COL_EN).text().strip()):
                continue
            rel = self._rel(r)
            bak = os.path.join(BR.backup_root(self._img_dir), rel)
            jobs.append((r, bak if os.path.isfile(bak) else os.path.join(self._img_dir, rel)))

        def read(path):
            regions = tr.ocr_image(path)
            return " ".join(x.text for x in regions)
        return self._start(read, jobs, None, self._ai)

    def _ocr_result(self, row: int, text: str):
        if row >= self.table.rowCount():
            return
        jp, en = BR.split_ocr_text(text)
        jp_item, en_item = self.table.item(row, COL_JP), self.table.item(row, COL_EN)
        if jp and not jp_item.text().strip():
            jp_item.setText(jp)
        if en and not en_item.text().strip():
            en_item.setText(en)
            self.table.item(row, COL_NOTE).setText("English taken from the button")
        if not jp and not jp_item.text().strip():
            self.table.item(row, COL_TICK).setCheckState(Qt.CheckState.Unchecked)
            self.table.item(row, COL_NOTE).setText(
                "already English: left alone" if en else "no text found")

    def _run_translate(self):
        jobs = [(r, (self.table.item(r, COL_JP).text().strip(),
                     os.path.splitext(os.path.basename(self._rel(r)))[0]))
                for r in range(self.table.rowCount())
                if self.table.item(r, COL_JP).text().strip()
                and not self.table.item(r, COL_EN).text().strip()]

        def tr(job):
            jp, stem = job
            # The main model (e.g. Sugoi) translates: it reads the grammar
            # of short choices better than the image model.
            return BR.translate_label(self._client, jp, self._scenes.get(stem),
                                      self._player)
        return self._start(tr, jobs, COL_EN, self._client)

    def _run_shorten(self):
        """Shorten English that doesn't fit its button at normal size."""
        try:
            blank = self._blank()
        except ValueError:
            return False
        if blank is None:
            return False
        jobs = []
        for r in range(self.table.rowCount()):
            jp = self.table.item(r, COL_JP).text().strip()
            en = self.table.item(r, COL_EN).text().strip()
            if jp and en and not BR.fits(blank, en, self._font):
                jobs.append((r, (jp, en)))

        def short(job):
            return BR.shorten_label(self._ai, *job)
        return self._start(short, jobs, COL_EN, self._ai)

    # ── CSV ─────────────────────────────────────────────────────────
    def _save_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save button labels", "button_labels.csv", "CSV (*.csv)")
        if not path:
            return
        rows = [(self._rel(r), self.table.item(r, COL_JP).text(),
                 self.table.item(r, COL_EN).text())
                for r in range(self.table.rowCount())]
        try:
            BR.write_labels_csv(path, rows)
        except OSError as e:
            QMessageBox.warning(self, "Not saved", str(e))
            return
        self.status.setText(f"Saved {len(rows)} labels to {path}")

    def _load_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load button labels", "", "CSV (*.csv)")
        if not path:
            return
        try:
            data = BR.read_labels_csv(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Not loaded", str(e))
            return
        hit = 0
        for r in range(self.table.rowCount()):
            row = data.get(self._rel(r))
            if row:
                self.table.item(r, COL_JP).setText(row[0])
                self.table.item(r, COL_EN).setText(row[1])
                hit += 1
        self.status.setText(f"Loaded {hit} of {len(data)} labels for this style.")

    # ── Write ───────────────────────────────────────────────────────
    def _write(self):
        rows = [r for r in range(self.table.rowCount())
                if self.table.item(r, COL_TICK).checkState() == Qt.CheckState.Checked]
        missing = [r for r in rows if not self.table.item(r, COL_EN).text().strip()]
        if missing:
            QMessageBox.warning(self, "English missing",
                                f"{len(missing)} ticked buttons have no English yet.")
            return
        if not rows:
            return
        if QMessageBox.question(
                self, "Make buttons",
                f"Make {len(rows)} new buttons and write them into the game?\n\n"
                "The originals are kept in img_original/.") != QMessageBox.StandardButton.Yes:
            return
        try:
            blank = self._blank()
        except ValueError as e:
            QMessageBox.warning(self, "Can't make buttons", str(e))
            return
        done, failed = 0, []
        for r in rows:
            rel = self._rel(r)
            im, note = BR.render_button(blank, self.table.item(r, COL_EN).text(), self._font)
            try:
                BR.install_button(self._img_dir, rel, im, self._key)
            except (OSError, ValueError) as e:
                failed.append(f"{os.path.basename(rel)}: {e}")
                self.table.item(r, COL_NOTE).setText(f"not written: {e}")
                continue
            done += 1
            self.table.item(r, COL_NOTE).setText("written" + (f" ({note})" if note else ""))
        msg = f"Wrote {done} of {len(rows)} buttons. Originals are in img_original/."
        if failed:
            msg += f"\n\n{len(failed)} failed:\n" + "\n".join(failed[:8])
        (QMessageBox.warning if failed else QMessageBox.information)(
            self, "Redraw Buttons", msg)

    def done(self, result):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(30000)
        super().done(result)
