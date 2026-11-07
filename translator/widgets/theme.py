"""Central theme: the "candlelit study" palettes, the app stylesheet and shared status visuals.

Every widget pulls colors from here instead of hard-coding hex values, so
dark (ink night) and light (parchment) mode stay consistent.  ``apply()`` is called
by the main window at startup and whenever the user toggles dark mode;
widgets that paint per-item colors re-read ``qcolor()`` when they refresh.
"""

import os

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import (
    QColor, QFontDatabase, QIcon, QPainter, QPalette, QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

try:
    from ..resource_paths import resource_path
except ImportError:  # pragma: no cover — older checkout without the helper
    def resource_path(*parts):
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        return os.path.join(root, *parts)

# ── Palettes ───────────────────────────────────────────────────────

# "Candlelit study" — colors taken from the app's logo: ink-navy night,
# parchment text, candle-amber as the one primary accent, the navy coat's
# blue as a cool secondary.

# Semantic tokens → dark theme (default)
DARK = {
    "window":       "#0f1424",  # ink night
    "panel":        "#131a2c",  # menubar/toolbar/statusbar
    "field":        "#111729",  # inputs, tables, trees
    "field_alt":    "#151c31",  # alternate rows / hover
    "raised":       "#1c2440",  # cards, menus, popups
    "border":       "#222b45",  # hairlines between surfaces
    "border_strong": "#35405f",  # input outlines, splitters
    "control_border": "#8a8fa8",  # checkbox/radio outlines (3:1+ on field)
    "button":       "#1f2842",
    "button_hover": "#2a3556",
    "button_pressed": "#35426a",
    "selection":    "#2b3456",
    "selection_text": "#f4eddf",
    "text":         "#ece4d4",  # parchment
    "text_soft":    "#d8cfbd",
    "text_muted":   "#a9a294",  # 6.8:1 on panel
    "text_dim":     "#969080",  # 5.8:1 on window
    "accent":       "#f2a93b",  # candle amber — the one primary accent
    "accent_hover": "#f7bd5e",
    "accent_pressed": "#d98f22",
    "accent_fill":  "#f2a93b",  # primary button fill
    "accent_fill_hover": "#f7bd5e",
    "on_accent":    "#1a1206",
    "accent_alt":   "#6ea8ff",  # navy-coat blue: info, links, translated
    "accent_soft":  "#33291a",  # amber wash for the running step
    "accent_quiet": "#c98a2e",  # secondary amber (tab underline, badges)
    "alt_soft":     "#16294a",  # blue wash for active filter chips
    "scroll_thumb": "#5a6690",  # 3:1 on field
    "error_soft":   "#3a1f1c",
    "ok_soft":      "#17302a",
    "ok":           "#8fd18a",
    "status_translated": "#6ea8ff",
    "status_untranslated": "#c46a52",  # muted ember: still to do, not an error
    "warn":         "#f2c14e",
    "caution":      "#f2994a",
    "error":        "#f26b4f",  # ember
    "special":      "#b79cf7",
    "tooltip":      "#1c2440",
    "glow":         "#3a2a12",  # candlelight behind the welcome logo
    # Row tints by entry status — kept faint; the status stripe carries meaning
    "row_untranslated": "#1e1722",
    "row_translated":   "#111729",
    "row_reviewed":     "#112222",
    "row_skipped":      "#121827",
}

# Semantic tokens → light theme (parchment)
LIGHT = {
    "window":       "#f6f0e2",
    "panel":        "#efe6d2",
    "field":        "#fffaf0",
    "field_alt":    "#f9f2e3",
    "raised":       "#fffaf0",
    "border":       "#e3d7bd",
    "border_strong": "#cdbd9a",
    "control_border": "#857760",
    "button":       "#efe6d2",
    "button_hover": "#e6dac0",
    "button_pressed": "#dccfb0",
    "selection":    "#dde5f3",  # cool ink wash, never mistaken for amber
    "selection_text": "#1b2238",
    "text":         "#1b2238",  # ink navy
    "text_soft":    "#2b3350",
    "text_muted":   "#4f5670",
    "text_dim":     "#6b6658",  # 4.6:1+ on panel
    "accent":       "#9a5500",  # deep amber — 5:1 as text on parchment
    "accent_hover": "#b36400",
    "accent_pressed": "#7f4600",
    "accent_fill":  "#e9a23b",  # primary button fill, ink text on it
    "accent_fill_hover": "#f0b255",
    "on_accent":    "#1b1206",
    "accent_alt":   "#2f5fb3",
    "accent_soft":  "#f6e3bd",
    "accent_quiet": "#a8661a",
    "alt_soft":     "#dbe7fb",
    "scroll_thumb": "#9c8a66",
    "error_soft":   "#fbe1d6",
    "ok_soft":      "#e1f0dc",
    "ok":           "#2e7d32",
    "status_translated": "#2f5fb3",
    "status_untranslated": "#b4532f",
    "warn":         "#8f5400",
    "caution":      "#b45309",
    "error":        "#c2410c",
    "special":      "#7c3aed",
    "tooltip":      "#fffaf0",
    "glow":         "#f3d9a6",
    "row_untranslated": "#fbece3",
    "row_translated":   "#fffaf0",
    "row_reviewed":     "#eef5e6",
    "row_skipped":      "#f3ede0",
}

# Speaker chips: (background, text) per theme, picked by a hash of the name
SPEAKER_CHIPS = {
    True: [("#3a2c14", "#f6c76e"),   # amber
           ("#3a1d2a", "#f4a3bd"),   # rose
           ("#123331", "#7fd8cc"),   # teal
           ("#16294a", "#8fbcff"),   # sky
           ("#2a2147", "#c3adff"),   # violet
           ("#21301a", "#b3d98c")],  # moss
    False: [("#f8e3b8", "#6b4200"),
            ("#f8dbe4", "#8a2148"),
            ("#d4efea", "#14594f"),
            ("#dbe7fb", "#1f4c94"),
            ("#e6defb", "#4b2c94"),
            ("#e2efd6", "#38591d")],
}


def speaker_chip(name: str) -> tuple[str, str]:
    """Stable (background, text) colors for a speaker name."""
    palette = SPEAKER_CHIPS[is_dark()]
    h = 0
    for ch in name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return palette[h % len(palette)]


_state = {"dark": True}


def is_dark() -> bool:
    return _state["dark"]


def colors() -> dict:
    return DARK if _state["dark"] else LIGHT


def c(name: str) -> str:
    """Hex color for a semantic token in the current theme."""
    return colors()[name]


def qcolor(name: str) -> QColor:
    return QColor(colors()[name])


# ── Shared status visuals (never rely on color alone) ──────────────

# Text fallbacks (exports, tooltips); the tables draw these shapes themselves
STATUS_ICONS = {
    "untranslated": "○",
    "translated":   "●",
    "reviewed":     "✓",
    "skipped":      "—",
}

STATUS_LABELS = {
    "untranslated": "Untranslated",
    "translated":   "Translated (not reviewed)",
    "reviewed":     "Reviewed",
    "skipped":      "Skipped",
}

_STATUS_FG = {
    "untranslated": "status_untranslated",
    "translated":   "status_translated",
    "reviewed":     "ok",
    "skipped":      "text_dim",
}


def status_row_color(status: str) -> QColor:
    return qcolor("row_" + status) if ("row_" + status) in colors() \
        else qcolor("field")


def status_fg(status: str) -> QColor:
    return qcolor(_STATUS_FG.get(status, "text"))


# ── Small reusable style snippets ──────────────────────────────────

def hint_css() -> str:
    """Secondary/help text under or beside controls."""
    return f"color: {c('text_muted')};"


def heading_css() -> str:
    return "font-weight: 600; font-size: 11pt;"


# ── Application palette + stylesheet ───────────────────────────────

def qpalette() -> QPalette:
    t = colors()
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: t["window"],
        QPalette.ColorRole.WindowText: t["text"],
        QPalette.ColorRole.Base: t["field"],
        QPalette.ColorRole.AlternateBase: t["field_alt"],
        QPalette.ColorRole.Text: t["text"],
        QPalette.ColorRole.Button: t["button"],
        QPalette.ColorRole.ButtonText: t["text"],
        QPalette.ColorRole.Highlight: t["selection"],
        QPalette.ColorRole.HighlightedText: t["selection_text"],
        QPalette.ColorRole.ToolTipBase: t["tooltip"],
        QPalette.ColorRole.ToolTipText: t["text"],
        QPalette.ColorRole.PlaceholderText: t["text_dim"],
        QPalette.ColorRole.Link: t["accent"],
        QPalette.ColorRole.Mid: t["border"],
        QPalette.ColorRole.Midlight: t["border_strong"],
        QPalette.ColorRole.Dark: t["panel"],
        QPalette.ColorRole.Shadow: t["panel"],
        QPalette.ColorRole.Light: t["button_hover"],
        QPalette.ColorRole.BrightText: t["error"],
    }
    for role, hexval in roles.items():
        p.setColor(role, QColor(hexval))
    dim = QColor(t["text_dim"])
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, dim)
    return p


