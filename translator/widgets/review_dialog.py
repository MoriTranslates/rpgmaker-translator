"""Review with AI: send scenes to a strong model, then pick which fixes to apply.

Nothing changes until the user ticks fixes and presses Apply. Fixes that
fail validation (wrong line, codes changed, Japanese left, line edited
since...) are shown with the reason and can't be ticked.
"""

import html

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QRadioButton, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import review
from . import theme


class ReviewWorker(QThread):
    """Runs the chunks one by one (off the GUI thread)."""
    progress = pyqtSignal(int, int)          # done, total
    chunk_done = pyqtSignal(list)            # [Proposal]
    chunk_failed = pyqtSignal(int, str)      # chunk index, error

    def __init__(self, client, chunks, sheet, parent=None):
        super().__init__(parent)
        self._client = client
        self._chunks = chunks
        self._sheet = sheet
        self._stop = False

    def stop(self):
        self._stop = True
        self._client.cancel_event.set()

    def run(self):
        from ..ai_client import FatalProviderError, RequestCancelled
        total = len(self._chunks)
        for i, chunk in enumerate(self._chunks):
            if self._stop:
                break
            try:
                self.chunk_done.emit(
                    review.request_review(self._client, chunk, self._sheet))
            except RequestCancelled:
                break
            except FatalProviderError as e:      # bad key, no credit: stop
                self.chunk_failed.emit(i, str(e))
                break
            except (ConnectionError, ValueError) as e:
                # One bad reply shouldn't end the run; it is reported.
                self.chunk_failed.emit(i, str(e))
            self.progress.emit(i + 1, total)


