"""Friendly placeholder for panes that have nothing to show yet.

A large tinted icon, one headline, a line of plain-English help and an
optional action button, centered.  ``EmptyStack`` swaps between it and the
real content so panels never show a bare grid.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from . import theme


class EmptyState(QWidget):
    """Centered icon + headline + help text (+ optional button)."""

    action = pyqtSignal()

    def __init__(self, icon: str, title: str, text: str = "",
                 button: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("emptyState")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addStretch(1)

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        theme.bind_icon(lambda ic: self._icon.setPixmap(ic.pixmap(48, 48)),
                        icon, "accent")
        layout.addWidget(self._icon)

        self.title = QLabel(title)
        self.title.setObjectName("emptyTitle")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)

        self.text = QLabel(text)
        self.text.setObjectName("hint")
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setWordWrap(True)
        self.text.setMaximumWidth(460)
        self.text.setVisible(bool(text))
        layout.addWidget(self.text, 0, Qt.AlignmentFlag.AlignHCenter)

        self.button = QPushButton(button)
        theme.make_primary(self.button)
        self.button.setVisible(bool(button))
        self.button.clicked.connect(self.action.emit)
        layout.addSpacing(4)
        layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)

    def set_text(self, title: str, text: str = ""):
        self.title.setText(title)
        self.text.setText(text)
        self.text.setVisible(bool(text))


class EmptyStack(QStackedWidget):
    """Shows ``content`` normally and ``empty`` while there is nothing."""

    def __init__(self, content: QWidget, empty: EmptyState, parent=None):
        super().__init__(parent)
        self.content = content
        self.empty = empty
        self.addWidget(content)
        self.addWidget(empty)

    def set_empty(self, empty: bool):
        self.setCurrentWidget(self.empty if empty else self.content)
