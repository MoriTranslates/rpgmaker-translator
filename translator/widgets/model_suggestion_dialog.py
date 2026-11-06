"""Model suggestion dialog: GPU-aware list from models.json (built in, with a
validated refresh from GitHub), installed badges, and pull-any-tag."""

import html
import subprocess
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPushButton, QLineEdit, QHeaderView,
    QApplication, QGroupBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from . import theme
from .. import model_catalog
from ..model_catalog import is_valid_tag

log = logging.getLogger(__name__)

# Fallback recommended default (also used by Settings to order its model
# combo).  The suggestion list itself comes from models.json.
RECOMMENDED_MODEL = "qwen3.5:9b"

# VRAM headroom for Windows display + KV cache overhead.  models.json
# "vram_gb" values are download size + this overhead.
_VRAM_OVERHEAD_GB = 1.5

_PULL_PREFIX = "ollama pull "
_INVALID_TAG_MSG = (
    "Invalid model tag. Use only letters, digits and . _ : / - "
    "(it cannot start with -).")


def _installed_text() -> str:
    return chr(0x2713) + " Installed"  # check mark + word


def _detect_gpu() -> tuple:
    """Detect GPU name and total VRAM via nvidia-smi.

    Returns (gpu_name, vram_total_mb) or (None, 0) if not available.
    """
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return None, 0
        line = result.stdout.strip().split("\n")[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            return parts[0], float(parts[1])
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
        pass
    return None, 0


def normalize_model_tag(tag: str) -> str:
    """Normalize an Ollama tag for exact comparison (missing tag = :latest)."""
    t = (tag or "").strip().lower()
    # Only the last path segment can carry a :tag (hf.co/org/repo:Q4_K_M)
    if t and ":" not in t.rsplit("/", 1)[-1]:
        t += ":latest"
    return t

# ── Background catalog refresh ────────────────────────────────────

_LIVE_FETCHERS = set()  # keeps a running QThread alive if its dialog closes


class _CatalogFetcher(QThread):
    """Fetches the remote models.json off the UI thread."""
    done = pyqtSignal(object)  # list of ModelEntry, or None

    def run(self):
        self.done.emit(model_catalog.fetch_remote_catalog())


# ── Pull worker ───────────────────────────────────────────────────

class _PullWorker(QThread):
    """Background thread that runs `ollama pull <tag>` (list args, no shell)."""
    progress = pyqtSignal(str)  # status line from ollama
    finished_ok = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, tag: str, parent=None):
        super().__init__(parent)
        self.tag = tag
        self._proc = None

    def cancel(self):
        """Terminate the running ollama pull (if any)."""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self):
        if not is_valid_tag(self.tag):
            self.error.emit(_INVALID_TAG_MSG)
            return
        try:
            proc = subprocess.Popen(
                ["ollama", "pull", "--", self.tag],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._proc = proc
            for line in proc.stdout:
                self.progress.emit(line.strip())
            proc.wait()
            if proc.returncode == 0:
                self.finished_ok.emit()
            else:
                self.error.emit(f"ollama pull exited with code {proc.returncode}")
        except (FileNotFoundError, OSError) as e:
            self.error.emit(str(e))


# ── Dialog ────────────────────────────────────────────────────────

class ModelSuggestionDialog(QDialog):
    """GPU-aware model recommendation dialog."""

    model_selected = pyqtSignal(str)  # ollama tag to use

    # Tests switch this off so no dialog touches the network.
    _AUTO_FETCH = True

    def __init__(self, installed_models: list = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Model Recommendations")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        self._installed = set(installed_models or [])
        self._pull_worker = None
        self._catalog = model_catalog.load_builtin()

        # Detect GPU
        self._gpu_name, self._vram_mb = _detect_gpu()
        self._recommended = self._catalog.pick_for_vram_mb(self._vram_mb)

        self._build_ui()
        if self._AUTO_FETCH:
            self._start_catalog_fetch()

    # ── remote refresh ────────────────────────────────────────────

    def _start_catalog_fetch(self):
        fetcher = _CatalogFetcher()  # no parent: may outlive the dialog
        _LIVE_FETCHERS.add(fetcher)
        fetcher.done.connect(self._on_catalog_fetched)
        fetcher.finished.connect(lambda f=fetcher: _LIVE_FETCHERS.discard(f))
        fetcher.start()

    def _on_catalog_fetched(self, catalog):
        """Swap in the remote list (already validated); None keeps built-in."""
        if catalog is None:
            return
        self._apply_catalog(catalog)

    def _apply_catalog(self, catalog):
        keep = self._current_tag()
        self._catalog = catalog
        self._recommended = catalog.pick_for_vram_mb(self._vram_mb)
        self._fill_tables()
        self._reselect(keep or self._recommended)

    # ── UI ────────────────────────────────────────────────────────

    def _make_table(self, group_title: str, max_height: int = 0):
        group = QGroupBox(group_title)
        box = QVBoxLayout(group)
        table = QTableWidget()
        table.setColumnCount(4)
        table.setHorizontalHeaderLabels(["Model", "VRAM", "Notes", "Status"])
        hdr = table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col in (0, 1, 3):
            hdr.setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        if max_height:
            table.setMaximumHeight(max_height)
        box.addWidget(table)
        return group, table

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Header (GPU name comes from nvidia-smi: escape it, label is rich text)
        if self._gpu_name:
            vram_gb = self._vram_mb / 1024
            header = QLabel(
                f"Detected: <b>{html.escape(self._gpu_name)}</b> "
                f"({vram_gb:.0f} GB VRAM)")
        else:
            header = QLabel("No NVIDIA GPU detected — showing all models")
        header.setStyleSheet("font-size: 11pt; padding: 4px;")
        layout.addWidget(header)

        # Recommended + experimental general models (Qwen)
        qwen_group, self._qwen_table = self._make_table(
            "Qwen3.5 — Recommended, plus experimental models", 220)
        self._qwen_table.selectionModel().selectionChanged.connect(
            self._on_qwen_selected)
        layout.addWidget(qwen_group)

        # Specialists
        sugoi_group, self._table = self._make_table(
            "Sugoi Ultra — Alternative Japanese → English specialist")
        self._table.selectionModel().selectionChanged.connect(
            self._on_sugoi_selected)
        layout.addWidget(sugoi_group)

        self._fill_tables()

        # ── Tag + buttons ─────────────────────────────────────────
        cmd_layout = QHBoxLayout()
        cmd_layout.addWidget(QLabel("Model tag:"))
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setPlaceholderText(
            "Select a model above, or type any Ollama tag")
        self._cmd_edit.textChanged.connect(self._update_buttons)
        cmd_layout.addWidget(self._cmd_edit, 1)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.setToolTip("Copy the ollama pull command")
        self._copy_btn.clicked.connect(self._copy_command)
        self._copy_btn.setEnabled(False)
        cmd_layout.addWidget(self._copy_btn)

        self._pull_btn = QPushButton("Pull Now")
        self._pull_btn.setToolTip("Download this model via Ollama")
        self._pull_btn.clicked.connect(self._pull_model)
        self._pull_btn.setEnabled(False)
        cmd_layout.addWidget(self._pull_btn)

        self._use_btn = QPushButton("Use Model")
        self._use_btn.setToolTip("Select this model and close")
        theme.make_primary(self._use_btn)
        self._use_btn.clicked.connect(self._use_model)
        self._use_btn.setEnabled(False)
        cmd_layout.addWidget(self._use_btn)

        layout.addLayout(cmd_layout)

        # Status label for pull progress
        self._pull_status = QLabel("")
        self._pull_status.setTextFormat(Qt.TextFormat.PlainText)
        self._pull_status.setStyleSheet(theme.hint_css())
        layout.addWidget(self._pull_status)

        # Close
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._reselect(self._recommended)

    def _fill_tables(self):
        """(Re)build both tables from the current catalog."""
        self._fill(self._qwen_table,
                   self._catalog.by_section("recommended", "experimental"))
        self._fill(self._table, self._catalog.by_section("specialist"))

    def _fill(self, table, entries):
        table.selectionModel().blockSignals(True)
        table.clearContents()
        table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            tag = entry.tag
            fits = self._fits(entry.vram_gb)
            is_rec = (tag == self._recommended)
            label = entry.label
            if entry.section == "experimental":
                label += " (experimental)"
            if is_rec:
                label += " (Recommended)"
            # QTableWidgetItem text is always plain text, never rich text
            items = [
                QTableWidgetItem(label),
                QTableWidgetItem(f"~{entry.vram_gb:g} GB"),
                QTableWidgetItem(entry.notes),
            ]
            if self._is_installed(tag):
                status = QTableWidgetItem(_installed_text())
                status.setForeground(theme.qcolor("ok"))
            elif fits:
                status = QTableWidgetItem("Available")
                status.setForeground(theme.qcolor("warn"))
            else:
                status = QTableWidgetItem("Too large")
                status.setForeground(theme.qcolor("text_dim"))
            items.append(status)
            for j, item in enumerate(items):
                if not fits and not is_rec:
                    item.setForeground(theme.qcolor("text_dim"))
                if is_rec:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                    item.setBackground(theme.qcolor("row_reviewed"))
                item.setToolTip(f"ollama pull {tag}")  # tag is validated
                item.setData(Qt.ItemDataRole.UserRole, tag)
                table.setItem(i, j, item)
        table.selectionModel().blockSignals(False)

    def _reselect(self, tag: str):
        """Select the row for ``tag`` (if listed) and show it in the edit."""
        norm = normalize_model_tag(tag)
        for table in (self._qwen_table, self._table):
            for i in range(table.rowCount()):
                item = table.item(i, 0)
                if item and normalize_model_tag(
                        item.data(Qt.ItemDataRole.UserRole)) == norm:
                    table.selectRow(i)  # fires the selection handler
                    return
        self._update_buttons()

    def _fits(self, vram_needed_gb: float) -> bool:
        """Check if a model fits in detected VRAM."""
        if not self._vram_mb:
            return True  # no GPU detected, show all as available
        return vram_needed_gb <= (self._vram_mb / 1024)

    def _is_installed(self, tag: str) -> bool:
        """Check if a model tag exactly matches an installed model."""
        norm = normalize_model_tag(tag)
        return any(normalize_model_tag(m) == norm for m in self._installed)

    def _current_tag(self) -> str:
        """The tag in the edit box (a pasted 'ollama pull ' prefix is dropped)."""
        text = self._cmd_edit.text().strip()
        if text.lower().startswith(_PULL_PREFIX):
            text = text[len(_PULL_PREFIX):].strip()
        return text

    def _get_selected_tag(self) -> str:
        """The tag to act on: selected row, or whatever the user typed."""
        return self._current_tag()

    @staticmethod
    def _row_tag(table) -> str:
        rows = table.selectionModel().selectedRows()
        if rows:
            item = table.item(rows[0].row(), 0)
            if item:
                return item.data(Qt.ItemDataRole.UserRole) or ""
        return ""

    def _on_sugoi_selected(self):
        """Specialist table selection: clear the other table, show the tag."""
        self._qwen_table.selectionModel().blockSignals(True)
        self._qwen_table.clearSelection()
        self._qwen_table.selectionModel().blockSignals(False)
        self._cmd_edit.setText(self._row_tag(self._table))

    def _on_qwen_selected(self):
        """Qwen table selection: clear the other table, show the tag."""
        self._table.selectionModel().blockSignals(True)
        self._table.clearSelection()
        self._table.selectionModel().blockSignals(False)
        self._cmd_edit.setText(self._row_tag(self._qwen_table))

    def _update_buttons(self, *_):
        """Enable buttons from the tag in the edit box."""
        tag = self._current_tag()
        valid = is_valid_tag(tag)
        if tag and not valid:
            self._pull_status.setText(_INVALID_TAG_MSG)
        elif self._pull_status.text() == _INVALID_TAG_MSG:
            self._pull_status.setText("")
        self._copy_btn.setEnabled(valid)
        # Never re-enable Pull while a pull is running
        self._pull_btn.setEnabled(valid and self._pull_worker is None)
        self._use_btn.setEnabled(valid and self._is_installed(tag))

    def _copy_command(self):
        """Copy the pull command to clipboard."""
        tag = self._current_tag()
        if is_valid_tag(tag):
            QApplication.clipboard().setText(f"{_PULL_PREFIX}{tag}")
            self._pull_status.setText("Copied to clipboard!")

    def _pull_model(self):
        """Run ollama pull in background."""
        tag = self._current_tag()
        if self._pull_worker is not None:
            return
        if not is_valid_tag(tag):
            self._pull_status.setText(_INVALID_TAG_MSG)
            return

        self._pull_btn.setEnabled(False)
        self._pull_btn.setText("Pulling...")
        self._pull_status.setText(f"Downloading {tag}...")

        self._pull_worker = _PullWorker(tag, self)
        self._pull_worker.progress.connect(self._on_pull_progress)
        self._pull_worker.finished_ok.connect(
            lambda: self._on_pull_done(tag, True))
        self._pull_worker.error.connect(
            lambda msg: self._on_pull_done(tag, False, msg))
        self._pull_worker.start()

    def _on_pull_progress(self, line: str):
        """Update pull status with latest line."""
        self._pull_status.setText(line)

    def _on_pull_done(self, tag: str, success: bool, error: str = ""):
        """Handle pull completion."""
        self._pull_btn.setText("Pull Now")
        self._pull_worker = None

        if success:
            self._installed.add(tag)
            self._pull_status.setText(f"Successfully pulled {tag}")
            self._fill_tables_keep_selection()
        else:
            self._pull_status.setText(f"Pull failed: {error}")
        self._update_buttons()

    def _fill_tables_keep_selection(self):
        keep = self._current_tag()
        self._fill_tables()
        self._reselect(keep)

    def done(self, result):
        """Stop any running pull before the dialog closes (Close/Esc/X)."""
        worker = self._pull_worker
        if worker is not None and worker.isRunning():
            for sig in (worker.progress, worker.finished_ok, worker.error):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            worker.cancel()
            if not worker.wait(3000):
                worker.cancel()  # proc may have started after first cancel
                worker.wait(10000)
            self._pull_worker = None
        super().done(result)

    def _use_model(self):
        """Emit the selected model tag and close."""
        tag = self._current_tag()
        if is_valid_tag(tag) and self._is_installed(tag):
            self.model_selected.emit(tag)
            self.accept()