class ReviewDialog(QDialog):
    """Scope -> run -> pick fixes."""

    def __init__(self, entries, client, sheet: str, scopes: dict, parent=None):
        """``scopes``: {label: set of ids or None for all}."""
        super().__init__(parent)
        self.setWindowTitle("Review with AI")
        self.setMinimumSize(980, 620)
        self._entries = entries
        self._client = client
        self._sheet = sheet
        self._scopes = scopes
        self._worker = None
        self._proposals: list = []
        self._errors: list[str] = []
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────
    def _build_ui(self):
        layout = QVBoxLayout(self)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)
        self.stack.addWidget(self._build_setup_page())
        self.stack.addWidget(self._build_results_page())

    def _build_setup_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        intro = QLabel(
            "A model reads each scene with the Japanese and the current "
            "English side by side, plus the character sheet, and proposes "
            "fixes for wrong meaning, wrong person, dropped negations, broken "
            "English and romanised sound effects. You then pick which fixes "
            "to apply. Nothing changes until you press Apply.\n\n"
            "Use a strong model: small local models miss subtle meaning "
            "flips. Lines marked Reviewed are never sent or changed.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.scope_group = QButtonGroup(self)
        self._scope_chunks = {}
        for i, (label, ids) in enumerate(self._scopes.items()):
            chunks = review.build_chunks(self._entries, ids)
            self._scope_chunks[label] = chunks
            n = sum(len(c.lines) for c in chunks)
            rb = QRadioButton(f"{label}: {n} lines in {len(chunks)} requests")
            rb.setEnabled(n > 0)
            self.scope_group.addButton(rb, i)
            lay.addWidget(rb)
        first = next((b for b in self.scope_group.buttons() if b.isEnabled()), None)
        if first:
            first.setChecked(True)
        model = getattr(self._client, "model", "") or "(no model)"
        provider = getattr(self._client, "provider", "") or "Ollama (Local)"
        lay.addWidget(QLabel(f"Model: {model} ({provider})"))
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch()
        row = QHBoxLayout()
        row.addStretch()
        self.start_btn = QPushButton("Start Review")
        theme.make_primary(self.start_btn)
        self.start_btn.setEnabled(first is not None)
        self.start_btn.clicked.connect(self.start)
        row.addWidget(self.start_btn)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop)
        row.addWidget(self.stop_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        lay.addLayout(row)
        return page

    def _build_results_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        self.results = QTableWidget(0, 4)
        self.results.setHorizontalHeaderLabels(
            ["Japanese", "Current English", "Proposed fix", "Why / problem"])
        self.results.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.results.setWordWrap(True)
        header = self.results.horizontalHeader()
        for col in range(4):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.results)
        row = QHBoxLayout()
        all_btn = QPushButton("Tick All Valid")
        all_btn.clicked.connect(lambda: self._tick_all(True))
        row.addWidget(all_btn)
        none_btn = QPushButton("Untick All")
        none_btn.clicked.connect(lambda: self._tick_all(False))
        row.addWidget(none_btn)
        row.addStretch()
        self.apply_btn = QPushButton("Apply Ticked Fixes")
        theme.make_primary(self.apply_btn)
        self.apply_btn.clicked.connect(self.accept)
        row.addWidget(self.apply_btn)
        close_btn = QPushButton("Close Without Applying")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        lay.addLayout(row)
        return page

    # ── Run ─────────────────────────────────────────────────────────
    def selected_scope(self) -> str:
        btn = self.scope_group.checkedButton()
        return list(self._scopes)[self.scope_group.id(btn)] if btn else ""

    def start(self):
        chunks = self._scope_chunks.get(self.selected_scope(), [])
        if not chunks:
            return
        self._client.cancel_event.clear()
        self._proposals, self._errors = [], []
        self.progress.setRange(0, len(chunks))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        for b in self.scope_group.buttons():
            b.setEnabled(False)
        self.status.setText("Reviewing...")
        self._worker = ReviewWorker(self._client, chunks, self._sheet, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.chunk_done.connect(self._proposals.extend)
        self._worker.chunk_failed.connect(
            lambda i, err: self._errors.append(f"request {i + 1}: {err}"))
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def stop(self):
        if self._worker is not None:
            self._worker.stop()
            self.stop_btn.setEnabled(False)
            self.status.setText("Stopping after the current request...")

    def _on_progress(self, done, total):
        self.progress.setValue(done)
        self.status.setText(f"Reviewed {done} of {total} requests; "
                            f"{len(self._proposals)} fixes proposed so far.")

    def _on_finished(self):
        self._client.cancel_event.clear()
        self.stop_btn.setEnabled(False)
        review.validate_all(self._proposals, self._entries)
        self.show_results()

    def show_results(self):
        valid = [p for p in self._proposals if not p.problem]
        bad = [p for p in self._proposals if p.problem]
        text = (f"{len(valid)} fixes proposed"
                + (f", {len(bad)} rejected by the safety checks (shown last, "
                   "can't be applied)" if bad else "")
                + ". Untick any you don't want, then Apply. Applied fixes "
                "reach the game on the next Export to Game; Undo works.")
        if self._errors:
            text += (f"\n{len(self._errors)} requests failed and were skipped: "
                     + "; ".join(self._errors[:3])
                     + (" ..." if len(self._errors) > 3 else ""))
        self.summary.setText(text)
        rows = valid + bad
        self._rows = rows
        self.results.setRowCount(len(rows))
        for r, p in enumerate(rows):
            jp = QTableWidgetItem(p.original)
            if not p.problem:
                jp.setFlags(jp.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                jp.setCheckState(Qt.CheckState.Checked)
            else:
                jp.setFlags(jp.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            for c, (item, text_) in enumerate((
                    (jp, None), (None, p.old), (None, p.new),
                    (None, p.problem and f"Not applied: {p.problem}" or p.why))):
                it = item or QTableWidgetItem(text_)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if p.problem:
                    it.setForeground(theme.qcolor("text_soft"))
                it.setToolTip(html.escape(it.text()))
                self.results.setItem(r, c, it)
        self.results.resizeRowsToContents()
        self.apply_btn.setEnabled(bool(valid))
        self.stack.setCurrentIndex(1)

    def _tick_all(self, on: bool):
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for r, p in enumerate(self._rows):
            if not p.problem:
                self.results.item(r, 0).setCheckState(state)

    def ticked(self) -> list:
        """Proposals the user ticked (only valid ones can be ticked)."""
        return [p for r, p in enumerate(getattr(self, "_rows", []))
                if not p.problem
                and self.results.item(r, 0).checkState() == Qt.CheckState.Checked]

    def reject(self):
        if self._worker is not None and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(5000)
        super().reject()

    def accept(self):
        if self._worker is not None and self._worker.isRunning():
            return
        super().accept()
