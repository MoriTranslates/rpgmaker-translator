"""Start page shown while no project is open (instead of an empty table).

The logo sits on the ink background under a soft candle glow; below it are
the two ways in (open a folder, or drop one anywhere on the page), recent
projects, the four steps and the supported engines as small badges.
"""

import html
import os

from PyQt6.QtCore import QPointF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap, QRadialGradient
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from . import theme

try:
    from ..resource_paths import resource_path
except ImportError:  # pragma: no cover
    resource_path = theme.resource_path

_ENGINES = ("RPG Maker MV / MZ", "VX Ace", "2000 / 2003", "Wolf RPG",
            "TyranoScript", "Kirikiri", "Ren'Py", "SRPG Studio", "Crowd")

_STEPS = (
    ("1", "Open", "Pick or drop the game folder; its text is extracted."),
    ("2", "Translate", "Run the wizard, or each step from the bar."),
    ("3", "Review", "Fix lines in the editor; progress saves itself."),
    ("4", "Apply", "Write the English into the game. Originals are backed up."),
)

# Recent list: rows visible before it scrolls
_RECENT_VISIBLE_ROWS = 4
_RECENT_ROW_HEIGHT = 44
_RECENT_PATH_WIDTH = 480  # px — longer paths are elided in the middle
_LOGO_WIDTH = 520


class _DropZone(QFrame):
    """Dashed target; clicking it opens the folder picker too."""

    def __init__(self, on_click, parent=None):
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._on_click = on_click
        row = QHBoxLayout(self)
        row.setContentsMargins(18, 14, 18, 14)
        row.setSpacing(12)
        row.addStretch()
        self.icon = QLabel()
        theme.bind_icon(lambda ic: self.icon.setPixmap(ic.pixmap(26, 26)),
                        "folder-down", "accent")
        row.addWidget(self.icon)
        text = QLabel("<b>Drop a game folder here</b><br>"
                      "<span>or click to browse — the engine is detected "
                      "automatically</span>")
        text.setObjectName("dropText")
        text.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(text)
        row.addStretch()

    def set_hover(self, on: bool):
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._on_click()
        super().mouseReleaseEvent(event)


