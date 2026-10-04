"""Model suggestion dialog — GPU-aware model list (Qwen3.5 9B recommended,
Sugoi Ultra as the alternative JP→EN specialist)."""

import subprocess
import logging

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QPushButton, QLineEdit, QHeaderView,
    QApplication, QProgressDialog, QGroupBox, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from . import theme

log = logging.getLogger(__name__)

# ── Model database ────────────────────────────────────────────────

_SUGOI_MODELS = [
    # (label, quant, file_gb, vram_gb, quality, ollama_tag)
    ("Sugoi 14B", "Q2_K",  5.8,   7, "Fair",
     "hf.co/sugoitoolkit/Sugoi-14B-Ultra-GGUF:Q2_K"),
    ("Sugoi 14B", "Q4_K_M", 9.0, 10, "Good",
     "hf.co/sugoitoolkit/Sugoi-14B-Ultra-GGUF:Q4_K_M"),
    ("Sugoi 14B", "Q8_0", 15.7,  17, "Great",
     "hf.co/sugoitoolkit/Sugoi-14B-Ultra-GGUF:Q8_0"),
    ("Sugoi 14B", "F16",  29.5,  31, "Best",
     "hf.co/sugoitoolkit/Sugoi-14B-Ultra-GGUF:F16"),
    ("Sugoi 32B", "Q2_K", 12.3,  14, "Good",
     "hf.co/sugoitoolkit/Sugoi-32B-Ultra-GGUF:Q2_K"),
    ("Sugoi 32B", "Q4_K_M", 19.9, 21, "Great",
     "hf.co/sugoitoolkit/Sugoi-32B-Ultra-GGUF:Q4_K_M"),
    ("Sugoi 32B", "Q8_0", 34.8,  36, "Excellent",
     "hf.co/sugoitoolkit/Sugoi-32B-Ultra-GGUF:Q8_0"),
    ("Sugoi 32B", "F16",  65.5,  67, "Best",
     "hf.co/sugoitoolkit/Sugoi-32B-Ultra-GGUF:F16"),
]

# Recommended default for every target language
RECOMMENDED_MODEL = "qwen3.5:9b"

_QWEN3_MODELS = [
    # (label, vram_gb, ollama_tag, notes) — recommended model first
    ("Qwen3.5 9B",     8, RECOMMENDED_MODEL,
     "Recommended — 262K context, multimodal (text + images), ~6.6 GB"),
    ("Qwen3 8B",       8, "qwen3:8b", "Older general model"),
    ("Qwen3 14B",     12, "qwen3:14b", "Older general model"),
    ("Qwen3 14B Q8",  17, "qwen3:14b-q8_0", "Older general model, 8-bit"),
    ("Qwen3 30B MoE", 24, "qwen3:30b-a3b", "Older general model, large"),
]

# VRAM headroom for Windows display + KV cache overhead
_VRAM_OVERHEAD_GB = 1.5


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


def _best_sugoi_model(vram_mb: float) -> str:
    """Return the ollama tag of the best Sugoi model that fits in VRAM."""
    vram_gb = vram_mb / 1024
    usable = vram_gb - _VRAM_OVERHEAD_GB
    best = None
    for label, quant, file_gb, vram_needed, quality, tag in _SUGOI_MODELS:
        if vram_needed <= usable:
            best = tag  # keep updating — list is ordered by quality ascending
    return best or _SUGOI_MODELS[0][5]  # fallback to smallest


def _recommend_model(vram_mb: float = 0) -> str:
    """The recommended model: Qwen3.5 9B (~6.6 GB, partial offload on
    smaller GPUs still works)."""
    return RECOMMENDED_MODEL


# ── Pull worker ───────────────────────────────────────────────────

