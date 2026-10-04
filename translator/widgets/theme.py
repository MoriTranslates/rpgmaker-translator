"""Central theme: Catppuccin palettes, the app stylesheet and shared status visuals.

Every widget pulls colors from here instead of hard-coding hex values, so
dark (Mocha) and light (Latte) mode stay consistent.  ``apply()`` is called
by the main window at startup and whenever the user toggles dark mode;
widgets that paint per-item colors re-read ``qcolor()`` when they refresh.
"""

import os

from PyQt6.QtGui import QColor, QPalette
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
    "border":       "#313244",  # surface0
    "border_strong": "#45475a",  # surface1
    "button":       "#313244",
    "button_hover": "#45475a",
    "button_pressed": "#585b70",
    "selection":    "#45475a",
    "selection_text": "#cdd6f4",
    "text":         "#cdd6f4",
    "text_soft":    "#bac2de",  # subtext1
    "text_muted":   "#a6adc8",  # subtext0
    "text_dim":     "#6c7086",  # overlay0
    "accent":       "#89b4fa",  # blue
    "accent_hover": "#74c7ec",  # sapphire
    "on_accent":    "#1e1e2e",
    "accent_alt":   "#89dceb",  # sky
    "ok":           "#a6e3a1",  # green
    "warn":         "#f9e2af",  # yellow
    "caution":      "#fab387",  # peach
    "error":        "#f38ba8",  # red
    "special":      "#cba6f7",  # mauve
    "tooltip":      "#313244",
    # Translation table row tints, by entry status
    "row_untranslated": "#47283a",
    "row_translated":   "#433f2b",
    "row_reviewed":     "#24432f",
    "row_skipped":      "#2a2b3a",
}

