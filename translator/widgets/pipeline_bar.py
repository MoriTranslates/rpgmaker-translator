"""Pipeline bar — the workspace's one action bar.

Each workflow step is a button (done = tick, running = accent, upcoming =
plain) that runs that step; one primary button carries the next pending
step — "Next: …", or "Apply to Game" once only the export is left — and
Stop sits on the right.  Colors live in theme.py and are picked by the
``step`` / ``ready`` dynamic properties.
"""

from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QLabel, QPushButton, QToolButton,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal

from . import theme


_STEP_ICONS = {
    "db": "database",
    "dialogue": "message-square-text",
    "cleanup": "sparkles",
    "wordwrap": "wrap-text",
    "export": "gamepad-2",
}

_STATE_TIPS = {"done": "Done", "active": "In progress", "next": "Up next",
               "pending": "Not started"}


class _StepButton(QToolButton):
    """A single step: icon + name, styled by state, click runs the step."""

    def __init__(self, number: int, key: str, name: str, parent=None):
        super().__init__(parent)
        self.number = number
        self.key = key
        self.name = name
        self._state = "pending"  # pending | next | active | done
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(16, 16))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setText(name)
        self._refresh()

    def _refresh(self):
        if self._state == "done":
            self.setIcon(theme.icon("check", "ok"))
        elif self._state == "active":
            self.setIcon(theme.icon(_STEP_ICONS.get(self.key, "circle"), "accent"))
        elif self._state == "next":
            self.setIcon(theme.icon(_STEP_ICONS.get(self.key, "circle"), "text_soft"))
        else:
            self.setIcon(theme.icon(_STEP_ICONS.get(self.key, "circle"), "text_dim"))
        self.setToolTip(f"Step {self.number}: {self.name} — "
                        f"{_STATE_TIPS[self._state]}. Click to run it.")
        self.setProperty("step", self._state)
        self.style().unpolish(self)
        self.style().polish(self)

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, value: str):
        self._state = value
        self._refresh()


# Steps definitions per engine type: (key, display_name)
RPGMAKER_STEPS = [
    ("db", "Translate DB"),
    ("dialogue", "Translate Dialogue"),
    ("cleanup", "Clean Up"),
    ("wordwrap", "Word Wrap"),
    ("export", "Export"),
]

TYRANOSCRIPT_STEPS = [
    ("dialogue", "Translate"),
    ("cleanup", "Clean Up"),
    ("wordwrap", "Word Wrap"),
    ("export", "Export"),
]

SRPGSTUDIO_STEPS = [
    ("dialogue", "Translate"),
    ("cleanup", "Clean Up"),
    ("wordwrap", "Word Wrap"),
    ("export", "Export"),
]

RPGMAKER2K_STEPS = [
    ("db", "Translate DB"),
    ("dialogue", "Translate Dialogue"),
    ("cleanup", "Clean Up"),
    ("export", "Export"),
]

# Default for backward compat
PIPELINE_STEPS = RPGMAKER_STEPS


