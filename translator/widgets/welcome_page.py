"""Start page shown while no project is open (instead of an empty table)."""

import html
import os

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QListWidget, QListWidgetItem, QApplication,
)

from . import theme

_ENGINES = (
    "RPG Maker MV / MZ · VX Ace · 2000/2003 · Wolf RPG · "
    "TyranoScript · Kirikiri · Ren'Py · SRPG Studio · Crowd"
)

_STEPS = (
    ("1", "Open the game folder",
     "The engine is detected automatically and all Japanese text is extracted."),
    ("2", "Translate",
     "Use the Translation Wizard, or run each step yourself from the "
     "Translate menu (database first, then dialogue)."),
    ("3", "Review",
     "Fix lines in the editor or the Event Viewer. Progress saves "
     "automatically."),
    ("4", "Apply to game",
     "Game › Apply Translation to Game writes the English text back. "
     "The original files are backed up first."),
)

# Recent list: rows visible before it scrolls
_RECENT_VISIBLE_ROWS = 5
_RECENT_ROW_HEIGHT = 40
_RECENT_PATH_WIDTH = 540  # px — longer paths are elided in the middle


class WelcomePage(QWidget):
    """Friendly empty state with the two ways to get started."""

    recent_selected = pyqtSignal(str)  # folder path of a recent project

    def __init__(self, open_action, load_action, parent=None):
        super().__init__(parent)
        self._recent: list[str] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(2)

        card = QFrame()
        card.setObjectName("welcomeCard")
        card.setMaximumWidth(640)
        col = QVBoxLayout(card)
        col.setContentsMargins(32, 28, 32, 28)
        col.setSpacing(10)

        self._title = QLabel("RPG Maker Translator")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._title)

        self._subtitle = QLabel(
            "Translate Japanese games to English with a local LLM.")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setWordWrap(True)
        col.addWidget(self._subtitle)
        col.addSpacing(12)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()
        self.open_btn = QPushButton("Open Game Folder…")
        self.open_btn.setToolTip("Pick a game folder to start (Ctrl+O)")
        self.open_btn.setMinimumHeight(34)
        theme.make_primary(self.open_btn)
        self.open_btn.clicked.connect(open_action.trigger)
        buttons.addWidget(self.open_btn)
        self.load_btn = QPushButton("Load Saved State…")
        self.load_btn.setToolTip(
            "Continue from a _translation_state.json file (Ctrl+L)")
        self.load_btn.setMinimumHeight(34)
        self.load_btn.clicked.connect(load_action.trigger)
        buttons.addWidget(self.load_btn)
        buttons.addStretch()
        col.addLayout(buttons)
        col.addSpacing(16)

        # ── Recent projects (hidden while the list is empty) ──
        self._recent_box = QWidget()
        recent_col = QVBoxLayout(self._recent_box)
        recent_col.setContentsMargins(0, 0, 0, 8)
        recent_col.setSpacing(4)
        self._recent_header = QLabel("Recent projects")
        recent_col.addWidget(self._recent_header)
        self.recent_list = QListWidget()
        self.recent_list.setObjectName("recentList")
        self.recent_list.setFrameShape(QFrame.Shape.NoFrame)
        self.recent_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent_list.setCursor(Qt.CursorShape.PointingHandCursor)
        self.recent_list.itemClicked.connect(self._on_recent_clicked)
        self.recent_list.itemActivated.connect(self._on_recent_activated)
        recent_col.addWidget(self.recent_list)
        col.addWidget(self._recent_box)
        self._recent_box.setVisible(False)

        self._step_labels = []
        for num, head, body in _STEPS:
            row = QHBoxLayout()
            row.setSpacing(12)
            badge = QLabel(num)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(24, 24)
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            text = QLabel(f"<b>{head}</b><br>{body}")
            text.setWordWrap(True)
            text.setTextFormat(Qt.TextFormat.RichText)
            row.addWidget(text, 1)
            col.addLayout(row)
            self._step_labels.append((badge, text))

        col.addSpacing(12)
        self._engines = QLabel("Supported: " + _ENGINES)
        self._engines.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._engines.setWordWrap(True)
        col.addWidget(self._engines)

        center = QHBoxLayout()
        center.addStretch()
        center.addWidget(card, 10)
        center.addStretch()
        outer.addLayout(center)
        outer.addStretch(3)

        self._card = card
        self.apply_theme()

    # ── Recent projects ──────────────────────────────────────────

    def set_recent(self, paths: list):
        """Show these folders (most recent first); hide the section if empty."""
        self._recent = list(paths)
        self.recent_list.clear()
        fm = self.fontMetrics()
        for path in self._recent:
            name = os.path.basename(os.path.normpath(path)) or path
            shown_path = fm.elidedText(
                path, Qt.TextElideMode.ElideMiddle, _RECENT_PATH_WIDTH)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            item.setSizeHint(QSize(0, _RECENT_ROW_HEIGHT))
            self.recent_list.addItem(item)
            label = QLabel(
                f"<span>{html.escape(name)}</span><br>"
                f"<span style=\"color: {theme.c('text_dim')}; "
                f"font-size: 8pt;\">{html.escape(shown_path)}</span>")
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setContentsMargins(8, 2, 8, 2)
            # Clicks go to the list item, not the label
            label.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.recent_list.setItemWidget(item, label)
        rows = min(len(self._recent), _RECENT_VISIBLE_ROWS)
        self.recent_list.setFixedHeight(rows * _RECENT_ROW_HEIGHT + 4)
        self._recent_box.setVisible(bool(self._recent))

    def _on_recent_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self.recent_selected.emit(path)

    def _on_recent_activated(self, item: QListWidgetItem):
        # Enter key only — mouse clicks are handled by itemClicked
        if QApplication.mouseButtons() == Qt.MouseButton.NoButton:
            self._on_recent_clicked(item)

    # ── Theme ────────────────────────────────────────────────────

    def apply_theme(self):
        self._card.setStyleSheet(
            f"QFrame#welcomeCard {{ background-color: {theme.c('panel')}; "
            f"border: 1px solid {theme.c('border')}; border-radius: 8px; }}")
        self._title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        self._subtitle.setStyleSheet(
            f"font-size: 11pt; color: {theme.c('text_muted')};")
        self._engines.setStyleSheet(theme.hint_css() + " font-size: 8pt;")
        self._recent_header.setStyleSheet(
            f"font-weight: bold; color: {theme.c('text_soft')};")
        self.recent_list.setStyleSheet(
            f"QListWidget#recentList {{ background: transparent; "
            f"border: none; outline: none; }}"
            f"QListWidget#recentList::item {{ border-radius: 4px; }}"
            f"QListWidget#recentList::item:hover {{ "
            f"background: {theme.c('button')}; }}"
            f"QListWidget#recentList::item:selected {{ "
            f"background: {theme.c('selection')}; }}")
        for badge, text in self._step_labels:
            badge.setStyleSheet(
                f"background-color: {theme.c('border')}; "
                f"color: {theme.c('accent')}; border-radius: 12px; "
                "font-weight: bold;")
            text.setStyleSheet(f"color: {theme.c('text_soft')};")
        if self._recent:
            self.set_recent(self._recent)  # path color comes from the theme
