"""Start page shown while no project is open (instead of an empty table)."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
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


class WelcomePage(QWidget):
    """Friendly empty state with the two ways to get started."""

    def __init__(self, open_action, load_action, parent=None):
        super().__init__(parent)
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

    def apply_theme(self):
        self._card.setStyleSheet(
            f"QFrame#welcomeCard {{ background-color: {theme.c('panel')}; "
            f"border: 1px solid {theme.c('border')}; border-radius: 8px; }}")
        self._title.setStyleSheet("font-size: 18pt; font-weight: bold;")
        self._subtitle.setStyleSheet(
            f"font-size: 11pt; color: {theme.c('text_muted')};")
        self._engines.setStyleSheet(theme.hint_css() + " font-size: 8pt;")
        for badge, text in self._step_labels:
            badge.setStyleSheet(
                f"background-color: {theme.c('border')}; "
                f"color: {theme.c('accent')}; border-radius: 12px; "
                "font-weight: bold;")
            text.setStyleSheet(f"color: {theme.c('text_soft')};")
