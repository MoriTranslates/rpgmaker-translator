"""Central theme: Catppuccin palettes, the app stylesheet and shared status visuals.

Every widget pulls colors from here instead of hard-coding hex values, so
dark (Mocha) and light (Latte) mode stay consistent.  ``apply()`` is called
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

# Semantic tokens → Catppuccin Mocha (dark)
DARK = {
    "window":       "#1e1e2e",  # base
    "panel":        "#181825",  # mantle — menubar/toolbar/statusbar
    "field":        "#181825",  # inputs, tables, trees
    "field_alt":    "#1f1f30",  # alternate rows
    "raised":       "#232336",  # cards, menus, popups
    "border":       "#2c2d40",  # hairlines between surfaces
    "border_strong": "#45475a",  # surface1 — input outlines, splitters
    "control_border": "#7f849c",  # checkbox/radio outlines (3:1 on field)
    "button":       "#313244",
    "button_hover": "#45475a",
    "button_pressed": "#585b70",
    "selection":    "#313b5c",  # accent-tinted
    "selection_text": "#cdd6f4",
    "text":         "#cdd6f4",
    "text_soft":    "#bac2de",  # subtext1
    "text_muted":   "#a6adc8",  # subtext0
    "text_dim":     "#8a90aa",  # 4.5:1+ on window/panel
    "accent":       "#89b4fa",  # blue
    "accent_hover": "#74c7ec",  # sapphire
    "on_accent":    "#1e1e2e",
    "accent_alt":   "#89dceb",  # sky
    "accent_soft":  "#283250",  # accent wash for the current step
    "error_soft":   "#3a2233",
    "ok":           "#a6e3a1",  # green
    "warn":         "#f9e2af",  # yellow
    "caution":      "#fab387",  # peach
    "error":        "#f38ba8",  # red
    "special":      "#cba6f7",  # mauve
    "tooltip":      "#313244",
    # Row tints by entry status — kept faint; the status stripe carries meaning
    "row_untranslated": "#241b2b",
    "row_translated":   "#24222b",
    "row_reviewed":     "#1b2a26",
    "row_skipped":      "#1e1e2c",
}

# Semantic tokens → Catppuccin Latte (light)
LIGHT = {
    "window":       "#eff1f5",
    "panel":        "#e6e9ef",
    "field":        "#ffffff",
    "field_alt":    "#f6f7fa",
    "raised":       "#ffffff",
    "border":       "#dce0e8",
    "border_strong": "#bcc0cc",
    "control_border": "#8c8fa1",
    "button":       "#e6e9ef",
    "button_hover": "#dce0e8",
    "button_pressed": "#ccd0da",
    "selection":    "#c5d5f7",
    "selection_text": "#1e2030",
    "text":         "#4c4f69",
    "text_soft":    "#4c4f69",
    "text_muted":   "#5c5f77",
    "text_dim":     "#6c6f85",  # 4.5:1+ on white
    "accent":       "#1e66f5",
    "accent_hover": "#3b7af7",
    "on_accent":    "#ffffff",
    "accent_alt":   "#209fb5",
    "accent_soft":  "#dbe6fd",
    "error_soft":   "#fbe3e8",
    "ok":           "#2e7d1f",
    "warn":         "#8f5400",  # darker than Latte yellow — readable on white
    "caution":      "#fe640b",
    "error":        "#d20f39",
    "special":      "#8839ef",
    "tooltip":      "#ffffff",
    "row_untranslated": "#fdf1f3",
    "row_translated":   "#fdf8ec",
    "row_reviewed":     "#eff8ec",
    "row_skipped":      "#f4f5f8",
}

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
    "untranslated": "error",
    "translated":   "warn",
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
    border-bottom: 1px solid {border};
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
    background-color: {accent};
    color: {on_accent};
    border: 1px solid {accent};
    font-weight: 600;
}}
QPushButton[primary="true"]:hover {{
    background-color: {accent_hover};
    border-color: {accent_hover};
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
    border-bottom: 2px solid {accent};
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
    background-color: {border_strong};
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
QScrollArea {{
    border: none;
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
QToolButton[step="active"] {{
    background-color: {accent_soft};
    color: {accent};
    font-weight: 600;
}}
QToolButton[step="done"] {{
    color: {text};
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
    border-color: {error};
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