# Semantic tokens → Catppuccin Latte (light)
LIGHT = {
    "window":       "#eff1f5",
    "panel":        "#e6e9ef",
    "field":        "#ffffff",
    "field_alt":    "#f6f7fa",
    "border":       "#ccd0da",
    "border_strong": "#acb0be",
    "button":       "#e6e9ef",
    "button_hover": "#dce0e8",
    "button_pressed": "#ccd0da",
    "selection":    "#c5d5f7",
    "selection_text": "#1e2030",
    "text":         "#4c4f69",
    "text_soft":    "#4c4f69",
    "text_muted":   "#6c6f85",
    "text_dim":     "#9ca0b0",
    "accent":       "#1e66f5",
    "accent_hover": "#3b7af7",
    "on_accent":    "#ffffff",
    "accent_alt":   "#209fb5",
    "ok":           "#40a02b",
    "warn":         "#c47a10",  # darker than Latte yellow — readable on white
    "caution":      "#fe640b",
    "error":        "#d20f39",
    "special":      "#8839ef",
    "tooltip":      "#ffffff",
    "row_untranslated": "#fce4e8",
    "row_translated":   "#fbf1d6",
    "row_reviewed":     "#e0f2da",
    "row_skipped":      "#eceef3",
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

STATUS_ICONS = {
    "untranslated": "○",  # ○
    "translated":   "◐",  # ◐
    "reviewed":     "●",  # ●
    "skipped":      "—",  # —
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
    return "font-weight: bold; font-size: 11pt;"


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
QMenuBar, QToolBar {{
    background-color: {panel};
    color: {text};
    border-bottom: 1px solid {border};
}}
QMenuBar::item {{
    padding: 4px 10px;
    background: transparent;
}}
QMenuBar::item:selected, QToolBar QToolButton:hover {{
    background-color: {border};
}}
QToolBar {{
    spacing: 2px;
    padding: 2px 4px;
}}
QToolBar QToolButton {{
    background: transparent;
    color: {text};
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px 8px;
}}
QToolBar QToolButton:pressed {{
    background-color: {button_pressed};
}}
QToolBar QToolButton:disabled {{
    color: {text_dim};
}}
QToolBar QToolButton#stopButton:enabled {{
    color: {error};
}}
QToolBar::separator {{
    background-color: {border};
    width: 1px;
    margin: 4px 6px;
}}
QMenu {{
    background-color: {window};
    color: {text};
    border: 1px solid {border};
    padding: 4px 0;
}}
QMenu::item {{
    padding: 5px 28px 5px 24px;
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
    margin: 4px 8px;
}}
QToolTip {{
    background-color: {tooltip};
    color: {text};
    border: 1px solid {border_strong};
    padding: 4px 6px;
}}
QTreeWidget, QTreeView, QTableWidget, QTableView, QListWidget, QListView,
QPlainTextEdit, QTextEdit, QLineEdit, QComboBox {{
    background-color: {field};
    color: {text};
    border: 1px solid {border};
    selection-background-color: {selection};
    selection-color: {selection_text};
}}
QLineEdit, QComboBox {{
    padding: 2px 4px;
    border-radius: 3px;
}}
QLineEdit:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {accent};
}}
QLineEdit:disabled, QComboBox:disabled {{
    color: {text_dim};
}}
QComboBox QAbstractItemView {{
    background-color: {field};
    color: {text};
    border: 1px solid {border_strong};
    selection-background-color: {selection};
    selection-color: {selection_text};
}}
QTableView, QTableWidget {{
    gridline-color: {border};
}}
QTableWidget::item, QTableView::item {{
    padding: 4px;
}}
QTreeView::item, QTreeWidget::item {{
    padding: 2px 2px;
}}
QTreeView::item:selected, QTreeWidget::item:selected,
QTableView::item:selected, QTableWidget::item:selected,
QListView::item:selected, QListWidget::item:selected {{
    background-color: {selection};
    color: {selection_text};
}}
QHeaderView::section {{
    background-color: {panel};
    color: {text_muted};
    border: none;
    border-right: 1px solid {border};
    border-bottom: 1px solid {border};
    padding: 4px 6px;
    font-weight: bold;
}}
QHeaderView {{
    background-color: {panel};
}}
QTableCornerButton::section {{
    background-color: {panel};
    border: none;
}}
QPushButton {{
    background-color: {button};
    color: {text};
    border: 1px solid {border_strong};
    padding: 5px 15px;
    border-radius: 4px;
}}
QPushButton:hover {{
    background-color: {button_hover};
}}
QPushButton:pressed {{
    background-color: {button_pressed};
}}
QPushButton:disabled {{
    color: {text_dim};
    border-color: {border};
}}
QPushButton:default {{
    border: 1px solid {accent};
}}
QPushButton[primary="true"] {{
    background-color: {accent};
    color: {on_accent};
    border: 1px solid {accent};
    font-weight: bold;
}}
QPushButton[primary="true"]:hover {{
    background-color: {accent_hover};
    border-color: {accent_hover};
}}
QPushButton[primary="true"]:disabled {{
    background-color: {button};
    color: {text_dim};
    border-color: {border};
}}
QProgressBar {{
    border: 1px solid {border};
    border-radius: 3px;
    background-color: {field};
    text-align: center;
    color: {text};
}}
QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 2px;
}}
QStatusBar {{
    background-color: {panel};
    color: {text_muted};
    border-top: 1px solid {border};
}}
QStatusBar QLabel {{
    color: {text_muted};
}}
QGroupBox {{
    border: 1px solid {border};
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 16px;
    color: {text};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {text_muted};
}}
QTabWidget::pane {{
    border: 1px solid {border};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {panel};
    color: {text_muted};
    padding: 6px 16px;
    border: 1px solid {border};
    border-bottom: none;
    margin-right: 1px;
}}
QTabBar::tab:hover {{
    color: {text};
}}
QTabBar::tab:selected {{
    background-color: {window};
    color: {text};
    border-top: 2px solid {accent};
}}
QSplitter::handle {{
    background-color: {border};
}}
QSplitter::handle:horizontal {{
    width: 3px;
}}
QSplitter::handle:vertical {{
    height: 3px;
}}
QSplitter::handle:hover {{
    background-color: {accent};
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
QCheckBox, QRadioButton {{
    color: {text};
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {border_strong};
    border-radius: 3px;
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
    width: 14px;
    height: 14px;
    border: 1px solid {border_strong};
    border-radius: 8px;
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
"""


def icon_path(name: str) -> str:
    """Forward-slash path to a bundled icon (for stylesheet url()), or ''."""
    path = resource_path("assets", "icons", name)
    return path.replace("\\", "/") if os.path.isfile(path) else ""


def stylesheet() -> str:
    css = _STYLESHEET.format(**colors())
    check = icon_path("check-dark.svg" if is_dark() else "check-light.svg")
    if check:
        css += 'QCheckBox::indicator:checked { image: url("%s"); }\n' % check
    return css


def apply(dark: bool, app: QApplication | None = None):
    """Switch the whole application to the dark or light theme."""
    _state["dark"] = bool(dark)
    app = app or QApplication.instance()
    if app is None:
        return
    app.setPalette(qpalette())
    app.setStyleSheet(stylesheet())


def make_primary(button):
    """Style a QPushButton as the dialog's primary (accent) action."""
    button.setProperty("primary", True)
    button.style().unpolish(button)
    button.style().polish(button)
    return button