class PipelineBar(QWidget):
    """Step buttons + Next Step nudge + Stop, in one bar."""

    step_requested = pyqtSignal(str)  # emits step key when a step is clicked

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("pipelineBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._steps = RPGMAKER_STEPS
        self._step_labels: dict[str, _StepButton] = {}
        self._arrow_labels: list[QLabel] = []
        self._current_index = -1  # no step active
        self.stop_button = None
        self._build_ui()
        self.setVisible(False)  # hidden until project loaded

    def _build_ui(self):
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 6, 10, 6)
        self._layout.setSpacing(2)

        self._rebuild_steps()

        self._layout.addSpacing(12)

        # Next Step nudge
        self.next_btn = QPushButton("Next Step")
        theme.make_primary(self.next_btn)
        self.next_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.next_btn.setToolTip("Run the next step of the translation pipeline")
        self.next_btn.clicked.connect(self._on_next_clicked)
        self.next_btn.setVisible(False)
        self._layout.addWidget(self.next_btn)

        # Apply-to-game shortcut (wired to the window's action later)
        self.apply_button = None

        self._layout.addStretch()

        # Status hint
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("hint")
        self._layout.addWidget(self.hint_label)
        self.apply_theme()

    def set_stop_action(self, action):
        """Show the window's Stop action at the right end of the bar."""
        self.stop_button = QToolButton()
        self.stop_button.setObjectName("stopButton")
        self.stop_button.setDefaultAction(action)
        self.stop_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.stop_button.setIconSize(QSize(16, 16))
        self._layout.addSpacing(8)
        self._layout.addWidget(self.stop_button)

    def set_apply_action(self, action):
        """Show "Apply to Game" right after the Next button.

        Mirrors the window's Apply Translation to Game action (enabled state,
        tooltip) but keeps a short label so the bar fits small screens.
        """
        btn = QToolButton()
        btn.setObjectName("applyButton")
        btn.setText("Apply to Game")
        btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        btn.setIconSize(QSize(16, 16))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(action.trigger)

        def sync():
            btn.setEnabled(action.isEnabled())
            btn.setToolTip(action.toolTip())
        action.changed.connect(sync)
        sync()
        self.apply_button = btn
        self._layout.insertSpacing(self._layout.indexOf(self.next_btn) + 1, 6)
        self._layout.insertWidget(self._layout.indexOf(self.next_btn) + 2, btn)
        self.apply_theme()

    def _rebuild_steps(self):
        """Create step buttons for the current step list."""
        for label in self._step_labels.values():
            self._layout.removeWidget(label)
            label.deleteLater()
        for arrow in self._arrow_labels:
            self._layout.removeWidget(arrow)
            arrow.deleteLater()
        self._step_labels.clear()
        self._arrow_labels.clear()

        insert_pos = 0
        for i, (key, name) in enumerate(self._steps):
            button = _StepButton(i + 1, key, name)
            button.clicked.connect(lambda _=False, k=key: self.step_requested.emit(k))
            self._step_labels[key] = button
            self._layout.insertWidget(insert_pos, button)
            insert_pos += 1
            if i < len(self._steps) - 1:
                arrow = QLabel()
                arrow.setPixmap(theme.icon("chevron-right", "text_dim").pixmap(14, 14))
                arrow.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._arrow_labels.append(arrow)
                self._layout.insertWidget(insert_pos, arrow)
                insert_pos += 1

    def apply_theme(self):
        """Re-tint icons after a dark/light switch (colors come from QSS)."""
        for label in self._step_labels.values():
            label._refresh()
        for arrow in self._arrow_labels:
            arrow.setPixmap(theme.icon("chevron-right", "text_dim").pixmap(14, 14))
        self.next_btn.setIcon(theme.icon("arrow-right", "on_accent"))
        self._style_apply()

    def _style_apply(self):
        """Apply to Game is quiet until it is the next thing to do."""
        btn = self.apply_button
        if btn is None:
            return
        ready = bool(btn.property("ready"))
        btn.setIcon(theme.icon("gamepad-2", "on_accent" if ready else "text_soft"))
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    def _set_apply_ready(self, ready: bool):
        if (self.apply_button is not None
                and bool(self.apply_button.property("ready")) != ready):
            self.apply_button.setProperty("ready", ready)
            self._style_apply()

    def set_engine(self, engine_key_or_steps):
        """Switch pipeline steps for the given engine.

        Args:
            engine_key_or_steps: Either an engine key string (legacy) or
                a list of (key, label) tuples from handler.pipeline_steps.
        """
        if isinstance(engine_key_or_steps, list):
            new_steps = engine_key_or_steps
        elif engine_key_or_steps == "tyranoscript":
            new_steps = TYRANOSCRIPT_STEPS
        elif engine_key_or_steps == "srpgstudio":
            new_steps = SRPGSTUDIO_STEPS
        elif engine_key_or_steps == "rpgmaker_2k":
            new_steps = RPGMAKER2K_STEPS
        else:
            new_steps = RPGMAKER_STEPS
        if new_steps != self._steps:
            self._steps = new_steps
            self._rebuild_steps()

    def reset(self):
        """Reset all steps to pending (call when opening a new project)."""
        self._current_index = -1
        for label in self._step_labels.values():
            label.state = "pending"
        self.next_btn.setVisible(False)
        self._set_apply_ready(False)
        self.hint_label.setText("")
        self.setVisible(True)
        self._nudge_next()

    def mark_active(self, step_key: str):
        """Mark a step as currently running."""
        if step_key not in self._step_labels:
            return
        for key, label in self._step_labels.items():
            if label.state in ("active", "next"):
                label.state = "pending"
        self._step_labels[step_key].state = "active"
        idx = [k for k, _ in self._steps].index(step_key)
        self._current_index = idx
        self.next_btn.setVisible(False)
        self._set_apply_ready(False)
        self.hint_label.setText(f"{self._steps[idx][1]} in progress…")

    def mark_done(self, step_key: str):
        """Mark a step as completed and show Next Step nudge."""
        if step_key not in self._step_labels:
            return
        self._step_labels[step_key].state = "done"
        self._nudge_next()

    def mark_done_up_to(self, step_key: str):
        """Mark all steps up to and including step_key as done."""
        keys = [k for k, _ in self._steps]
        if step_key not in keys:
            return
        target = keys.index(step_key)
        for i, (key, _) in enumerate(self._steps):
            if i <= target:
                self._step_labels[key].state = "done"
        self._nudge_next()

    def _nudge_next(self):
        """Find next pending step and show the nudge button."""
        for label in self._step_labels.values():
            if label.state == "next":
                label.state = "pending"
        for i, (key, name) in enumerate(self._steps):
            if self._step_labels[key].state == "pending":
                self._step_labels[key].state = "next"
                self._current_index = i
                # Only the export is left: Apply to Game is the one action
                last = key == "export" and self.apply_button is not None
                self.next_btn.setText(f"Next: {name}")
                self.next_btn.setVisible(not last)
                self._set_apply_ready(last)
                self.hint_label.setText("")
                return
        self.next_btn.setVisible(False)
        self._set_apply_ready(False)
        self.hint_label.setText("All steps complete!")

    def _on_next_clicked(self):
        """Emit the step key for the next pending step."""
        for key, _ in self._steps:
            if self._step_labels[key].state in ("next", "pending"):
                self.step_requested.emit(key)
                return