class _PullWorker(QThread):
    """Background thread that runs `ollama pull <tag>`."""
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
        try:
            proc = subprocess.Popen(
                ["ollama", "pull", self.tag],
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

    def __init__(self, installed_models: list = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Model Recommendations")
        self.setMinimumWidth(700)
        self.setMinimumHeight(500)
        self._installed = set(installed_models or [])
        self._pull_worker = None

        # Detect GPU
        self._gpu_name, self._vram_mb = _detect_gpu()
        self._recommended = _recommend_model(self._vram_mb)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Header
        if self._gpu_name:
            vram_gb = self._vram_mb / 1024
            header = QLabel(
                f"Detected: <b>{self._gpu_name}</b> "
                f"({vram_gb:.0f} GB VRAM)")
        else:
            header = QLabel("No NVIDIA GPU detected — showing all models")
        header.setStyleSheet("font-size: 11pt; padding: 4px;")
        layout.addWidget(header)

        # ── Sugoi models table ────────────────────────────────────
        sugoi_group = QGroupBox(
            "Sugoi Ultra — Alternative Japanese → English specialist")
        sugoi_layout = QVBoxLayout(sugoi_group)

        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Model", "Quant", "Size", "VRAM", "Quality", "Status"])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        self._table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setRowCount(len(_SUGOI_MODELS))

        for i, (label, quant, file_gb, vram_gb, quality, tag) in enumerate(_SUGOI_MODELS):
            fits = self._fits(vram_gb)
            installed = self._is_installed(tag)
            is_rec = (tag == self._recommended)

            items = [
                QTableWidgetItem(label),
                QTableWidgetItem(quant),
                QTableWidgetItem(f"{file_gb:.1f} GB"),
                QTableWidgetItem(f"~{vram_gb} GB"),
                QTableWidgetItem(quality),
            ]

            # Status
            if installed:
                status = QTableWidgetItem("Installed")
                status.setForeground(theme.qcolor("ok"))  # green
            elif fits:
                status = QTableWidgetItem("Available")
                status.setForeground(theme.qcolor("warn"))  # yellow
            else:
                status = QTableWidgetItem("Too large")
                status.setForeground(theme.qcolor("text_dim"))  # overlay0
            items.append(status)

            # Color coding
            for j, item in enumerate(items):
                if not fits:
                    item.setForeground(theme.qcolor("text_dim"))
                if is_rec:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                # Store tag for selection
                item.setData(Qt.ItemDataRole.UserRole, tag)
                self._table.setItem(i, j, item)

            # Highlight recommended row
            if is_rec:
                for j in range(6):
                    self._table.item(i, j).setBackground(
                        theme.qcolor("row_reviewed"))  # subtle green bg

        self._table.selectionModel().selectionChanged.connect(
            self._on_sugoi_selected)
        sugoi_layout.addWidget(self._table)
        layout.addWidget(sugoi_group)

        # ── Qwen3 models (compact) ───────────────────────────────
        qwen_group = QGroupBox(
            "Qwen3.5 — Recommended (all target languages, image OCR)")
        qwen_layout = QVBoxLayout(qwen_group)

        self._qwen_table = QTableWidget()
        self._qwen_table.setColumnCount(4)
        self._qwen_table.setHorizontalHeaderLabels(
            ["Model", "VRAM", "Notes", "Status"])
        self._qwen_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self._qwen_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self._qwen_table.verticalHeader().setVisible(False)
        self._qwen_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self._qwen_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection)
        self._qwen_table.setRowCount(len(_QWEN3_MODELS))
        self._qwen_table.setMaximumHeight(180)
        header = self._qwen_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)

        for i, (label, vram_gb, tag, notes) in enumerate(_QWEN3_MODELS):
            fits = self._fits(vram_gb)
            installed = self._is_installed(tag)
            is_rec = (tag == self._recommended)
            items = [
                QTableWidgetItem(f"{label} (Recommended)" if is_rec else label),
                QTableWidgetItem(f"~{vram_gb} GB"),
                QTableWidgetItem(notes),
            ]
            if installed:
                status = QTableWidgetItem("Installed")
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
                item.setToolTip(f"ollama pull {tag}")
                item.setData(Qt.ItemDataRole.UserRole, tag)
                self._qwen_table.setItem(i, j, item)

        self._qwen_table.selectionModel().selectionChanged.connect(
            self._on_qwen_selected)
        qwen_layout.addWidget(self._qwen_table)
        # Recommended family first, right under the GPU header
        layout.insertWidget(1, qwen_group)

        # ── Command + buttons ─────────────────────────────────────
        cmd_layout = QHBoxLayout()
        cmd_layout.addWidget(QLabel("Command:"))
        self._cmd_edit = QLineEdit()
        self._cmd_edit.setReadOnly(True)
        self._cmd_edit.setPlaceholderText("Select a model above")
        cmd_layout.addWidget(self._cmd_edit, 1)

        self._copy_btn = QPushButton("Copy")
        self._copy_btn.clicked.connect(self._copy_command)
        self._copy_btn.setEnabled(False)
        cmd_layout.addWidget(self._copy_btn)

        self._pull_btn = QPushButton("Pull Now")
        self._pull_btn.setToolTip("Download the selected model via Ollama")
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
        self._pull_status.setStyleSheet(theme.hint_css())
        layout.addWidget(self._pull_status)

        # Close
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        # Auto-select recommended
        if self._recommended:
            for table in (self._qwen_table, self._table):
                rows = [i for i in range(table.rowCount())
                        if table.item(i, 0) and table.item(i, 0).data(
                            Qt.ItemDataRole.UserRole) == self._recommended]
                if rows:
                    table.selectRow(rows[0])
                    break

    def _fits(self, vram_needed_gb: float) -> bool:
        """Check if a model fits in detected VRAM."""
        if not self._vram_mb:
            return True  # no GPU detected, show all as available
        return vram_needed_gb <= (self._vram_mb / 1024)

    def _is_installed(self, tag: str) -> bool:
        """Check if a model tag exactly matches an installed model."""
        norm = normalize_model_tag(tag)
        return any(normalize_model_tag(m) == norm for m in self._installed)

    def _get_selected_tag(self) -> str:
        """Get the ollama tag of the currently selected row."""
        # Check Sugoi table first
        rows = self._table.selectionModel().selectedRows()
        if rows:
            item = self._table.item(rows[0].row(), 0)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        # Check Qwen3 table
        rows = self._qwen_table.selectionModel().selectedRows()
        if rows:
            item = self._qwen_table.item(rows[0].row(), 0)
            if item:
                return item.data(Qt.ItemDataRole.UserRole)
        return ""

    def _on_sugoi_selected(self):
        """Sugoi table selection — clear Qwen3 selection and update command."""
        self._qwen_table.selectionModel().blockSignals(True)
        self._qwen_table.clearSelection()
        self._qwen_table.selectionModel().blockSignals(False)
        self._update_command()

    def _on_qwen_selected(self):
        """Qwen3 table selection — clear Sugoi selection and update command."""
        self._table.selectionModel().blockSignals(True)
        self._table.clearSelection()
        self._table.selectionModel().blockSignals(False)
        self._update_command()

    def _update_command(self):
        """Update command field based on current selection."""
        tag = self._get_selected_tag()
        if tag:
            self._cmd_edit.setText(f"ollama pull {tag}")
            self._copy_btn.setEnabled(True)
            # Never re-enable Pull while a pull is running
            self._pull_btn.setEnabled(self._pull_worker is None)
            self._use_btn.setEnabled(self._is_installed(tag))
        else:
            self._cmd_edit.clear()
            self._copy_btn.setEnabled(False)
            self._pull_btn.setEnabled(False)
            self._use_btn.setEnabled(False)

    def _copy_command(self):
        """Copy the pull command to clipboard."""
        cmd = self._cmd_edit.text()
        if cmd:
            QApplication.clipboard().setText(cmd)
            self._pull_status.setText("Copied to clipboard!")

    def _pull_model(self):
        """Run ollama pull in background."""
        tag = self._get_selected_tag()
        if not tag or self._pull_worker is not None:
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
        self._pull_btn.setEnabled(True)
        self._pull_worker = None

        if success:
            self._installed.add(tag)
            self._pull_status.setText(f"Successfully pulled {tag}")
            self._use_btn.setEnabled(True)
            # Update status column in table
            self._refresh_status_columns()
        else:
            self._pull_status.setText(f"Pull failed: {error}")

    def _refresh_status_columns(self):
        """Refresh the Status column in both tables after a pull."""
        for i in range(self._table.rowCount()):
            tag = self._table.item(i, 0).data(Qt.ItemDataRole.UserRole)
            status_item = self._table.item(i, 5)
            if self._is_installed(tag):
                status_item.setText("Installed")
                status_item.setForeground(theme.qcolor("ok"))

        for i in range(self._qwen_table.rowCount()):
            tag = self._qwen_table.item(i, 0).data(Qt.ItemDataRole.UserRole)
            status_item = self._qwen_table.item(i, 3)
            if self._is_installed(tag):
                status_item.setText("Installed")
                status_item.setForeground(theme.qcolor("ok"))

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
        tag = self._get_selected_tag()
        if tag and self._is_installed(tag):
            self.model_selected.emit(tag)
            self.accept()
