"""Event Viewer panel — tree-based event browser for reviewing full dialogue flows."""

import re
from collections import OrderedDict

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget,
    QTreeWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QPushButton, QAbstractItemView, QTextEdit, QGroupBox,
)
from PyQt6.QtCore import Qt, pyqtSignal as Signal, QEvent
from PyQt6.QtGui import QColor, QFont

from ..utils import event_prefix, extract_event_context
from .spell_checker import SpellHighlighter, build_spell_menu_actions
from . import theme

# Files that contain events (not database flat entries)
_EVENT_FILES = {"CommonEvents.json", "Troops.json"}
_DB_FILES = {
    "Actors.json", "Classes.json", "Items.json", "Weapons.json",
    "Armors.json", "Skills.json", "States.json", "Enemies.json",
    "System.json", "plugins.js",
    # Wolf RPG databases
    "Database/DataBase", "Database/CDataBase", "Database/SysDatabase",
    # RPG Maker 2000/2003
    "RPG_RT.ldb",
}

# Fields that are not part of event dialogue flow
_SKIP_FIELDS = {"speaker_name", "displayName"}

# Status icons — shared with the translation table so both views match
_STATUS_ICONS = theme.STATUS_ICONS

_MAP_RE = re.compile(r'^Map\d+\.json$', re.IGNORECASE)