class WelcomePage(QWidget):
    """Friendly start page with the ways to get started."""

    recent_selected = pyqtSignal(str)  # folder path of a recent project
    folder_dropped = pyqtSignal(str)   # folder dragged onto the page

    def __init__(self, open_action, load_action, parent=None):
        super().__init__(parent)
        self.setObjectName("welcomePage")
        self.setAcceptDrops(True)
        self._recent: list[str] = []
        self._logo_src = QPixmap(resource_path("assets", "logo_banner.png"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 12, 24, 24)
        outer.addStretch(1)

        col = QVBoxLayout()
        col.setSpacing(10)

        self._logo = QLabel()
        self._logo.setObjectName("welcomeLogo")
        self._logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._logo, 0, Qt.AlignmentFlag.AlignHCenter)

        self._title = QLabel("RPG Maker Translator")
        self._title.setObjectName("welcomeTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(self._title)

        self._subtitle = QLabel(
            "Play Japanese games in English — translated on your own PC, "
            "by a local AI.")
        self._subtitle.setObjectName("welcomeTagline")
        self._subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._subtitle.setWordWrap(True)
        col.addWidget(self._subtitle)
        col.addSpacing(8)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        buttons.addStretch()
        self.open_btn = QPushButton("Open Game Folder…")
        self.open_btn.setToolTip("Pick a game folder to start (Ctrl+O)")
        self.open_btn.setMinimumHeight(38)
        theme.make_primary(self.open_btn)
        theme.bind_icon(self.open_btn.setIcon, "folder-open", "on_accent")
        self.open_btn.clicked.connect(open_action.trigger)
        buttons.addWidget(self.open_btn)
        self.load_btn = QPushButton("Load Saved State…")
        self.load_btn.setToolTip(
            "Continue from a _translation_state.json file (Ctrl+L)")
        self.load_btn.setMinimumHeight(38)
        theme.bind_icon(self.load_btn.setIcon, "upload")
        self.load_btn.clicked.connect(load_action.trigger)
        buttons.addWidget(self.load_btn)
        buttons.addStretch()
        col.addLayout(buttons)
        col.addSpacing(6)

        self._drop = _DropZone(open_action.trigger)
        col.addWidget(self._drop)

        # ── Recent projects (hidden while the list is empty) ──
        self._recent_box = QWidget()
        self._recent_box.setObjectName("welcomeRecent")
        recent_col = QVBoxLayout(self._recent_box)
        recent_col.setContentsMargins(0, 6, 0, 0)
        recent_col.setSpacing(4)
        self._recent_header = QLabel("Recent projects")
        self._recent_header.setObjectName("sectionLabel")
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

        # ── The four steps, side by side ──
        col.addSpacing(10)
        steps = QHBoxLayout()
        steps.setSpacing(10)
        self._step_labels = []
        for num, head, body in _STEPS:
            card = QFrame()
            card.setObjectName("stepCard")
            box = QVBoxLayout(card)
            box.setContentsMargins(12, 10, 12, 10)
            box.setSpacing(4)
            top = QHBoxLayout()
            top.setSpacing(8)
            badge = QLabel(num)
            badge.setObjectName("stepBadge")
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setFixedSize(22, 22)
            top.addWidget(badge)
            name = QLabel(head)
            name.setObjectName("stepName")
            top.addWidget(name, 1)
            box.addLayout(top)
            text = QLabel(body)
            text.setObjectName("hint")
            text.setWordWrap(True)
            box.addWidget(text)
            box.addStretch()
            steps.addWidget(card, 1)
            self._step_labels.append((badge, text))
        col.addLayout(steps)

        # ── Supported engines as badges (two centered rows, never squeezed) ──
        col.addSpacing(8)
        half = (len(_ENGINES) + 1) // 2
        for names in (_ENGINES[:half], _ENGINES[half:]):
            engines = QHBoxLayout()
            engines.setSpacing(6)
            engines.addStretch()
            for name in names:
                chip = QLabel(name)
                chip.setObjectName("engineChip")
                engines.addWidget(chip)
            engines.addStretch()
            col.addLayout(engines)

        holder = QWidget()
        holder.setObjectName("welcomeHolder")
        holder.setLayout(col)
        holder.setMaximumWidth(760)
        center = QHBoxLayout()
        center.addStretch()
        center.addWidget(holder, 10)
        center.addStretch()
        outer.addLayout(center)
        outer.addStretch(2)

        self.apply_theme()

    # ── Background glow ──────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), theme.qcolor("window"))
        mid = self._logo.mapTo(self, self._logo.rect().center())
        center = QPointF(self.width() / 2, mid.y() + 10)
        radius = max(self.width(), self.height()) * 0.38
        grad = QRadialGradient(center, radius)
        glow = QColor(theme.c("glow"))
        # Eased falloff in small steps: a few linear stops show as rings
        peak = 170 if theme.is_dark() else 140
        for i in range(13):
            t = i / 12
            glow.setAlpha(int(peak * (1 - t) ** 2.2))
            grad.setColorAt(t, QColor(glow))
        painter.fillRect(self.rect(), grad)
        painter.end()

    # ── Drag and drop a game folder ──────────────────────────────

    @staticmethod
    def _dropped_folder(event) -> str:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        for url in urls:
            path = url.toLocalFile()
            if os.path.isfile(path):   # dropped Game.exe → its folder
                path = os.path.dirname(path)
            if path and os.path.isdir(path):
                return path
        return ""

    def dragEnterEvent(self, event):
        if self._dropped_folder(event):
            self._drop.set_hover(True)
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._drop.set_hover(False)

    def dropEvent(self, event):
        self._drop.set_hover(False)
        path = self._dropped_folder(event)
        if path:
            event.acceptProposedAction()
            self.folder_dropped.emit(path)

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
                f"font-size: 9.5pt;\">{html.escape(shown_path)}</span>")
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
        if not self._logo_src.isNull():
            dpr = self.devicePixelRatioF() or 1.0
            pm = self._logo_src.scaledToWidth(
                int(_LOGO_WIDTH * dpr),
                Qt.TransformationMode.SmoothTransformation)
            pm.setDevicePixelRatio(dpr)
            self._logo.setPixmap(pm)
            self._title.setVisible(False)   # the logo already says it
        t = theme.colors()
        # The logo is drawn for a night sky (white lettering).  On parchment
        # it gets one: a candlelit "window" card with a warm rim and a soft
        # shadow, so it reads as an illustration rather than a pasted box.
        night = theme.is_dark() or self._logo_src.isNull()
        if night:
            logo_css = "background: transparent; padding: 6px;"
            self._logo.setGraphicsEffect(None)
        else:
            logo_css = (
                "background: qradialgradient(cx:0.5, cy:0.62, radius:0.8, "
                "fx:0.5, fy:0.62, stop:0 #3a2a14, stop:0.5 #171d33, "
                "stop:1 #0f1424);"
                "border: 1px solid #d9bf8c; border-radius: 18px;"
                "padding: 10px 34px;")
            shadow = QGraphicsDropShadowEffect(self._logo)
            shadow.setBlurRadius(36)
            shadow.setOffset(0, 8)
            shadow.setColor(QColor(110, 72, 20, 70))
            self._logo.setGraphicsEffect(shadow)
        self.setStyleSheet(f"""
            QWidget#welcomeHolder, QWidget#welcomeRecent {{
                background: transparent;
            }}
            QLabel#welcomeLogo {{ {logo_css} }}
            QLabel#welcomeTitle {{ font-size: 20pt; font-weight: 700; }}
            QLabel#welcomeTagline {{
                font-size: 12pt; color: {t['text_soft']};
            }}
            QFrame#dropZone {{
                border: 2px dashed {t['border_strong']};
                border-radius: 12px;
                background-color: {t['panel']};
            }}
            QFrame#dropZone:hover, QFrame#dropZone[hover="true"] {{
                border-color: {t['accent']};
                background-color: {t['accent_soft']};
            }}
            QLabel#dropText {{ color: {t['text_soft']}; }}
            QLabel#sectionLabel {{
                font-weight: 600; color: {t['text_muted']};
            }}
            QFrame#stepCard {{
                background-color: {t['panel']};
                border: 1px solid {t['border']};
                border-radius: 10px;
            }}
            QLabel#stepBadge {{
                background-color: {t['accent_soft']}; color: {t['accent']};
                border-radius: 11px; font-weight: 700;
            }}
            QLabel#stepName {{ font-weight: 600; }}
            QLabel#engineChip {{
                background-color: {t['raised']}; color: {t['text_muted']};
                border: 1px solid {t['border']}; border-radius: 10px;
                padding: 2px 10px; font-size: 9.5pt;
            }}
            QListWidget#recentList {{
                background: transparent; border: none; outline: none;
            }}
            QListWidget#recentList::item {{ border-radius: 6px; }}
            QListWidget#recentList::item:hover {{
                background: {t['button']};
            }}
            QListWidget#recentList::item:selected {{
                background: {t['selection']};
            }}
        """)
        if self._recent:
            self.set_recent(self._recent)  # path color comes from the theme
        self.update()
