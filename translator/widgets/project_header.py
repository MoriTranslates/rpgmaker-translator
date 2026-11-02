"""Slim header over the workspace: game title, engine badge and progress."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

from . import theme


class ProjectHeader(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("projectHeader")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(46)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)
        layout.setSpacing(10)

        self._icon = QLabel()
        theme.bind_icon(lambda ic: self._icon.setPixmap(ic.pixmap(20, 20)),
                        "scroll-text", "accent_quiet")
        layout.addWidget(self._icon)

        self.title = QLabel("")
        self.title.setObjectName("projectTitle")
        layout.addWidget(self.title)

        self.engine = QLabel("")
        self.engine.setObjectName("engineBadge")
        layout.addWidget(self.engine)

        self.subtitle = QLabel("")
        self.subtitle.setObjectName("hint")
        layout.addWidget(self.subtitle, 1)

        self.counts = QLabel("")
        self.counts.setObjectName("projectCounts")
        layout.addWidget(self.counts)

        self.bar = QProgressBar()
        self.bar.setObjectName("headerProgress")
        self.bar.setTextVisible(False)
        self.bar.setFixedSize(180, 8)
        layout.addWidget(self.bar)

    def set_project(self, title: str, engine: str, subtitle: str = ""):
        self.title.setText(title)
        self.engine.setText(engine)
        self.engine.setVisible(bool(engine))
        self.subtitle.setText(subtitle)

    def set_progress(self, translated: int, reviewed: int, total: int):
        pct = round(100 * translated / total) if total else 0
        self.bar.setMaximum(max(total, 1))
        self.bar.setValue(translated)
        extra = f"  ·  {reviewed} reviewed" if reviewed else ""
        self.counts.setText(
            f"<b>{translated:,}</b> / {total:,} translated ({pct}%){extra}")