_STYLESHEET = """
QMainWindow, QDialog, QWidget {{
    background-color: {window};
    color: {text};
}}
QMenuBar {{
    background-color: {panel};
    color: {text};
    padding: 2px 4px;
}}
QMenuBar::item {{
    padding: 5px 10px;
    background: transparent;
    border-radius: 6px;
}}
QMenuBar::item:selected {{
    background-color: {button};
}}
QToolBar {{
    background-color: {panel};
    border: none;
    border-bottom: 1px solid {border};
    spacing: 2px;
    padding: 4px 6px;
}}
QToolButton {{
    background: transparent;
    color: {text};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 8px;
}}
QToolButton:hover {{
    background-color: {button};
}}
QToolButton:pressed {{
    background-color: {button_pressed};
}}
QToolButton:disabled {{
    color: {text_dim};
}}
QToolButton:focus {{
    border: 1px solid {accent};
}}
QToolBar::separator {{
    background-color: {border};
    width: 1px;
    margin: 4px 6px;
}}
QMenu {{
    background-color: {raised};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 28px 6px 10px;
    border-radius: 5px;
}}
QMenu::icon {{
    padding-left: 10px;
}}
QMenu::item:selected {{
    background-color: {selection};
    color: {selection_text};
}}
QMenu::item:disabled {{
    color: {text_dim};
}}
QMenu::separator {{
    height: 1px;
    background-color: {border};
    margin: 5px 6px;
}}
QToolTip {{
    background-color: {raised};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: 6px;
    padding: 5px 8px;
}}
QTreeWidget, QTreeView, QTableWidget, QTableView, QListWidget, QListView {{
    background-color: {field};
    alternate-background-color: {field_alt};
    color: {text};
    border: none;
    selection-background-color: {selection};
    selection-color: {selection_text};
    outline: 0;
}}
QTableView, QTableWidget {{
    gridline-color: {border};
}}
QTableWidget::item, QTableView::item {{
    padding: 4px 8px;
    border-bottom: 1px solid {border};
}}
QTreeView::item, QTreeWidget::item {{
    padding: 3px 2px;
}}
QTreeView::item:hover, QTreeWidget::item:hover,
QListView::item:hover, QListWidget::item:hover {{
    background-color: {field_alt};
}}
QTreeView::item:selected, QTreeWidget::item:selected,
QTableView::item:selected, QTableWidget::item:selected,
QListView::item:selected, QListWidget::item:selected {{
    background-color: {selection};
    color: {selection_text};
}}
QHeaderView {{
    background-color: {panel};
    border: none;
}}
QHeaderView::section {{
    background-color: {panel};
    color: {text_muted};
    border: none;
    border-bottom: 1px solid {border_strong};
    padding: 6px 8px;
    font-weight: 600;
}}
QTableCornerButton::section {{
    background-color: {panel};
    border: none;
}}
QPlainTextEdit, QTextEdit, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {field};
    color: {text};
    border: 1px solid {border_strong};
    border-radius: 6px;
    selection-background-color: {selection};
    selection-color: {selection_text};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    padding: 4px 8px;
    min-height: 18px;
}}
QPlainTextEdit, QTextEdit {{
    padding: 6px;
    border-radius: 8px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 2px solid {accent};
    padding: 3px 7px;
}}
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 2px solid {accent};
    padding: 5px;
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {text_dim};
    border-color: {border};
}}
QComboBox QAbstractItemView {{
    background-color: {raised};
    color: {text};
    border: 1px solid {border_strong};
    padding: 4px;
    selection-background-color: {selection};
    selection-color: {selection_text};
}}
QPushButton {{
    background-color: {button};
    color: {text};
    border: 1px solid {border_strong};
    padding: 6px 16px;
    border-radius: 6px;
    min-height: 18px;
}}
QPushButton:hover {{
    background-color: {button_hover};
}}
QPushButton:pressed {{
    background-color: {button_pressed};
}}
QPushButton:focus {{
    border: 2px solid {accent};
    padding: 5px 15px;
}}
QPushButton:disabled {{
    color: {text_dim};
    border-color: {border};
}}
QPushButton[primary="true"] {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {accent_fill_hover}, stop:1 {accent_fill});
    color: {on_accent};
    border: 1px solid {accent_fill};
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background-color: {accent_fill_hover};
    border-color: {accent_fill_hover};
}}
QPushButton[primary="true"]:pressed {{
    background-color: {accent_pressed};
}}
QPushButton[primary="true"]:focus {{
    border: 2px solid {text};
    padding: 5px 15px;
}}
QPushButton[primary="true"]:disabled {{
    background-color: {button};
    color: {text_dim};
    border-color: {border};
}}
QProgressBar {{
    border: none;
    border-radius: 4px;
    background-color: {button};
    text-align: center;
    color: {text};
}}
QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 4px;
}}
QProgressBar#meter {{
    border-radius: 3px;
}}
QWidget#gpuMonitor {{
    background-color: {panel};
    border-top: 1px solid {border};
}}
QStatusBar {{
    background-color: {panel};
    color: {text_muted};
    border-top: 1px solid {border};
}}
QStatusBar QLabel, QLabel#hint {{
    color: {text_muted};
}}
QGroupBox {{
    border: none;
    margin-top: 22px;
    padding: 0;
    color: {text};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    padding: 0 0 4px 0;
    color: {text_muted};
    font-weight: 600;
}}
QTabWidget::pane {{
    border: none;
    border-top: 1px solid {border};
    top: -1px;
}}
QTabBar {{
    qproperty-drawBase: 0;
}}
QTabBar::tab {{
    background: transparent;
    color: {text_muted};
    padding: 8px 16px;
    border: none;
    border-bottom: 2px solid transparent;
    margin-right: 2px;
}}
QTabBar::tab:hover {{
    color: {text};
    border-bottom-color: {border_strong};
}}
QTabBar::tab:selected {{
    color: {text};
    border-bottom: 2px solid {accent_quiet};
}}
QSplitter::handle {{
    background-color: {border_strong};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}
QSplitter::handle:hover {{
    background-color: {accent};
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background-color: {scroll_thumb};
    border-radius: 3px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    min-height: 32px;
}}
QScrollBar::handle:horizontal {{
    min-width: 32px;
}}
QScrollBar::handle:hover {{
    background-color: {text_dim};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0;
    height: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QLabel {{
    color: {text};
    background: transparent;
}}
QMessageBox QLabel {{
    min-width: 320px;
}}
QMessageBox QPushButton, QDialogButtonBox QPushButton {{
    padding: 6px 24px;
    min-width: 80px;
}}
QMessageBox QPushButton:focus, QDialogButtonBox QPushButton:focus {{
    padding: 5px 23px;
}}
QCheckBox, QRadioButton {{
    color: {text};
    spacing: 8px;
    background: transparent;
}}
QCheckBox:focus, QRadioButton:focus {{
    color: {accent};
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {control_border};
    border-radius: 4px;
    background-color: {field};
}}
QCheckBox::indicator:hover {{
    border-color: {accent};
}}
QCheckBox::indicator:checked {{
    background-color: {accent};
    border-color: {accent};
}}
QCheckBox::indicator:disabled {{
    background-color: {button};
    border-color: {border};
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {control_border};
    border-radius: 9px;
    background-color: {field};
}}
QRadioButton::indicator:hover {{
    border-color: {accent};
}}
QRadioButton::indicator:checked {{
    border-color: {accent};
    background-color: qradialgradient(cx:0.5, cy:0.5, radius:0.5,
        fx:0.5, fy:0.5, stop:0 {accent}, stop:0.5 {accent},
        stop:0.6 {field}, stop:1 {field});
}}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    width: 18px;
    height: 13px;
    border: none;
    border-left: 1px solid {border};
    background: transparent;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: 6px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: 6px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {button_hover};
}}
QScrollArea {{
    border: none;
}}
QWidget#filterBar {{
    background-color: {panel};
    border-bottom: 1px solid {border};
}}
QComboBox[chip="true"], QToolButton[chip="true"] {{
    background-color: {button};
    border: 1px solid {border_strong};
    border-radius: 14px;
    padding: 3px 12px;
    color: {text_muted};
    min-height: 20px;
}}
QComboBox[chip="true"]:hover, QToolButton[chip="true"]:hover {{
    color: {text};
    border-color: {control_border};
}}
QComboBox[chip="true"]:focus, QToolButton[chip="true"]:focus {{
    border: 2px solid {accent};
    padding: 2px 11px;
}}
QComboBox[chip="true"][active="true"], QToolButton[chip="true"]:checked {{
    background-color: {alt_soft};
    border-color: {accent_alt};
    color: {accent_alt};
    font-weight: 600;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
    subcontrol-position: center right;
}}
QComboBox[chip="true"] {{
    padding-right: 26px;
}}
QComboBox[chip="true"]::drop-down {{
    border: none;
    width: 22px;
    subcontrol-position: center right;
}}
QStackedWidget#editorPane, QWidget#eventEditorPane {{
    background-color: {panel};
}}
QStackedWidget#editorPane QTabWidget::pane {{
    border-top: 1px solid {border};
}}
QWidget#projectHeader {{
    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {accent_soft}, stop:0.3 {panel}, stop:1 {panel});
    border-bottom: 1px solid {border};
}}
QLabel#projectTitle {{
    font-size: 12pt;
    font-weight: 600;
    color: {text};
}}
QLabel#engineBadge {{
    background-color: {raised};
    color: {text_soft};
    border: 1px solid {border_strong};
    border-radius: 10px;
    padding: 1px 9px;
    font-size: 9.5pt;
    font-weight: 600;
}}
QLabel#projectCounts {{
    color: {text_soft};
}}
QProgressBar#headerProgress {{
    background-color: {button};
    border-radius: 4px;
}}
QProgressBar#headerProgress::chunk {{
    background-color: {status_translated};
    border-radius: 4px;
}}
QLabel#emptyTitle {{
    font-size: 14pt;
    font-weight: 600;
    color: {text};
}}
QWidget#pipelineBar {{
    background-color: {panel};
    border-bottom: 1px solid {border};
}}
QWidget#pipelineBar QLabel#hint {{
    color: {text_muted};
}}
QToolButton[step] {{
    background: transparent;
    border: none;
    border-radius: 6px;
    padding: 5px 10px;
    color: {text_muted};
}}
QToolButton[step]:hover {{
    background-color: {button};
    color: {text};
}}
QToolButton[step="pending"] {{
    color: {text_dim};
}}
QToolButton[step="next"] {{
    color: {text};
}}
QToolButton[step="active"] {{
    background-color: {accent_soft};
    color: {accent};
    font-weight: 600;
}}
QToolButton[step="done"] {{
    color: {ok};
}}
QToolButton#stopButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 12px;
    color: {text_dim};
}}
QToolButton#stopButton:enabled {{
    color: {error};
    background-color: {error_soft};
    border-color: {error};
    font-weight: 600;
}}
QToolButton#applyButton {{
    border: 1px solid {border_strong};
    border-radius: 6px;
    padding: 5px 12px;
    color: {text_soft};
}}
QToolButton#applyButton:hover {{
    background-color: {button};
    color: {text};
}}
QToolButton#applyButton[ready="true"] {{
    background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {accent_fill_hover}, stop:1 {accent_fill});
    border-color: {accent_fill};
    color: {on_accent};
    font-weight: 600;
}}
QToolButton#applyButton[ready="true"]:hover {{
    background-color: {accent_fill_hover};
}}
QToolButton#applyButton:disabled {{
    background: transparent;
    color: {text_dim};
    border-color: {border};
}}
QToolButton#stopButton:enabled:hover {{
    background-color: {error_soft};
}}
"""


