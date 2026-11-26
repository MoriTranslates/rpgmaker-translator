"""Find Suspect Lines: counts per check, examples, and a table filter.

The checks (translator/suspects.py) only point at likely problems; on the
game they were tuned on, about half the flagged lines really needed a fix.
"""

import html

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout,
)

from .. import suspects
from . import theme

_EXAMPLES = 12


class SuspectDialog(QDialog):
    """Pick check kinds; Show in Table returns their line ids."""

    def __init__(self, entries, run, parent=None):
        """``run(include_reviewed: bool) -> SuspectReport``."""
        super().__init__(parent)
        self.setWindowTitle("Find Suspect Lines")
        self.setMinimumSize(760, 520)
        self._by_id = {e.id: e for e in entries}
        self._run = run
        self._report = run(False)
        self._build_ui()
        self._fill()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Quick checks for the kinds of mistakes that pass every other "
            "check: lost \"I\" in narration, dropped negations, \"he\" with no "
            "man in the Japanese, romanised sound effects... They only point; "
            "roughly half the flagged lines need a fix. Nothing is changed.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.include_reviewed = QCheckBox("Include lines marked Reviewed")
        self.include_reviewed.toggled.connect(self._rerun)
        layout.addWidget(self.include_reviewed)

        splitter = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Check", "Lines"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_examples)
        splitter.addWidget(self.table)
        self.examples = QTextBrowser()
        splitter.addWidget(self.examples)
        splitter.setSizes([220, 260])
        layout.addWidget(splitter)

        row = QHBoxLayout()
        self.total_label = QLabel()
        row.addWidget(self.total_label)
        row.addStretch()
        self.show_btn = QPushButton("Show in Table")
        theme.make_primary(self.show_btn)
        self.show_btn.setToolTip("Show only the lines of the checked kinds")
        self.show_btn.clicked.connect(self.accept)
        row.addWidget(self.show_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        row.addWidget(close_btn)
        layout.addLayout(row)

    def _rerun(self, include_reviewed: bool):
        self._report = self._run(include_reviewed)
        self._fill()

    def _fill(self):
        counts = self._report.counts()
        self._kinds = [k for k in suspects.KINDS if counts.get(k)]
        self.table.setRowCount(len(self._kinds))
        for row, kind in enumerate(self._kinds):
            item = QTableWidgetItem(suspects.KINDS[kind])
            item.setFlags((item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                          & ~Qt.ItemFlag.ItemIsEditable)
            item.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, item)
            n = QTableWidgetItem(str(counts[kind]))
            n.setFlags(n.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(row, 1, n)
        total = len(self._report.ids())
        self.total_label.setText(f"{total} lines flagged" if total
                                 else "No suspect lines found")
        self.show_btn.setEnabled(bool(total))
        if self._kinds:
            self.table.selectRow(0)
        else:
            self.examples.setPlainText("Nothing flagged.")

    def _show_examples(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        kind = self._kinds[rows[0].row()]
        items = self._report.by_kind.get(kind, [])
        parts = [f"<b>{html.escape(suspects.KINDS[kind])}</b> "
                 f"({len(items)}; first {min(len(items), _EXAMPLES)}):"]
        for s in items[:_EXAMPLES]:
            e = self._by_id.get(s.id)
            if e is None:
                continue
            jp = html.escape((e.original or "").strip()).replace("\n", "<br>")
            en = html.escape((e.translation or "").strip()).replace("\n", "<br>")
            parts.append(
                f"<p style='margin:6px 0'>{jp}<br>"
                f"<span style='color:gray'>{en}</span><br>"
                f"<i>{html.escape(s.reason)}</i></p>")
        self.examples.setHtml("".join(parts))

    def checked_kinds(self) -> list[str]:
        return [k for row, k in enumerate(self._kinds)
                if self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    def selected_ids(self) -> set[str]:
        return self._report.ids(set(self.checked_kinds()))
