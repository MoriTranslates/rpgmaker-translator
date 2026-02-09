"""Main application window — ties together all widgets."""

import os
import time

from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QToolBar, QStatusBar, QProgressBar,
    QFileDialog, QMessageBox, QLabel, QWidget, QVBoxLayout, QApplication,
    QProgressDialog,
)
from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QAction, QPalette, QColor

DARK_STYLESHEET = """
QMainWindow, QDialog, QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
}
QMenuBar, QToolBar {
    background-color: #181825;
    color: #cdd6f4;
    border-bottom: 1px solid #313244;
}
QMenuBar::item:selected, QToolBar QToolButton:hover {
    background-color: #313244;
}
QTreeWidget, QTableWidget, QPlainTextEdit, QLineEdit, QComboBox {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    selection-background-color: #45475a;
}
QTableWidget::item {
    padding: 4px;
}
QHeaderView::section {
    background-color: #1e1e2e;
    color: #cdd6f4;
    border: 1px solid #313244;
    padding: 4px;
}
QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    padding: 5px 15px;
    border-radius: 3px;
}
QPushButton:hover {
    background-color: #45475a;
}
QPushButton:pressed {
    background-color: #585b70;
}
QProgressBar {
    border: 1px solid #313244;
    background-color: #181825;
    text-align: center;
    color: #cdd6f4;
}
QProgressBar::chunk {
    background-color: #89b4fa;
}
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
}
QGroupBox {
    border: 1px solid #313244;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 16px;
    color: #cdd6f4;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
}
QTabWidget::pane {
    border: 1px solid #313244;
}
QTabBar::tab {
    background-color: #181825;
    color: #a6adc8;
    padding: 6px 16px;
    border: 1px solid #313244;
}
QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #cdd6f4;
}
QSplitter::handle {
    background-color: #313244;
}
QLabel {
    color: #cdd6f4;
}
"""