def icon_path(name: str) -> str:
    """Forward-slash path to a bundled icon (for stylesheet url()), or ''."""
    path = resource_path("assets", "icons", name)
    return path.replace("\\", "/") if os.path.isfile(path) else ""


# ── Icons (Lucide, tinted to the theme) ───────────────────────────

_icon_cache: dict = {}
_bound_icons: list = []   # (setter, name, token) re-tinted on theme switch


def _tinted_pixmap(name: str, hexcolor: str, px: int) -> QPixmap:
    path = resource_path("assets", "icons", "lucide", name + ".svg")
    try:
        with open(path, encoding="utf-8") as f:
            svg = f.read().replace("currentColor", hexcolor)
    except OSError:
        return QPixmap()
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(pm)
    renderer.render(painter)
    painter.end()
    return pm


def icon(name: str, token: str = "text_soft") -> QIcon:
    """A Lucide icon tinted with a theme token (dimmed when disabled)."""
    key = (name, token, is_dark())
    cached = _icon_cache.get(key)
    if cached is None:
        cached = QIcon()
        for px in (32, 64):
            cached.addPixmap(_tinted_pixmap(name, c(token), px),
                             QIcon.Mode.Normal)
            cached.addPixmap(_tinted_pixmap(name, c("text_dim"), px),
                             QIcon.Mode.Disabled)
        _icon_cache[key] = cached
    return cached