class EventViewerPanel(QWidget):
    """Tree-based event browser for reviewing full dialogue flows."""

    status_changed = Signal()     # emitted when entries are modified
    entry_updated = Signal(str)   # emitted with entry_id after inline edit

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_entries = []
        self._event_groups: dict[str, list] = OrderedDict()
        self._current_prefix = ""
        self._current_entries = []
        self._speaker_lookup = {}
        self._dark_mode = True
        self._tree_items: dict[str, QTreeWidgetItem] = {}  # prefix -> tree item
        self._build_ui()

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: event tree
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["Event", "Reviewed"])
        th = self._tree.header()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tree.setMinimumWidth(200)
        self._tree.setIndentation(16)
        self._tree.currentItemChanged.connect(self._on_tree_selection_changed)
        splitter.addWidget(self._tree)

        # Right: detail panel
        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(4, 4, 4, 4)

        # Header row: event name + mark reviewed button
        header = QHBoxLayout()
        self._event_label = QLabel("Select an event from the tree")
        self._event_label.setStyleSheet(theme.heading_css())
        header.addWidget(self._event_label, 1)

        self._review_btn = QPushButton("Mark Event Reviewed")
        self._review_btn.setToolTip(
            "Mark every translated line in this event as reviewed")
        self._review_btn.setEnabled(False)
        self._review_btn.clicked.connect(self._mark_event_reviewed)
        header.addWidget(self._review_btn)
        detail_layout.addLayout(header)

        # Detail table
        self._detail_table = QTableWidget()
        self._detail_table.setColumnCount(4)
        self._detail_table.setHorizontalHeaderLabels(
            ["", "Speaker", "Original (JP)", "Translation (EN)"])

        # Column sizing
        h = self._detail_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._detail_table.setColumnWidth(0, 30)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self._detail_table.setColumnWidth(1, 120)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)

        # Behavior — read-only table, editing via panel below
        self._detail_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._detail_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._detail_table.setWordWrap(True)
        self._detail_table.verticalHeader().setVisible(False)
        self._detail_table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._detail_table.setAlternatingRowColors(False)
        self._detail_table.currentCellChanged.connect(self._on_row_selected)

        # Vertical splitter: table (top 70%) + editor (bottom 30%)
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.addWidget(self._detail_table)

        # Editor panel
        editor_widget = QWidget()
        editor_layout = QHBoxLayout(editor_widget)
        editor_layout.setContentsMargins(0, 2, 0, 0)

        orig_box = QGroupBox("Original (JP)")
        orig_inner = QVBoxLayout(orig_box)
        self._orig_editor = QTextEdit()
        self._orig_editor.setReadOnly(True)
        self._orig_editor.setAcceptRichText(False)
        orig_inner.addWidget(self._orig_editor)
        editor_layout.addWidget(orig_box)

        trans_box = QGroupBox("Translation (EN) — editable")
        trans_inner = QVBoxLayout(trans_box)
        self._trans_editor = QTextEdit()
        self._trans_editor.setAcceptRichText(False)
        self._trans_editor.textChanged.connect(self._on_editor_changed)
        self._trans_editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._trans_editor.customContextMenuRequested.connect(self._show_trans_context_menu)
        self._spell = SpellHighlighter(self._trans_editor.document())
        trans_inner.addWidget(self._trans_editor)
        editor_layout.addWidget(trans_box)

        v_splitter.addWidget(editor_widget)
        v_splitter.setSizes([500, 200])

        detail_layout.addWidget(v_splitter)

        self._hint_label = QLabel(
            "↑/↓ move between lines  ·  Space toggles reviewed  "
            "·  edits in the Translation box save automatically")
        self._hint_label.setStyleSheet(theme.hint_css())
        detail_layout.addWidget(self._hint_label)

        splitter.addWidget(detail_widget)
        splitter.setSizes([300, 900])
        layout.addWidget(splitter)

        self._selected_row = -1

        # Spacebar = mark reviewed when table has focus
        self._detail_table.installEventFilter(self)

    def eventFilter(self, obj, event):
        """Intercept spacebar on detail table to toggle reviewed status."""
        if (obj is self._detail_table
                and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Space):
            self._toggle_row_reviewed()
            return True
        return super().eventFilter(obj, event)

    def _toggle_row_reviewed(self):
        """Toggle reviewed status on current row (spacebar shortcut)."""
        row = self._detail_table.currentRow()
        if row < 0 or row >= len(self._current_entries):
            return
        entry = self._current_entries[row]
        if entry.status == "reviewed":
            # Un-review: go back to translated (or untranslated if empty)
            entry.status = "translated" if entry.translation else "untranslated"
        elif (entry.translation or "").strip():
            entry.status = "reviewed"
        else:
            return  # Nothing to review — keep it untranslated
        # Update table
        self._detail_table.blockSignals(True)
        status_item = self._detail_table.item(row, 0)
        if status_item:
            status_item.setText(_STATUS_ICONS.get(entry.status, ""))
        self._apply_row_colors(row, entry)
        self._detail_table.blockSignals(False)
        self._refresh_tree_stats()
        self.status_changed.emit()

    # ── Public API ───────────────────────────────────────────────────

    def set_entries(self, entries: list):
        """Load entries and rebuild the event tree."""
        self._all_entries = entries
        self._current_prefix = ""
        self._current_entries = []
        self._build_speaker_lookup()
        self._build_event_groups()
        self._populate_tree()
        self._detail_table.setRowCount(0)
        self._event_label.setText("Select an event from the tree")
        self._review_btn.setEnabled(False)

    def update_entry(self, entry_id: str, translation: str):
        """Update a single entry's display after batch translation."""
        prefix = event_prefix(entry_id)
        if prefix != self._current_prefix:
            return  # Not currently displayed
        for row, entry in enumerate(self._current_entries):
            if entry.id == entry_id:
                self._detail_table.blockSignals(True)
                # Status icon
                status_item = self._detail_table.item(row, 0)
                if status_item:
                    status_item.setText(_STATUS_ICONS.get(entry.status, ""))
                # Translation text
                trans_item = self._detail_table.item(row, 3)
                if trans_item:
                    trans_item.setText(translation)
                self._apply_row_colors(row, entry)
                self._detail_table.blockSignals(False)
                # Update editor panel if this row is selected
                if row == self._selected_row:
                    self._trans_editor.blockSignals(True)
                    self._trans_editor.setPlainText(translation)
                    self._trans_editor.blockSignals(False)
                break

    def refresh_current_event(self):
        """Refresh the current event detail in place (after external edits).

        Updates cells without rebuilding the table, and only touches the
        editor when its text actually differs and the user isn't typing
        in it — so the cursor never jumps while editing.
        """
        entries = self._current_entries
        if (self._current_prefix
                and self._detail_table.rowCount() == len(entries)):
            self._detail_table.blockSignals(True)
            for row, entry in enumerate(entries):
                status_item = self._detail_table.item(row, 0)
                if status_item:
                    status_item.setText(_STATUS_ICONS.get(entry.status, ""))
                trans_item = self._detail_table.item(row, 3)
                text = entry.translation or ""
                if trans_item and trans_item.text() != text:
                    trans_item.setText(text)
                self._apply_row_colors(row, entry)
            self._detail_table.blockSignals(False)
            self._update_event_label()
            row = self._selected_row
            if (0 <= row < len(entries)
                    and not self._trans_editor.hasFocus()):
                text = entries[row].translation or ""
                if self._trans_editor.toPlainText() != text:
                    self._trans_editor.blockSignals(True)
                    self._trans_editor.setPlainText(text)
                    self._trans_editor.blockSignals(False)
        elif self._current_prefix:
            self._show_event(self._current_prefix)
        self._refresh_tree_stats()

    def refresh_stats(self):
        """Refresh tree progress badges (called at batch checkpoints)."""
        self._refresh_tree_stats()

    def set_dark_mode(self, enabled: bool):
        """Toggle dark/light mode (colors come from the shared theme)."""
        self._dark_mode = enabled
        self._hint_label.setStyleSheet(theme.hint_css())
        self._refresh_tree_stats()
        # Re-color the current event in place (keeps the selected row)
        if self._current_prefix:
            self._detail_table.blockSignals(True)
            for row, entry in enumerate(self._current_entries):
                self._apply_row_colors(row, entry)
            self._detail_table.blockSignals(False)

    # ── Internal: build data structures ──────────────────────────────

    def _build_speaker_lookup(self):
        """Build JP->EN speaker name lookup from speaker_name and actor entries."""
        self._speaker_lookup = {}
        for e in self._all_entries:
            if e.field == "speaker_name" and e.translation:
                self._speaker_lookup[e.original.strip()] = e.translation.strip()
            elif e.field == "name" and e.file == "Actors.json" and e.translation:
                self._speaker_lookup[e.original.strip()] = e.translation.strip()

    def _is_event_entry(self, entry) -> bool:
        """Return True if this entry belongs to an in-game event."""
        if entry.file in _DB_FILES:
            return False
        if entry.field in _SKIP_FIELDS:
            return False
        # Must have at least 3 ID segments: file/event/field
        if len(entry.id.split("/")) < 3:
            return False
        return True

    def _build_event_groups(self):
        """Group entries by event prefix."""
        self._event_groups = OrderedDict()
        for e in self._all_entries:
            if not self._is_event_entry(e):
                continue
            prefix = event_prefix(e.id)
            if not prefix:
                continue
            if prefix not in self._event_groups:
                self._event_groups[prefix] = []
            self._event_groups[prefix].append(e)

    # ── Internal: tree population ────────────────────────────────────

    def _populate_tree(self):
        """Build the event tree from grouped entries."""
        self._tree.clear()
        self._tree_items = {}

        # Categorize prefixes
        ce_prefixes = []
        troop_prefixes = []
        map_prefixes: dict[str, list] = {}  # filename -> [prefixes]

        for prefix in self._event_groups:
            filename = prefix.split("/")[0]
            if filename == "CommonEvents.json":
                ce_prefixes.append(prefix)
            elif filename == "Troops.json":
                troop_prefixes.append(prefix)
            elif _MAP_RE.match(filename):
                map_prefixes.setdefault(filename, []).append(prefix)

        # Common Events category
        if ce_prefixes:
            ce_root = QTreeWidgetItem(self._tree, ["Common Events", ""])
            ce_root.setFlags(
                Qt.ItemFlag.ItemIsEnabled)
            for prefix in ce_prefixes:
                entries = self._event_groups[prefix]
                display = extract_event_context(entries[0].id)
                reviewed = sum(1 for e in entries if e.status == "reviewed")
                total = len(entries)
                progress = f"{reviewed}/{total}"

                item = QTreeWidgetItem(ce_root, [display, progress])
                item.setData(0, Qt.ItemDataRole.UserRole, prefix)
                self._apply_tree_item_color(item, reviewed, total)
                self._tree_items[prefix] = item

        # Maps category
        if map_prefixes:
            maps_root = QTreeWidgetItem(self._tree, ["Maps", ""])
            maps_root.setFlags(
                Qt.ItemFlag.ItemIsEnabled)
            for filename in sorted(map_prefixes.keys()):
                prefixes = map_prefixes[filename]
                map_name = filename.replace(".json", "")

                if len(prefixes) == 1:
                    # Single event in map — show directly under Maps
                    prefix = prefixes[0]
                    entries = self._event_groups[prefix]
                    display = extract_event_context(entries[0].id)
                    reviewed = sum(1 for e in entries if e.status == "reviewed")
                    total = len(entries)

                    item = QTreeWidgetItem(
                        maps_root,
                        [f"{map_name}/{display}", f"{reviewed}/{total}"])
                    item.setData(0, Qt.ItemDataRole.UserRole, prefix)
                    self._apply_tree_item_color(item, reviewed, total)
                    self._tree_items[prefix] = item
                else:
                    # Multiple events — group under map file
                    map_node = QTreeWidgetItem(maps_root, [map_name, ""])
                    map_node.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    for prefix in prefixes:
                        entries = self._event_groups[prefix]
                        display = extract_event_context(entries[0].id)
                        reviewed = sum(
                            1 for e in entries if e.status == "reviewed")
                        total = len(entries)

                        item = QTreeWidgetItem(
                            map_node, [display, f"{reviewed}/{total}"])
                        item.setData(0, Qt.ItemDataRole.UserRole, prefix)
                        self._apply_tree_item_color(item, reviewed, total)
                        self._tree_items[prefix] = item

        # Troops category
        if troop_prefixes:
            troops_root = QTreeWidgetItem(self._tree, ["Troops", ""])
            troops_root.setFlags(
                Qt.ItemFlag.ItemIsEnabled)
            for prefix in troop_prefixes:
                entries = self._event_groups[prefix]
                display = extract_event_context(entries[0].id)
                reviewed = sum(1 for e in entries if e.status == "reviewed")
                total = len(entries)

                item = QTreeWidgetItem(troops_root, [display, f"{reviewed}/{total}"])
                item.setData(0, Qt.ItemDataRole.UserRole, prefix)
                self._apply_tree_item_color(item, reviewed, total)
                self._tree_items[prefix] = item

    def _apply_tree_item_color(self, item: QTreeWidgetItem,
                                reviewed: int, total: int):
        """Color tree item based on review progress."""
        if total == 0:
            return
        if reviewed == total:
            name_fg = badge_fg = theme.qcolor("ok")
        elif reviewed > 0:
            name_fg = badge_fg = theme.qcolor("warn")
        else:
            name_fg, badge_fg = theme.qcolor("text"), theme.qcolor("text_dim")
        item.setForeground(0, name_fg)
        item.setForeground(1, badge_fg)
        item.setToolTip(1, f"{reviewed} of {total} lines reviewed")

    def _refresh_tree_stats(self):
        """Update progress badges on all tree items."""
        for prefix, item in self._tree_items.items():
            entries = self._event_groups.get(prefix, [])
            reviewed = sum(1 for e in entries if e.status == "reviewed")
            total = len(entries)
            item.setText(1, f"{reviewed}/{total}")
            self._apply_tree_item_color(item, reviewed, total)

    # ── Internal: event detail display ───────────────────────────────

    def _on_tree_selection_changed(self, current: QTreeWidgetItem,
                                    previous: QTreeWidgetItem):
        """Handle tree selection change (click or arrow keys)."""
        if not current:
            return
        prefix = current.data(0, Qt.ItemDataRole.UserRole)
        if prefix:
            self._show_event(prefix)

    def _show_event(self, prefix: str):
        """Populate the detail table with all entries from an event."""
        entries = self._event_groups.get(prefix, [])
        if not entries:
            return

        self._current_prefix = prefix
        self._current_entries = entries

        # Update header
        display = extract_event_context(entries[0].id)
        filename = prefix.split("/")[0].replace(".json", "")
        reviewed = sum(1 for e in entries if e.status == "reviewed")
        total = len(entries)
        self._event_label.setText(
            f"{filename} \u2014 {display}  ({reviewed}/{total} reviewed)")
        self._review_btn.setEnabled(True)

        # Populate table
        self._detail_table.blockSignals(True)
        self._detail_table.setRowCount(len(entries))

        for row, e in enumerate(entries):
            # Status icon
            icon = _STATUS_ICONS.get(e.status, "")
            status_item = QTableWidgetItem(icon)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            status_item.setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            status_item.setData(Qt.ItemDataRole.UserRole, e.id)
            self._detail_table.setItem(row, 0, status_item)

            # Speaker
            speaker_jp = ""
            if e.context:
                for line in e.context.split("\n"):
                    if line.startswith("[Speaker:"):
                        speaker_jp = line.strip("[]").replace("Speaker: ", "")
                        break
            speaker_en = self._speaker_lookup.get(
                speaker_jp, "") if speaker_jp else ""
            speaker_display = speaker_en or speaker_jp

            spk_item = QTableWidgetItem(speaker_display)
            spk_item.setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            if speaker_jp and speaker_en and speaker_en != speaker_jp:
                spk_item.setToolTip(f"JP: {speaker_jp}")
            self._detail_table.setItem(row, 1, spk_item)

            # Original (read-only)
            orig_item = QTableWidgetItem(e.original)
            orig_item.setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self._detail_table.setItem(row, 2, orig_item)

            # Translation (read-only in table, edit via panel below)
            trans_item = QTableWidgetItem(e.translation or "")
            trans_item.setFlags(
                Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self._detail_table.setItem(row, 3, trans_item)

            self._apply_row_colors(row, e)

        self._detail_table.blockSignals(False)

        # Auto-select first row so editors populate.  setCurrentCell is a
        # no-op when already at (0, 0), so sync the editors explicitly.
        if entries:
            self._detail_table.setCurrentCell(0, 0)
            self._on_row_selected(0, 0, -1, -1)

    def _update_event_label(self):
        """Refresh the 'N/M reviewed' header for the current event."""
        if not self._current_entries:
            return
        total = len(self._current_entries)
        reviewed = sum(1 for e in self._current_entries
                       if e.status == "reviewed")
        display = extract_event_context(self._current_entries[0].id)
        filename = self._current_prefix.split("/")[0].replace(".json", "")
        self._event_label.setText(
            f"{filename} \u2014 {display}  ({reviewed}/{total} reviewed)")

    def _apply_row_colors(self, row: int, entry):
        """Apply theme colors to a detail table row."""
        bg = theme.qcolor("field") if row % 2 == 0 else theme.qcolor("field_alt")

        if entry.status == "untranslated":
            fg = theme.qcolor("error")
        elif entry.status == "reviewed":
            fg = theme.qcolor("ok")
        else:
            fg = theme.qcolor("text_soft")

        for col in range(4):
            item = self._detail_table.item(row, col)
            if item:
                item.setBackground(bg)
                item.setForeground(fg)

        # Speaker accent color
        spk_item = self._detail_table.item(row, 1)
        if spk_item and spk_item.text():
            spk_item.setForeground(theme.qcolor("accent"))

        # Status icon color + text (status never relies on color alone)
        status_item = self._detail_table.item(row, 0)
        if status_item:
            status_item.setForeground(theme.status_fg(entry.status))
            status_item.setToolTip(theme.STATUS_LABELS.get(entry.status, ""))

    # ── Internal: editing & review ───────────────────────────────────

    def _on_row_selected(self, row: int, col: int, prev_row: int, prev_col: int):
        """Update editor panel when a row is selected."""
        if row < 0 or row >= len(self._current_entries):
            self._trans_editor.blockSignals(True)
            self._orig_editor.clear()
            self._trans_editor.clear()
            self._trans_editor.blockSignals(False)
            self._selected_row = -1
            return

        self._selected_row = row
        entry = self._current_entries[row]
        self._orig_editor.setPlainText(entry.original)
        self._trans_editor.blockSignals(True)
        self._trans_editor.setPlainText(entry.translation or "")
        self._trans_editor.blockSignals(False)

    def _show_trans_context_menu(self, pos):
        """Right-click on translation editor — spell suggestions + standard menu."""
        menu = self._trans_editor.createStandardContextMenu()
        build_spell_menu_actions(self._spell, self._trans_editor, menu, pos)
        menu.exec(self._trans_editor.mapToGlobal(pos))

    def _on_editor_changed(self):
        """Sync translation edits from editor panel back to entry + table."""
        row = self._selected_row
        if row < 0 or row >= len(self._current_entries):
            return

        entry = self._current_entries[row]
        new_text = self._trans_editor.toPlainText()
        entry.translation = new_text
        if not new_text.strip():
            entry.status = "untranslated"
        elif entry.status not in ("translated", "reviewed"):
            entry.status = "translated"

        # Update table row
        self._detail_table.blockSignals(True)
        status_item = self._detail_table.item(row, 0)
        if status_item:
            status_item.setText(_STATUS_ICONS.get(entry.status, ""))
        trans_item = self._detail_table.item(row, 3)
        if trans_item:
            trans_item.setText(new_text)
        self._apply_row_colors(row, entry)
        self._detail_table.blockSignals(False)

        self.status_changed.emit()

    def _mark_event_reviewed(self):
        """Mark all entries in the current event as reviewed."""
        if not self._current_entries:
            return

        self._detail_table.blockSignals(True)
        for row, entry in enumerate(self._current_entries):
            # Only promote entries that actually have a translation
            if (entry.status in ("translated", "untranslated")
                    and (entry.translation or "").strip()):
                entry.status = "reviewed"
            # Update icon
            status_item = self._detail_table.item(row, 0)
            if status_item:
                status_item.setText(_STATUS_ICONS.get(entry.status, ""))
            self._apply_row_colors(row, entry)
        self._detail_table.blockSignals(False)

        self._update_event_label()

        # Update tree badge
        self._refresh_tree_stats()

        self.status_changed.emit()