from ..ollama_client import OllamaClient
from ..rpgmaker_mv import RPGMakerMVParser
from ..project_model import TranslationProject
from ..translation_engine import TranslationEngine
from ..text_processor import PluginAnalyzer, TextProcessor
from .file_tree import FileTreeWidget
from .translation_table import TranslationTable
from .settings_dialog import SettingsDialog
from .actor_gender_dialog import ActorGenderDialog


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("RPG Maker Translator — Local LLM")
        self.setMinimumSize(1200, 700)

        # Core objects
        self.client = OllamaClient()
        self.parser = RPGMakerMVParser()
        self.project = TranslationProject()
        self.engine = TranslationEngine(self.client)
        self.plugin_analyzer = PluginAnalyzer()
        self.text_processor = TextProcessor(self.plugin_analyzer)
        self._dark_mode = True
        self._batch_start_time = 0
        self._batch_done_count = 0
        self._last_save_path = ""

        self._build_ui()
        self._build_toolbar()
        self._build_statusbar()
        self._connect_signals()

        # Apply dark mode by default
        self._apply_dark_mode()

        # Auto-save timer (every 2 minutes)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start(120_000)

    # ── UI Setup ───────────────────────────────────────────────────

    def _build_ui(self):
        """Build the main layout with splitter."""
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: file tree
        self.file_tree = FileTreeWidget()
        splitter.addWidget(self.file_tree)

        # Right: translation table
        self.trans_table = TranslationTable()
        splitter.addWidget(self.trans_table)

        splitter.setSizes([250, 950])
        self.setCentralWidget(splitter)

    def _build_toolbar(self):
        """Build the top toolbar with actions."""
        toolbar = QToolBar("Main Toolbar")
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Open project
        self.open_action = QAction("Open Project", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.triggered.connect(self._open_project)
        toolbar.addAction(self.open_action)

        # Save translation state
        self.save_action = QAction("Save State", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.triggered.connect(self._save_state)
        self.save_action.setEnabled(False)
        toolbar.addAction(self.save_action)

        # Load saved state
        self.load_action = QAction("Load State", self)
        self.load_action.setShortcut("Ctrl+L")
        self.load_action.triggered.connect(self._load_state)
        toolbar.addAction(self.load_action)

        toolbar.addSeparator()

        # Batch translate
        self.batch_action = QAction("Batch Translate", self)
        self.batch_action.setShortcut("Ctrl+T")
        self.batch_action.triggered.connect(self._batch_translate)
        self.batch_action.setEnabled(False)
        toolbar.addAction(self.batch_action)

        # Stop
        self.stop_action = QAction("Stop", self)
        self.stop_action.triggered.connect(self._stop_translation)
        self.stop_action.setEnabled(False)
        toolbar.addAction(self.stop_action)

        toolbar.addSeparator()

        # Export back to game
        self.export_action = QAction("Export to Game", self)
        self.export_action.setShortcut("Ctrl+E")
        self.export_action.triggered.connect(self._export_to_game)
        self.export_action.setEnabled(False)
        toolbar.addAction(self.export_action)

        toolbar.addSeparator()

        # Settings
        self.settings_action = QAction("Settings", self)
        self.settings_action.triggered.connect(self._open_settings)
        toolbar.addAction(self.settings_action)

        # Dark mode toggle
        self.dark_action = QAction("Light Mode", self)
        self.dark_action.triggered.connect(self._toggle_dark_mode)
        toolbar.addAction(self.dark_action)

        toolbar.addSeparator()

        # Apply word wrap
        self.wordwrap_action = QAction("Apply Word Wrap", self)
        self.wordwrap_action.triggered.connect(self._apply_wordwrap)
        self.wordwrap_action.setEnabled(False)
        toolbar.addAction(self.wordwrap_action)

        # Export to TXT
        self.txt_export_action = QAction("Export TXT", self)
        self.txt_export_action.triggered.connect(self._export_txt)
        self.txt_export_action.setEnabled(False)
        toolbar.addAction(self.txt_export_action)

    def _build_statusbar(self):
        """Build the bottom status bar with progress."""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(300)
        self.progress_bar.setVisible(False)
        self.statusbar.addPermanentWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.statusbar.addWidget(self.progress_label)

    def _connect_signals(self):
        """Wire up signals between components."""
        # File tree
        self.file_tree.file_selected.connect(self._filter_by_file)
        self.file_tree.all_selected.connect(self._show_all_entries)

        # Translation table
        self.trans_table.translate_requested.connect(self._translate_selected)
        self.trans_table.retranslate_correction.connect(self._retranslate_with_correction)
        self.trans_table.status_changed.connect(self._on_status_changed)

        # Engine
        self.engine.progress.connect(self._on_progress)
        self.engine.entry_done.connect(self._on_entry_done)
        self.engine.error.connect(self._on_error)
        self.engine.finished.connect(self._on_batch_finished)

    # ── Actions ────────────────────────────────────────────────────

    def _open_project(self):
        """Open an RPG Maker MV/MZ project folder."""
        path = QFileDialog.getExistingDirectory(
            self, "Select RPG Maker MV/MZ Project Folder"
        )
        if not path:
            return

        try:
            entries = self.parser.load_project(path)
        except FileNotFoundError as e:
            QMessageBox.warning(self, "Error", str(e))
            return

        self.project = TranslationProject(project_path=path, entries=entries)
        self.file_tree.load_project(self.project)
        self.trans_table.set_entries(entries)

        # Load actors for gender assignment
        actors_raw = self.parser.load_actors_raw(path)

        # Pre-translate game title + actor info so the user can read them
        translated_title = ""
        actor_translations = {}
        if actors_raw or any(e.id == "System.json/gameTitle" for e in entries):
            actor_translations, translated_title = self._pre_translate_info(
                entries, actors_raw
            )

        # Show gender assignment dialog with translated names
        if actors_raw:
            dlg = ActorGenderDialog(actors_raw, self, translations=actor_translations)
            if dlg.exec():
                genders = dlg.get_genders()
            else:
                # User skipped — use auto-detected genders
                genders = {a["id"]: a["auto_gender"] for a in actors_raw
                           if a["auto_gender"] != "unknown"}
            actor_ctx = self.parser.build_actor_context(actors_raw, genders)
            self.client.actor_context = actor_ctx
            self.project.actor_genders = genders
        else:
            self.client.actor_context = ""

        # Analyze plugins for word wrap settings
        self.plugin_analyzer.analyze_project(path)

        self.save_action.setEnabled(True)
        self.batch_action.setEnabled(True)
        self.export_action.setEnabled(True)
        self.txt_export_action.setEnabled(True)
        self.wordwrap_action.setEnabled(True)

        plugin_info = ""
        if self.plugin_analyzer.detected_plugins:
            plugin_info = f" | Plugins: {', '.join(self.plugin_analyzer.detected_plugins)}"
        self.statusbar.showMessage(
            f"Loaded {len(entries)} entries | "
            f"~{self.plugin_analyzer.chars_per_line} chars/line{plugin_info}", 8000
        )

        # Window title: show translated game title when available
        folder = os.path.basename(path)
        if translated_title:
            self.setWindowTitle(f"RPG Maker Translator \u2014 {translated_title} ({folder})")
        else:
            self.setWindowTitle(f"RPG Maker Translator \u2014 {folder}")

    def _pre_translate_info(self, entries, actors_raw):
        """Translate game title + actor names/profiles before the gender dialog.

        Returns:
            (actor_translations, translated_title) where actor_translations is
            {actor_id: {"name": ..., "nickname": ..., "profile": ...}}
        """
        # Build list of items to translate
        items = []  # (label, text) for progress display
        title_entry = None
        for e in entries:
            if e.id == "System.json/gameTitle":
                title_entry = e
                items.append(("Game title", e.original))
                break

        for actor in actors_raw:
            if actor.get("name"):
                items.append((f"Actor {actor['id']} name", actor["name"]))
            if actor.get("nickname"):
                items.append((f"Actor {actor['id']} nickname", actor["nickname"]))
            if actor.get("profile"):
                items.append((f"Actor {actor['id']} profile", actor["profile"]))

        if not items:
            return {}, ""

        # Check Ollama availability first
        if not self.client.is_available():
            return {}, ""

        progress = QProgressDialog(
            "Translating character info...", "Skip", 0, len(items), self
        )
        progress.setWindowTitle("Pre-translating")
        progress.setMinimumDuration(0)
        progress.setValue(0)

        translated_title = ""
        actor_translations = {}  # {actor_id: {"name":..., "nickname":..., "profile":...}}
        idx = 0

        # Translate game title
        if title_entry:
            progress.setLabelText(f"Translating game title...")
            QApplication.processEvents()
            if progress.wasCanceled():
                return actor_translations, translated_title
            result = self.client.translate_name(title_entry.original)
            if result and result != title_entry.original:
                translated_title = result
                title_entry.translation = result
                title_entry.status = "translated"
            idx += 1
            progress.setValue(idx)

        # Translate actor fields
        for actor in actors_raw:
            aid = actor["id"]
            if aid not in actor_translations:
                actor_translations[aid] = {}

            for field in ("name", "nickname", "profile"):
                text = actor.get(field, "")
                if not text:
                    continue
                progress.setLabelText(f"Translating Actor {aid} {field}...")
                QApplication.processEvents()
                if progress.wasCanceled():
                    return actor_translations, translated_title
                result = self.client.translate_name(text)
                if result and result != text:
                    actor_translations[aid][field] = result
                idx += 1
                progress.setValue(idx)

        progress.close()
        return actor_translations, translated_title

    def _save_state(self):
        """Save current translation state to a JSON file."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Translation State", "", "JSON Files (*.json)"
        )
        if path:
            self.project.save_state(path)
            self._last_save_path = path
            self.statusbar.showMessage(f"State saved to {path}", 3000)

    def _load_state(self):
        """Load a previously saved translation state."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Translation State", "", "JSON Files (*.json)"
        )
        if not path:
            return

        try:
            self.project = TranslationProject.load_state(path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to load state:\n{e}")
            return

        self.file_tree.load_project(self.project)
        self.trans_table.set_entries(self.project.entries)

        # Restore glossary
        self.client.glossary = self.project.glossary

        # Restore actor context from saved genders
        if self.project.actor_genders and self.project.project_path:
            actors_raw = self.parser.load_actors_raw(self.project.project_path)
            if actors_raw:
                self.client.actor_context = self.parser.build_actor_context(
                    actors_raw, self.project.actor_genders
                )

        self.save_action.setEnabled(True)
        self.batch_action.setEnabled(True)
        self.export_action.setEnabled(bool(self.project.project_path))

        self.statusbar.showMessage(
            f"Loaded state: {self.project.total} entries "
            f"({self.project.translated_count} translated)", 5000
        )
        name = os.path.basename(self.project.project_path) if self.project.project_path else "Restored"
        self.setWindowTitle(f"RPG Maker Translator — {name}")

    def _stop_translation(self):
        """Cancel the running batch translation."""
        self.engine.cancel()
        self.stop_action.setEnabled(False)

    def _export_to_game(self):
        """Write translations back to the game's JSON files."""
        if not self.project.project_path:
            QMessageBox.warning(self, "Error", "No project path set. Open a project first.")
            return

        translated = [e for e in self.project.entries if e.status in ("translated", "reviewed")]
        if not translated:
            QMessageBox.information(self, "Nothing to Export", "No translated entries to export.")
            return

        reply = QMessageBox.question(
            self, "Confirm Export",
            f"This will overwrite {len(set(e.file for e in translated))} file(s) "
            f"in:\n{self.project.project_path}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.parser.save_project(self.project.project_path, self.project.entries)
            QMessageBox.information(
                self, "Export Complete",
                f"Exported {len(translated)} translations to game files."
            )
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    def _open_settings(self):
        """Open the settings dialog."""
        dlg = SettingsDialog(self.client, self)
        if dlg.exec():
            # Sync glossary to project model
            self.project.glossary = self.client.glossary

    # ── Filtering ──────────────────────────────────────────────────

    def _filter_by_file(self, filename: str):
        """Show only entries from a specific file."""
        entries = self.project.get_entries_for_file(filename)
        self.trans_table.set_entries(entries)

    def _show_all_entries(self):
        """Show all entries."""
        self.trans_table.set_entries(self.project.entries)

    # ── Engine signal handlers ─────────────────────────────────────

    def _on_error(self, entry_id: str, error_msg: str):
        """Handle translation error for a single entry."""
        self.statusbar.showMessage(f"Error translating {entry_id}: {error_msg}", 5000)

    def _on_batch_finished(self):
        """Handle batch translation completing."""
        self.batch_action.setEnabled(True)
        self.stop_action.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("")
        self.file_tree.refresh_stats(self.project)
        self.statusbar.showMessage(
            f"Batch complete — {self.project.translated_count}/{self.project.total} translated",
            5000,
        )

    def _on_status_changed(self):
        """Handle status change from manual edits."""
        self.file_tree.refresh_stats(self.project)

    # ── Dark mode ──────────────────────────────────────────────────

    def _apply_dark_mode(self):
        """Apply or remove dark stylesheet."""
        app = QApplication.instance()
        if self._dark_mode:
            app.setStyleSheet(DARK_STYLESHEET)
            self.dark_action.setText("Light Mode")
        else:
            app.setStyleSheet("")
            self.dark_action.setText("Dark Mode")

    def _toggle_dark_mode(self):
        """Toggle between dark and light mode."""
        self._dark_mode = not self._dark_mode
        self._apply_dark_mode()

    # ── Auto-save ──────────────────────────────────────────────────

    def _autosave(self):
        """Auto-save project state if there are entries and a save path exists."""
        if not self.project.entries:
            return
        if not self._last_save_path:
            # Auto-save next to project if possible
            if self.project.project_path:
                self._last_save_path = os.path.join(
                    self.project.project_path, "_translation_autosave.json"
                )
            else:
                return
        try:
            self.project.save_state(self._last_save_path)
            self.statusbar.showMessage("Auto-saved", 2000)
        except Exception:
            pass  # Silent fail on autosave

    # ── Progress ETA ───────────────────────────────────────────────

    def _on_progress(self, current: int, total: int, text: str):
        """Update progress bar with ETA during batch translation."""
        self.progress_bar.setValue(current)
        self._batch_done_count = current

        # Calculate ETA
        eta_str = ""
        elapsed = time.time() - self._batch_start_time
        if current > 0 and elapsed > 0:
            rate = elapsed / current  # seconds per entry
            remaining = (total - current) * rate
            if remaining > 3600:
                eta_str = f" | ETA: {remaining/3600:.1f}h"
            elif remaining > 60:
                eta_str = f" | ETA: {remaining/60:.0f}m"
            else:
                eta_str = f" | ETA: {remaining:.0f}s"

        self.progress_label.setText(f"Translating {current}/{total}{eta_str}: {text}")

    # ── Translation memory ─────────────────────────────────────────

    def _batch_translate(self):
        """Start batch translating all untranslated entries."""
        if not self.client.is_available():
            QMessageBox.warning(
                self, "Ollama Not Available",
                "Cannot connect to Ollama. Make sure it's running:\n  ollama serve"
            )
            return

        # Translation memory: auto-fill exact duplicates from already-translated entries
        translated_map = {}
        for e in self.project.entries:
            if e.status in ("translated", "reviewed") and e.translation:
                translated_map[e.original] = e.translation

        tm_count = 0
        for e in self.project.entries:
            if e.status == "untranslated" and e.original in translated_map:
                e.translation = translated_map[e.original]
                e.status = "translated"
                self.trans_table.update_entry(e.id, e.translation)
                tm_count += 1

        if tm_count:
            self.file_tree.refresh_stats(self.project)
            self.statusbar.showMessage(
                f"Translation memory: filled {tm_count} duplicate(s)", 3000
            )

        untranslated = [e for e in self.project.entries if e.status == "untranslated"]
        if not untranslated:
            QMessageBox.information(self, "Done", "All entries are already translated!")
            return

        self.batch_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(untranslated))
        self.progress_bar.setValue(0)
        self._batch_start_time = time.time()
        self._batch_done_count = 0

        self.engine.translate_batch(self.project.entries)

    # ── Word Wrap ──────────────────────────────────────────────────

    def _apply_wordwrap(self):
        """Apply word wrapping to all translated entries based on plugin analysis."""
        if not self.project.entries:
            return

        summary = self.plugin_analyzer.get_summary()
        reply = QMessageBox.question(
            self, "Apply Word Wrap",
            f"Detected settings:\n\n{summary}\n\n"
            f"Apply word wrapping to all translated entries?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        count = self.text_processor.process_all(self.project.entries)
        # Refresh table
        self.trans_table.set_entries(
            self.trans_table._entries  # refresh current view
        )
        QMessageBox.information(
            self, "Word Wrap Applied",
            f"Modified {count} entries to fit ~{self.plugin_analyzer.chars_per_line} chars/line."
        )

    # ── Export TXT ─────────────────────────────────────────────────

    def _export_txt(self):
        """Export translations to a human-readable TXT patch file."""
        if not self.project.entries:
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Translation Patch", "", "Text Files (*.txt)"
        )
        if not path:
            return

        translated = [e for e in self.project.entries if e.status in ("translated", "reviewed")]
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# RPG Maker Translation Patch\n")
            f.write(f"# Project: {self.project.project_path}\n")
            f.write(f"# Entries: {len(translated)}\n")
            f.write(f"# Generated by RPG Maker Translator (Local LLM)\n\n")

            current_file = ""
            for entry in translated:
                if entry.file != current_file:
                    current_file = entry.file
                    f.write(f"\n{'='*60}\n")
                    f.write(f"# File: {current_file}\n")
                    f.write(f"{'='*60}\n\n")

                f.write(f"[{entry.id}]\n")
                f.write(f"  JP: {entry.original}\n")
                f.write(f"  EN: {entry.translation}\n\n")

        QMessageBox.information(
            self, "Export Complete",
            f"Exported {len(translated)} translations to:\n{path}"
        )

    # ── Re-translate with diff ─────────────────────────────────────

    def _translate_selected(self, entry_ids: list):
        """Translate specific selected entries (allows re-translation)."""
        if not self.client.is_available():
            QMessageBox.warning(
                self, "Ollama Not Available",
                "Cannot connect to Ollama. Make sure it's running:\n  ollama serve"
            )
            return

        entries = [self.project.get_entry_by_id(eid) for eid in entry_ids]
        entries = [e for e in entries if e is not None]
        if not entries:
            return

        # Store old translations for diff display
        self._old_translations = {e.id: e.translation for e in entries if e.translation}

        # Force re-translate by temporarily marking as untranslated
        for e in entries:
            if e.status in ("translated", "reviewed"):
                e.status = "untranslated"

        self.batch_action.setEnabled(False)
        self.stop_action.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(entries))
        self.progress_bar.setValue(0)
        self._batch_start_time = time.time()
        self._batch_done_count = 0

        self.engine.translate_batch(entries)

    def _on_entry_done(self, entry_id: str, translation: str):
        """Handle a single entry translation completing, with diff info."""
        entry = self.project.get_entry_by_id(entry_id)
        if entry:
            # Check for diff with previous translation
            old = getattr(self, '_old_translations', {}).get(entry_id, "")
            if old and old != translation:
                self.statusbar.showMessage(
                    f"Re-translated: was \"{old[:40]}...\" -> now \"{translation[:40]}...\"",
                    5000,
                )
            entry.translation = translation
            entry.status = "translated"
        self.trans_table.update_entry(entry_id, translation)
        self.file_tree.refresh_stats(self.project)

    # ── Retranslate single entry with correction ──────────────────

    def _retranslate_with_correction(self, entry_id: str, correction: str):
        """Retranslate a single entry with user's correction hint."""
        if not self.client.is_available():
            QMessageBox.warning(
                self, "Ollama Not Available",
                "Cannot connect to Ollama. Make sure it's running:\n  ollama serve"
            )
            return

        entry = self.project.get_entry_by_id(entry_id)
        if not entry:
            return

        old_translation = entry.translation
        self.statusbar.showMessage(f"Retranslating with correction: {correction}...")

        # Run in a background thread to avoid freezing the UI
        from PyQt6.QtCore import QThread, QObject, pyqtSignal as Signal

        class _RetranslateWorker(QObject):
            done = Signal(str)
            failed = Signal(str)

            def __init__(self, client, text, context, correction, old_trans):
                super().__init__()
                self.client = client
                self.text = text
                self.context = context
                self.correction = correction
                self.old_trans = old_trans

            def run(self):
                try:
                    result = self.client.translate(
                        text=self.text,
                        context=self.context,
                        correction=self.correction,
                        old_translation=self.old_trans,
                    )
                    self.done.emit(result)
                except Exception as e:
                    self.failed.emit(str(e))

        thread = QThread(self)
        worker = _RetranslateWorker(
            self.client, entry.original, entry.context, correction, old_translation
        )
        worker.moveToThread(thread)

        def on_done(new_translation):
            entry.translation = new_translation
            entry.status = "translated"
            self.trans_table.update_entry(entry_id, new_translation)
            self.file_tree.refresh_stats(self.project)
            if old_translation and old_translation != new_translation:
                self.statusbar.showMessage(
                    f"Corrected: \"{old_translation[:40]}\" -> \"{new_translation[:40]}\"", 8000
                )
            else:
                self.statusbar.showMessage("Retranslation complete", 3000)
            thread.quit()

        def on_failed(err):
            self.statusbar.showMessage(f"Retranslation failed: {err}", 5000)
            thread.quit()

        def on_thread_finished():
            thread.deleteLater()

        worker.done.connect(on_done)
        worker.failed.connect(on_failed)
        thread.started.connect(worker.run)
        thread.finished.connect(on_thread_finished)
        thread.start()

        # Keep references alive until thread completes
        self._correction_thread = thread
        self._correction_worker = worker