def bind_icon(setter, name: str, token: str = "text_soft"):
    """Apply an icon now and again after every theme switch.

    ``setter`` takes a QIcon, e.g. ``action.setIcon`` or
    ``lambda ic: tabs.setTabIcon(0, ic)``.
    """
    setter(icon(name, token))
    _bound_icons.append((setter, name, token))


def _retint_icons():
    alive = []
    for setter, name, token in _bound_icons:
        try:
            setter(icon(name, token))
        except RuntimeError:    # the widget was deleted
            continue
        alive.append((setter, name, token))
    _bound_icons[:] = alive


def stylesheet() -> str:
    css = _STYLESHEET.format(**colors())
    check = icon_path("check-dark.svg" if is_dark() else "check-light.svg")
    if check:
        css += 'QCheckBox::indicator:checked { image: url("%s"); }\n' % check
    suffix = "dark" if is_dark() else "light"
    chevron = icon_path(f"chevron-down-{suffix}.svg")
    if chevron:
        css += ('QComboBox::down-arrow '
                '{ image: url("%s"); width: 10px; height: 10px; }\n' % chevron)
    for arrow in ("up", "down"):
        path = icon_path(f"chevron-{arrow}-{suffix}.svg")
        if path:
            css += ('QSpinBox::%s-arrow, QDoubleSpinBox::%s-arrow '
                    '{ image: url("%s"); width: 10px; height: 10px; }\n'
                    % (arrow, arrow, path))
    return css


def _apply_font(app: QApplication):
    """10pt UI font, Segoe UI Variable where Windows has it (set once)."""
    if app.property("_themeFont"):
        return
    font = app.font()
    for family in ("Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI"):
        if family in QFontDatabase.families():
            font.setFamily(family)
            break
    font.setPointSizeF(10)
    app.setFont(font)
    app.setProperty("_themeFont", True)


def apply(dark: bool, app: QApplication | None = None):
    """Switch the whole application to the dark or light theme."""
    _state["dark"] = bool(dark)
    app = app or QApplication.instance()
    if app is None:
        return
    _apply_font(app)
    app.setPalette(qpalette())
    app.setStyleSheet(stylesheet())
    _retint_icons()


def make_primary(button):
    """Style a QPushButton as the dialog's primary (accent) action."""
    button.setProperty("primary", True)
    button.style().unpolish(button)
    button.style().polish(button)
    return button
