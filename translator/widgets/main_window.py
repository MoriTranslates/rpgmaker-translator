"""Main application window — ties together all widgets."""

import json
import logging
import os
import re
import shutil
import subprocess
import time
from collections import Counter

log = logging.getLogger(__name__)

from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QToolBar, QStatusBar, QProgressBar,
    QFileDialog, QMessageBox, QLabel, QWidget, QVBoxLayout, QHBoxLayout,
    QApplication, QProgressDialog, QMenu, QInputDialog, QDialog, QTabWidget,
    QCheckBox, QDialogButtonBox,
)
from PyQt6.QtCore import Qt, QSize, QTimer, QByteArray
from PyQt6.QtGui import QAction, QPalette, QColor, QKeySequence, QShortcut

from ..version import __version__
from ..resource_paths import app_dir
from ..ai_client import AIClient, build_system_prompt
from ..rpgmaker_mv import RPGMakerMVParser
from ..tyranoscript import TyranoScriptParser
from ..srpgstudio import SRPGStudioParser
from ..rpgmaker_ace import RPGMakerAceParser
from ..rpgmaker_2k import RPGMaker2KParser
from ..renpy import RenPyParser
from ..wolfrpg import WolfRPGParser
from ..crowd import CrowdParser
from ..kirikiri import KirikiriParser
from ..csv_game import CSVGameParser
from ..engine_handler import (
    EngineHandler, detect_engine, get_handler_by_key,
    RPGMakerMVHandler, RPGMakerMZHandler, RPGMakerAceHandler,
    RPGMaker2KHandler, TyranoScriptHandler, SRPGStudioHandler,
    RenPyHandler, WolfRPGHandler, CrowdHandler, KirikiriHandler,
    CSVGameHandler,
)
from ..project_model import TranslationProject
from ..translation_engine import TranslationEngine
from ..text_processor import PluginAnalyzer, TextProcessor
from .file_tree import FileTreeWidget
from .translation_table import TranslationTable
from .settings_dialog import SettingsDialog
from .glossary_dialog import GlossaryDialog
from .actor_gender_dialog import ActorGenderDialog
from .variant_dialog import VariantDialog
from .image_panel import ImagePanel
from .gpu_monitor import GPUMonitorPanel
from .queue_panel import QueuePanel
from .event_viewer import EventViewerPanel
from .model_suggestion_dialog import ModelSuggestionDialog
from .pipeline_bar import PipelineBar
from .background_task import run_in_thread, running_count, wait_all
from . import theme
from .welcome_page import WelcomePage


class MainWindow(QMainWindow):
    """Main application window."""

    # Map displayNames are keyed by Map###.json — matched dynamically
    _AUTO_GLOSSARY_MAP_FIELD = "displayName"
    # Fields where each word should be capitalized (names, titles, places)
    _CAPITALIZE_FIELDS = {"name", "nickname", "displayName", "speaker_name"}
    # Words to leave lowercase in title case (prepositions, articles, conjunctions)
    _TITLE_SMALL_WORDS = {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "and",
        "or", "but", "nor", "by", "with", "from", "as", "is", "vs",
    }

    _APP_TITLE = f"RPG Maker Translator v{__version__}"

    # Settings file lives next to main.py (or next to the .exe when frozen)
    _SETTINGS_FILE = os.path.join(app_dir(), "_settings.json")

    def __init__(self):
        super().__init__()
        self.setWindowTitle(self._APP_TITLE)
        icon_file = theme.icon_path("app.svg")
        if icon_file:
            from PyQt6.QtGui import QIcon
            QApplication.setWindowIcon(QIcon(icon_file))  # dialogs inherit it
        # Small enough for a 1366x768 laptop at 125% scaling; the default
        # size (and any remembered geometry) is applied in _restore_window_state
        self.setMinimumSize(960, 600)
        self.resize(1400, 900)

        # Core objects
        self.client = AIClient()
        self.parser = RPGMakerMVParser()
        self.tyrano_parser = TyranoScriptParser()
        self.srpg_parser = SRPGStudioParser()
        self.ace_parser = RPGMakerAceParser()
        self.rm2k_parser = RPGMaker2KParser()
        self.renpy_parser = RenPyParser()
        self.wolf_parser = WolfRPGParser()
        self.crowd_parser = CrowdParser()
        self.kirikiri_parser = KirikiriParser()
        self.csv_parser = CSVGameParser()
        # Engine handler registry — maps key -> handler with parser
        self._engine_handlers = {
            "rpgmaker_mv": RPGMakerMVHandler(self.parser),
            "rpgmaker_mz": RPGMakerMZHandler(self.parser),
            "rpgmaker_ace": RPGMakerAceHandler(self.ace_parser),
            "rpgmaker_2k": RPGMaker2KHandler(self.rm2k_parser),
            "tyranoscript": TyranoScriptHandler(self.tyrano_parser),
            "srpgstudio": SRPGStudioHandler(self.srpg_parser),
            "renpy": RenPyHandler(self.renpy_parser),
            "wolfrpg": WolfRPGHandler(self.wolf_parser),
            "crowd": CrowdHandler(self.crowd_parser),
            "kirikiri": KirikiriHandler(self.kirikiri_parser),
            "csv_game": CSVGameHandler(self.csv_parser),
        }
        self.handler: EngineHandler = self._engine_handlers["rpgmaker_mv"]
        self._engine_overrides: dict[str, dict] = {}  # per-engine setting overrides
        self.project = TranslationProject()
        self._project_type = "rpgmaker_mv"
        self.engine = TranslationEngine(self.client)
        self.plugin_analyzer = PluginAnalyzer()
        self.text_processor = TextProcessor(self.plugin_analyzer)
        self._dark_mode = True
        self._game_font = "Consolas"
        self._export_review_file = False
        self._disable_splash = True
        self._show_translation_splash = True
        self._actors_ready = False  # True after actor gender dialog has been shown/skipped
        self._batch_start_time = 0
        self._batch_done_count = 0
        self._dupe_fill_count = 0
        self._batch_dupe_map = {}  # original_text -> [duplicate entries]
        self._batch_all_chained = False
        self._wizard_active = False
        self._last_save_path = ""
        self._general_glossary = {}  # persists across all projects
        # Batch run bookkeeping
        self._run_kind = "batch"        # "batch" | "selected" | "polish"
        self._current_batch_mode = "all"
        self._batch_project = None      # project the running batch belongs to
        self._user_stopped = False      # Stop pressed — no auto-retranslate/chaining
        self._is_cleanup_retranslation = False
        self._server_down_dialog_open = False
        self._finished_during_server_down = False
        self._old_translations = {}
        self._vocab_genders = {}
        self._busy = False              # re-entrancy guard (actor pre-translate etc.)
        self._closing = False
        # Global (non per-engine) model / word wrap — what gets persisted
        self._global_model = self.client.model
        self._global_wordwrap = 0

        # Restore persistent settings before building UI
        self._load_settings()

        self._build_ui()
        self._build_menubar()
        self._build_toolbar()
        self._build_statusbar()
        self._build_welcome()
        self._polish_actions()
        self._connect_signals()
        self._restore_window_state()
        self.pipeline_bar.step_requested.connect(self._on_pipeline_step)

        # Apply dark mode by default
        self._apply_dark_mode()

        # Ollama is started on-demand (wizard or manual translate), not at launch

        # Auto-save timer (every 2 minutes)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start(120_000)

        # First-launch model suggestion (deferred to after window shows)
        if not os.path.exists(self._SETTINGS_FILE) and not self.client.is_cloud:
            QTimer.singleShot(500, self._show_model_suggestion)

    # ── UI Setup ───────────────────────────────────────────────────

    def _build_ui(self):
        """Build the main layout with tabs: Text Translation | Image Translation."""
        self.tabs = QTabWidget()

        # Tab 1: Text Translation (existing layout)
        text_tab = QSplitter(Qt.Orientation.Horizontal)

        # Left column: file tree + GPU monitor
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        self.file_tree = FileTreeWidget()
        left_layout.addWidget(self.file_tree, 1)
        self.gpu_monitor = GPUMonitorPanel()
        left_layout.addWidget(self.gpu_monitor)

        text_tab.addWidget(left_panel)
        self.trans_table = TranslationTable()
        text_tab.addWidget(self.trans_table)
        text_tab.setStretchFactor(0, 0)
        text_tab.setStretchFactor(1, 1)
        text_tab.setCollapsible(1, False)
        text_tab.setSizes([260, 1140])
        self.text_splitter = text_tab
        self.tabs.addTab(text_tab, "Text Translation")

        # Tab 2: Image Translation
        self.image_panel = ImagePanel()
        self.tabs.addTab(self.image_panel, "Image Translation (Experimental)")

        # Tab 3: Event Viewer
        self.event_viewer = EventViewerPanel()
        self.tabs.addTab(self.event_viewer, "Event Viewer")

        # Tab 4: Translation Queue
        self.queue_panel = QueuePanel()
        self.tabs.addTab(self.queue_panel, "Translation Queue")

        # Pipeline progress bar (below toolbar, above tabs)
        self.pipeline_bar = PipelineBar()

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.pipeline_bar)
        # Welcome page (no project) and the workspace tabs share one slot
        from PyQt6.QtWidgets import QStackedWidget
        self._stack = QStackedWidget()
        self._stack.addWidget(self.tabs)
        central_layout.addWidget(self._stack, 1)
        self.setCentralWidget(central)

    def _build_welcome(self):
        """Start page shown until a project is opened (needs menu actions)."""
        self._welcome = WelcomePage(self.open_action, self.load_action)
        self._stack.addWidget(self._welcome)
        self._show_workspace(False)

    def _show_workspace(self, show: bool):
        """Switch between the welcome page and the project workspace."""
        self._stack.setCurrentWidget(self.tabs if show else self._welcome)

    def _polish_actions(self):
        """Make menu help discoverable: tooltips on hover + status-bar tips."""
        for menu in self.menuBar().findChildren(QMenu):
            menu.setToolTipsVisible(True)
        for action in self.findChildren(QAction):
            tip = action.toolTip()
            # Qt's default tooltip is just the text — only real help text counts
            if tip and tip.replace("&&", "&") != action.text().replace("&&", "&"):
                action.setStatusTip(tip.split("\n")[0])

    # ── Window geometry persistence ────────────────────────────────

    def _restore_window_state(self):
        """Restore window size/position and splitter layout from settings."""
        ui = getattr(self, "_ui_state", None) or {}
        geo = ui.get("geometry")
        if not geo:
            # First launch: 1400x900, but never larger than the screen
            screen = self.screen() or QApplication.primaryScreen()
            if screen is not None:
                avail = screen.availableGeometry()
                w = min(1400, int(avail.width() * 0.92))
                h = min(900, int(avail.height() * 0.92))
                self.resize(max(w, self.minimumWidth()),
                            max(h, self.minimumHeight()))
                frame = self.frameGeometry()
                frame.moveCenter(avail.center())
                self.move(frame.topLeft())
        try:
            if geo:
                self.restoreGeometry(QByteArray.fromBase64(geo.encode("ascii")))
            split = ui.get("text_splitter")
            if split:
                self.text_splitter.restoreState(
                    QByteArray.fromBase64(split.encode("ascii")))
        except (AttributeError, ValueError, TypeError):
            pass  # Corrupt/foreign value — keep the defaults

    def _capture_window_state(self) -> dict:
        def b64(data) -> str:
            return bytes(data.toBase64()).decode("ascii")
        return {
            "geometry": b64(self.saveGeometry()),
            "text_splitter": b64(self.text_splitter.saveState()),
        }

    def _build_menubar(self):
        """Build the menu bar with organized menus."""
        menubar = self.menuBar()

        # ── Project menu ──────────────────────────────────────────
        project_menu = menubar.addMenu("Project")

        self.open_action = QAction("Open Project\u2026", self)
        self.open_action.setShortcut("Ctrl+O")
        self.open_action.setToolTip("Open a game folder (engine is detected automatically)")
        self.open_action.triggered.connect(self._open_project)
        project_menu.addAction(self.open_action)

        self.save_action = QAction("Save State", self)
        self.save_action.setShortcut("Ctrl+S")
        self.save_action.setToolTip("Save translation progress to _translation_state.json")
        self.save_action.triggered.connect(self._save_state)
        self.save_action.setEnabled(False)
        project_menu.addAction(self.save_action)

        self.save_as_action = QAction("Save State As\u2026", self)
        self.save_as_action.setShortcut("Ctrl+Shift+S")
        self.save_as_action.triggered.connect(self._save_state_as)
        self.save_as_action.setEnabled(False)
        project_menu.addAction(self.save_as_action)

        self.load_action = QAction("Load State\u2026", self)
        self.load_action.setShortcut("Ctrl+L")
        self.load_action.setToolTip("Resume from a saved translation state file")
        self.load_action.triggered.connect(self._load_state)
        project_menu.addAction(self.load_action)

        project_menu.addSeparator()

        self.close_action = QAction("Close Project", self)
        self.close_action.setShortcut("Ctrl+W")
        self.close_action.setToolTip("Close the current project")
        self.close_action.triggered.connect(self._close_project)
        self.close_action.setEnabled(False)
        project_menu.addAction(self.close_action)

        project_menu.addSeparator()

        self.rename_action = QAction("Rename Folder\u2026", self)
        self.rename_action.setToolTip("Rename the game folder to \"English Title - WIP\"")
        self.rename_action.triggered.connect(self._rename_folder)
        self.rename_action.setEnabled(False)
        project_menu.addAction(self.rename_action)

        # Import submenu
        import_menu = project_menu.addMenu("Import")

        self.import_action = QAction("From Save State\u2026", self)
        self.import_action.setToolTip(
            "Import translations from an older version's save state"
        )
        self.import_action.triggered.connect(self._import_translations)
        self.import_action.setEnabled(False)
        import_menu.addAction(self.import_action)

        self.import_folder_action = QAction("From Game Folder\u2026", self)
        self.import_folder_action.setToolTip(
            "Import translations from an already-translated game folder"
        )
        self.import_folder_action.triggered.connect(self._import_from_game_folder)
        self.import_folder_action.setEnabled(False)
        import_menu.addAction(self.import_folder_action)

        self.scan_plugin_edits_action = QAction("Plugin Parameters\u2026", self)
        self.scan_plugin_edits_action.setToolTip(
            "Compare two plugins.js files and import translated parameters"
        )
        self.scan_plugin_edits_action.triggered.connect(
            self._scan_plugin_edits)
        self.scan_plugin_edits_action.setEnabled(False)
        import_menu.addAction(self.scan_plugin_edits_action)

        # ── Translate menu ────────────────────────────────────────
        translate_menu = menubar.addMenu("Translate")

        # Pipeline steps (match pipeline bar order)
        self.batch_db_action = QAction("1. Translate DB (Names && Terms)", self)
        self.batch_db_action.setShortcut("Ctrl+D")
        self.batch_db_action.setToolTip(
            "Stage 1: Translate database names, descriptions, and system terms. "
            "QA these before translating dialogue."
        )
        self.batch_db_action.triggered.connect(self._batch_translate_db)
        self.batch_db_action.setEnabled(False)
        translate_menu.addAction(self.batch_db_action)

        self.batch_dialogue_action = QAction("2. Translate Dialogue", self)
        self.batch_dialogue_action.setShortcut("Ctrl+T")
        self.batch_dialogue_action.setToolTip(
            "Stage 2: Translate dialogue, events, and plugin text. "
            "Translated DB names are used as glossary terms."
        )
        self.batch_dialogue_action.triggered.connect(self._batch_translate_dialogue)
        self.batch_dialogue_action.setEnabled(False)
        translate_menu.addAction(self.batch_dialogue_action)

        self.cleanup_action = QAction("3. Clean Up Translations", self)
        self.cleanup_action.setShortcut("Ctrl+Shift+U")
        self.cleanup_action.setToolTip(
            "Fix word-per-line, placeholder leaks, spacing, capitalization,\n"
            "missing spaces after \\n[N], collapsed color codes, quotes, contractions"
        )
        self.cleanup_action.triggered.connect(self._cleanup_translations)
        self.cleanup_action.setEnabled(False)
        translate_menu.addAction(self.cleanup_action)

        self.wordwrap_action = QAction("4. Wrap Text to Lines\u2026", self)
        self.wordwrap_action.setToolTip(
            "Redistribute translated text across lines to fit message window width"
        )
        self.wordwrap_action.triggered.connect(self._apply_wordwrap)
        self.wordwrap_action.setEnabled(False)
        translate_menu.addAction(self.wordwrap_action)

        translate_menu.addSeparator()

        self.stop_action = QAction("Stop", self)
        self.stop_action.setToolTip(
            "Stop the running translation after the current requests finish. "
            "Completed entries are kept.")
        self.stop_action.triggered.connect(self._stop_translation)
        self.stop_action.setEnabled(False)
        translate_menu.addAction(self.stop_action)

        translate_menu.addSeparator()

        self.find_replace_action = QAction("Find && Replace\u2026", self)
        self.find_replace_action.setShortcut("Ctrl+H")
        self.find_replace_action.setToolTip("Find and replace text in translations")
        self.find_replace_action.triggered.connect(self.trans_table.show_replace_bar)
        self.find_replace_action.setEnabled(False)
        translate_menu.addAction(self.find_replace_action)

        # Advanced submenu — power-user batch modes and fixes
        advanced_menu = translate_menu.addMenu("Advanced")

        self.batch_action = QAction("Batch All (DB + Dialogue)", self)
        self.batch_action.setShortcut("Ctrl+Shift+T")
        self.batch_action.setToolTip("Translate everything at once (DB → glossary → dialogue)")
        self.batch_action.triggered.connect(self._batch_translate)
        self.batch_action.setEnabled(False)
        advanced_menu.addAction(self.batch_action)

        self.batch_actor_action = QAction("Batch by Actor\u2026", self)
        self.batch_actor_action.setShortcut("Ctrl+Shift+A")
        self.batch_actor_action.setToolTip(
            "Translate dialogue grouped by speaker — female speakers first, "
            "then male, then ungendered. Gives the LLM strong gender context."
        )
        self.batch_actor_action.triggered.connect(self._batch_translate_by_actor)
        self.batch_actor_action.setEnabled(False)
        advanced_menu.addAction(self.batch_actor_action)

        advanced_menu.addSeparator()

        self.strip_actor_codes_action = QAction("Strip Duplicate Actor Codes", self)
        self.strip_actor_codes_action.setToolTip(
            "Remove leading \\n[N] from translations where speaker/namebox\n"
            "already shows the name (prevents double name display in-game)"
        )
        self.strip_actor_codes_action.triggered.connect(self._strip_duplicate_actor_codes)
        self.strip_actor_codes_action.setEnabled(False)
        advanced_menu.addAction(self.strip_actor_codes_action)

        self.polish_action = QAction("Polish Grammar", self)
        self.polish_action.setToolTip(
            "Run all translations through the LLM for grammar and fluency cleanup"
        )
        self.polish_action.triggered.connect(self._polish_translations)
        self.polish_action.setEnabled(False)
        advanced_menu.addAction(self.polish_action)

        self.consistency_action = QAction("Consistency Pass", self)
        self.consistency_action.setShortcut("Ctrl+Shift+C")
        self.consistency_action.setToolTip(
            "Fix name spelling variants, capitalization, and term inconsistencies"
        )
        self.consistency_action.triggered.connect(self._consistency_pass)
        self.consistency_action.setEnabled(False)
        advanced_menu.addAction(self.consistency_action)

        self.reset_all_action = QAction("Reset All for Retranslation", self)
        self.reset_all_action.setToolTip(
            "Mark all translated entries as untranslated so batch translate will redo them"
        )
        self.reset_all_action.triggered.connect(self._reset_all_for_retranslation)
        self.reset_all_action.setEnabled(False)
        advanced_menu.addAction(self.reset_all_action)

        translate_menu.addSeparator()

        self.translate_images_action = QAction(
            "Translate Images (Experimental)\u2026", self)
        self.translate_images_action.setShortcut("Ctrl+I")
        self.translate_images_action.setToolTip(
            "OCR Japanese text from game images, translate, and render English overlays"
        )
        self.translate_images_action.triggered.connect(self._translate_images)
        self.translate_images_action.setEnabled(False)
        translate_menu.addAction(self.translate_images_action)

        # ── Glossary menu ─────────────────────────────────────────
        glossary_menu = menubar.addMenu("Glossary")

        self.edit_glossary_action = QAction("Edit Glossary\u2026", self)
        self.edit_glossary_action.setToolTip(
            "Edit the general (all projects) and project glossaries")
        self.edit_glossary_action.triggered.connect(self._open_glossary)
        glossary_menu.addAction(self.edit_glossary_action)

        glossary_menu.addSeparator()

        self.load_vocab_action = QAction("Import Vocab File\u2026", self)
        self.load_vocab_action.setToolTip(
            "Import a DazedMTL-style vocab.txt into project glossary"
        )
        self.load_vocab_action.triggered.connect(self._load_vocab_file)
        self.load_vocab_action.setEnabled(False)
        glossary_menu.addAction(self.load_vocab_action)

        self.export_vocab_action = QAction("Export Vocab File\u2026", self)
        self.export_vocab_action.setToolTip(
            "Export glossary as a DazedMTL-compatible vocab.txt"
        )
        self.export_vocab_action.triggered.connect(self._export_vocab_file)
        self.export_vocab_action.setEnabled(False)
        glossary_menu.addAction(self.export_vocab_action)

        glossary_menu.addSeparator()

        self.scan_glossary_action = QAction(
            "Scan Translated Game\u2026", self)
        self.scan_glossary_action.setToolTip(
            "Open a translated game folder and harvest JP\u2192EN pairs "
            "to add to your general glossary"
        )
        self.scan_glossary_action.triggered.connect(
            self._scan_game_for_glossary)
        self.scan_glossary_action.setEnabled(False)
        glossary_menu.addAction(self.scan_glossary_action)

        self.scan_project_glossary_action = QAction(
            "Build from Translations\u2026", self)
        self.scan_project_glossary_action.setToolTip(
            "Scan this project's translations for terms to add "
            "to your general glossary"
        )
        self.scan_project_glossary_action.triggered.connect(
            self._scan_project_for_glossary)
        self.scan_project_glossary_action.setEnabled(False)
        glossary_menu.addAction(self.scan_project_glossary_action)

        glossary_menu.addSeparator()

        self.apply_glossary_action = QAction("Apply Glossary to All\u2026", self)
        self.apply_glossary_action.setToolTip(
            "Find translated entries where glossary terms are inconsistent "
            "and offer to fix them"
        )
        self.apply_glossary_action.triggered.connect(self._apply_glossary)
        self.apply_glossary_action.setEnabled(False)
        glossary_menu.addAction(self.apply_glossary_action)

        # ── Game menu ─────────────────────────────────────────────
        game_menu = menubar.addMenu("Game")

        self.export_action = QAction("Apply Translation to Game", self)
        self.export_action.setShortcut("Ctrl+E")
        self.export_action.setToolTip(
            "Write translated text into the game's data files"
        )
        self.export_action.triggered.connect(self._export_to_game)
        self.export_action.setEnabled(False)
        game_menu.addAction(self.export_action)

        self.restore_action = QAction("Restore Original Game Files", self)
        self.restore_action.setToolTip(
            "Restore backed-up Japanese originals to the game's data folder"
        )
        self.restore_action.triggered.connect(self._restore_originals)
        self.restore_action.setEnabled(False)
        game_menu.addAction(self.restore_action)

        self.open_rpgmaker_action = QAction("Open in RPG Maker", self)
        self.open_rpgmaker_action.setShortcut("Ctrl+R")
        self.open_rpgmaker_action.setToolTip(
            "Create a workspace project and open the game in RPG Maker for visual QA"
        )
        self.open_rpgmaker_action.triggered.connect(self._open_in_rpgmaker)
        self.open_rpgmaker_action.setEnabled(False)
        game_menu.addAction(self.open_rpgmaker_action)

        game_menu.addSeparator()

        self.txt_export_action = QAction("Export Raw Text\u2026", self)
        self.txt_export_action.setToolTip(
            "Export all original and translated text to a plain text file"
        )
        self.txt_export_action.triggered.connect(self._export_txt)
        self.txt_export_action.setEnabled(False)
        game_menu.addAction(self.txt_export_action)

        self.create_patch_action = QAction("Share Translation Data\u2026", self)
        self.create_patch_action.setToolTip(
            "Export translation mappings as a zip for other translators "
            "(no game data, copyright-safe)"
        )
        self.create_patch_action.triggered.connect(self._create_patch)
        self.create_patch_action.setEnabled(False)
        game_menu.addAction(self.create_patch_action)

        self.export_zip_action = QAction("Create Install Package\u2026", self)
        self.export_zip_action.setToolTip(
            "Export translated game files + install.bat as a zip \u2014 "
            "end users just extract and run"
        )
        self.export_zip_action.triggered.connect(self._export_patch_zip)
        self.export_zip_action.setEnabled(False)
        game_menu.addAction(self.export_zip_action)

        self.export_folder_zip_action = QAction(
            "Create Patch from Game Folder\u2026", self
        )
        self.export_folder_zip_action.setToolTip(
            "Package a game folder's current data/ files into a zip \u2014 "
            "works without a project, includes all existing translations"
        )
        self.export_folder_zip_action.triggered.connect(
            self._export_game_as_patch
        )
        game_menu.addAction(self.export_folder_zip_action)

        # ── Settings (top-level action) ───────────────────────────
        self.settings_action = QAction("Settings", self)
        self.settings_action.setShortcut("Ctrl+,")
        self.settings_action.setToolTip(
            "Model, provider, prompt, word wrap and appearance (Ctrl+,)")
        self.settings_action.triggered.connect(self._open_settings)
        menubar.addAction(self.settings_action)

        # ── Help menu ─────────────────────────────────────────────
        help_menu = menubar.addMenu("Help")
        about_action = QAction("About RPG Maker Translator…", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self):
        QMessageBox.about(
            self, "About RPG Maker Translator",
            f"<b>RPG Maker Translator</b> v{__version__}<br><br>"
            "Local-LLM translation tool for RPG Maker and visual novel games.<br><br>"
            "Licensed under the Business Source License 1.1 — free for "
            "non-commercial use.<br>"
            '<a href="https://github.com/MoriTranslates/rpgmaker-translator">'
            "github.com/MoriTranslates/rpgmaker-translator</a>",
        )

    def _build_toolbar(self):
        """Build a slim toolbar with quick-access controls."""
        toolbar = QToolBar("Quick Actions")
        toolbar.setIconSize(QSize(20, 20))
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        # Reuse actions created in _build_menubar
        toolbar.addAction(self.batch_db_action)
        toolbar.addAction(self.batch_dialogue_action)
        toolbar.addAction(self.stop_action)
        toolbar.addSeparator()
        toolbar.addAction(self.export_action)
        stop_btn = toolbar.widgetForAction(self.stop_action)
        if stop_btn is not None:
            stop_btn.setObjectName("stopButton")  # red while a run is active
        # Toolbar buttons show the shortcut in their tooltip (set on the
        # action — QToolButton re-syncs its tooltip whenever the action changes)
        for action in (self.batch_db_action, self.batch_dialogue_action,
                       self.export_action):
            keys = action.shortcut().toString(
                QKeySequence.SequenceFormat.NativeText)
            if keys and keys not in action.toolTip():
                action.setToolTip(f"{action.toolTip()}  ({keys})")
        self.toolbar = toolbar

    def _build_statusbar(self):
        """Build the bottom status bar with progress."""
        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimumWidth(180)
        self.progress_bar.setMaximumWidth(320)
        self.progress_bar.setVisible(False)
        self.statusbar.addPermanentWidget(self.progress_bar)

        self.progress_label = QLabel("")
        self.statusbar.addWidget(self.progress_label)

    def _connect_signals(self):
        """Wire up signals between components."""
        # File tree
        self.file_tree.file_selected.connect(self._filter_by_file)
        self.file_tree.all_selected.connect(self._show_all_entries)

        # Ctrl+F jumps to the table's search box from anywhere in the window
        self._find_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self._find_shortcut.activated.connect(self._focus_search)

        # Translation table
        self.trans_table.translate_requested.connect(self._translate_selected)
        self.trans_table.retranslate_correction.connect(self._retranslate_with_correction)
        self.trans_table.variant_requested.connect(self._show_variants)
        self.trans_table.polish_requested.connect(self._polish_selected)
        self.trans_table.status_changed.connect(self._on_status_changed)
        self.trans_table.glossary_add.connect(self._on_glossary_add)

        # Event Viewer (status_changed fires per keystroke — debounce refresh)
        self._ev_status_timer = QTimer(self)
        self._ev_status_timer.setSingleShot(True)
        self._ev_status_timer.setInterval(150)
        self._ev_status_timer.timeout.connect(self._refresh_after_event_viewer_change)
        self.event_viewer.status_changed.connect(self._on_event_viewer_status_changed)

        # Engine
        self.engine.progress.connect(self._on_progress)
        self.engine.entry_done.connect(self._on_entry_done)
        self.engine.error.connect(self._on_error)
        self.engine.checkpoint.connect(self._on_checkpoint)
        self.engine.finished.connect(self._on_batch_finished)
        self.engine.server_down.connect(self._on_server_down)

    def _focus_search(self):
        if not self.project.entries:
            return
        self.tabs.setCurrentIndex(0)
        self.trans_table.search_edit.setFocus()
        self.trans_table.search_edit.selectAll()

    # ── Actions ────────────────────────────────────────────────────

    def _open_project(self):
        """Open an RPG Maker MV/MZ or TyranoScript project folder."""
        if not self._ensure_idle_for_project_change():
            return
        path = QFileDialog.getExistingDirectory(
            self, "Select Game Project Folder"
        )
        if not path:
            return
        # Save the outgoing project before anything replaces it
        self._autosave()

        # Check for existing save state in project folder
        default_save = os.path.join(path, "_translation_state.json")
        autosave = os.path.join(path, "_translation_autosave.json")
        save_path = self._pick_newest_save(default_save, autosave)

        if save_path:
            reply = QMessageBox.question(
                self, "Resume Previous Session?",
                f"Found saved translation state:\n{os.path.basename(save_path)}\n\n"
                "Load it to resume where you left off?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                if self._restore_from_state(save_path):
                    self._enable_project_actions()
                    manual = getattr(self.plugin_analyzer, '_manual_chars_per_line', 0)
                    self.plugin_analyzer.analyze_project(path)
                    if manual > 0:
                        self.plugin_analyzer._manual_chars_per_line = manual
                        self.plugin_analyzer.chars_per_line = manual
                    self.image_panel.set_project(path, self.client)
                    # Count plugin entries for status message
                    plugin_count = sum(
                        1 for e in self.project.entries if e.file == "plugins.js"
                    )
                    plugin_info = (f" | +{plugin_count} plugin entries"
                                   if plugin_count else "")
                    self.statusbar.showMessage(
                        f"Resumed: {self.project.total} entries "
                        f"({self.project.translated_count} translated)"
                        f"{plugin_info}", 8000
                    )
                    folder = os.path.basename(path)
                    self.setWindowTitle(f"{self.handler.display_name} Translator \u2014 {folder}")
                    # Offer wizard if there are untranslated entries
                    # Show choice BEFORE preloading so dialog appears instantly
                    wizard_chosen = False
                    if self.project.translated_count < self.project.total:
                        wizard_chosen = self._show_wizard_choice()
                    # Preload model into VRAM (wizard handles its own Ollama calls)
                    if not wizard_chosen and not self.client.is_cloud:
                        self._preload_model()
                    return
                # Fall through to fresh project on load failure

        # Auto-extract NW.js TyranoScript exe if needed
        if not TyranoScriptParser.is_tyranoscript_project(path):
            nwjs_exe = TyranoScriptParser.find_nwjs_exe(path)
            if nwjs_exe:
                dest = os.path.join(path, "extracted")
                exe_name = os.path.basename(nwjs_exe)
                reply = QMessageBox.question(
                    self, "Extract TyranoScript Game?",
                    f"Found NW.js game executable:\n{exe_name}\n\n"
                    "Extract game data so it can be translated?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    progress = QProgressDialog(
                        "Extracting game data...", "Cancel", 0, 100, self)
                    progress.setWindowTitle("Extracting")
                    progress.setMinimumDuration(0)
                    progress.setValue(0)
                    QApplication.processEvents()

                    def on_progress(current, total):
                        if total > 0:
                            progress.setMaximum(total)
                            progress.setValue(current)
                        QApplication.processEvents()

                    try:
                        count = TyranoScriptParser.extract_nwjs(
                            nwjs_exe, dest, progress_cb=on_progress)
                        progress.close()
                        self.statusbar.showMessage(
                            f"Extracted {count} files from {exe_name}", 5000)
                    except Exception as e:
                        progress.close()
                        QMessageBox.warning(
                            self, "Extraction Failed", f"Error: {e}")
                        return

        # Detect project type and parse
        handler_cls = detect_engine(path)
        if handler_cls:
            self._project_type = handler_cls.key
        else:
            self._project_type = "rpgmaker_mv"
        self._sync_project_type(self._project_type)

        # Wolf RPG may need to unpack Data.wolf (can take 10-30s for large archives)
        if self._project_type == "wolfrpg":
            from pathlib import Path as _Path
            wolf_file = _Path(path) / 'Data.wolf'
            data_dir = _Path(path) / 'Data'
            needs_unpack = wolf_file.is_file() and not (
                data_dir.is_dir() and (data_dir / 'MapData').is_dir())
            if needs_unpack:
                progress = QProgressDialog(
                    "Unpacking Data.wolf...", None, 0, 0, self)
                progress.setWindowTitle("Unpacking Wolf RPG Data")
                progress.setMinimumDuration(0)
                progress.setCancelButton(None)
                progress.setValue(0)
                QApplication.processEvents()

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self.statusbar.showMessage(f"Reading {os.path.basename(path)}\u2026")
        self.statusbar.repaint()
        try:
            entries = self.handler.load_project(path)
        except FileNotFoundError as e:
            QApplication.restoreOverrideCursor()
            self.statusbar.clearMessage()
            QMessageBox.warning(
                self, "Can't Open Project",
                f"{e}\n\nPick the game's top-level folder (the one that "
                "contains the game .exe).")
            return
        except Exception:
            QApplication.restoreOverrideCursor()
            raise
        else:
            QApplication.restoreOverrideCursor()
            self.statusbar.clearMessage()
        finally:
            # Close progress dialog if it was opened
            if self._project_type == "wolfrpg" and 'progress' in locals():
                progress.close()

        if not entries:
            QMessageBox.warning(
                self, "No Entries Found",
                "Could not find translatable text in this folder.\n\n"
                "Supported formats:\n"
                "  - RPG Maker MV/MZ (data/*.json)\n"
                "  - RPG Maker VX Ace (Data/*.rvdata2)\n"
                "  - RPG Maker 2000/2003 (RPG_RT.ldb)\n"
                "  - Wolf RPG Editor (Data.wolf / Game.exe)\n"
                "  - Kirikiri/KAG (data/scenario/*.ks + .xp3)\n"
                "  - TyranoScript (data/scenario/*.ks)\n"
                "  - Ren'Py (game/*.rpy)\n"
                "  - SRPG Studio (data.dts)\n"
                "  - Crowd (*.sce)",
            )
            return

        self._reset_project_runtime_state()
        self.project = TranslationProject(
            project_path=path, project_type=self._project_type, entries=entries
        )
        self.file_tree.load_project(self.project)
        self.trans_table.set_entries(entries)
        self.event_viewer.set_entries(entries)
        self._update_spell_glossary()

        # Defer actor gender dialog + pre-translate to first batch start
        self._actors_ready = not self.handler.has_actors

        # Check for vocab.txt first — if found and accepted, skip default glossary
        vocab_loaded = self._check_vocab_file(path)

        # Offer default glossary only if no vocab.txt was loaded
        if not vocab_loaded and not self._general_glossary:
            from ..default_glossary import get_all_defaults
            reply = QMessageBox.question(
                self, "Load Default Glossary?",
                "Would you like to load common term translations?\n\n"
                "This adds ~100 preset Japanese→English mappings for body parts,\n"
                "RPG terms, expressions, etc. so the LLM translates them consistently.\n\n"
                "These go into the General Glossary (shared across all projects).\n"
                "You can edit them in Settings > General Glossary.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._general_glossary.update(get_all_defaults())
                self._save_settings()

        # Rebuild merged glossary (general + project auto-glossary entries)
        self._rebuild_glossary()

        # RPG Maker MV/MZ-specific: analyze plugins for word wrap settings
        if self.handler.has_plugin_system:
            manual = getattr(self.plugin_analyzer, '_manual_chars_per_line', 0)
            self.plugin_analyzer.analyze_project(path)
            # Restore manual override if user set one (survives auto-detect)
            if manual > 0:
                self.plugin_analyzer._manual_chars_per_line = manual
                self.plugin_analyzer.chars_per_line = manual

        self._enable_project_actions()

        # Initialize image panel
        self.image_panel.set_project(path, self.client)

        # Status bar message
        status_msg = self.handler.get_status_message(entries)
        if self.handler.has_plugin_system:
            if self.plugin_analyzer.detected_plugins:
                status_msg += f" | Plugins: {', '.join(self.plugin_analyzer.detected_plugins)}"
            status_msg += f" | ~{self.plugin_analyzer.chars_per_line} chars/line"
        self.statusbar.showMessage(status_msg, 8000)

        # Info about plugin entries (MV/MZ only)
        if self.handler.has_plugin_system:
            plugin_count = sum(1 for e in entries if e.file == "plugins.js")
            if plugin_count > 0:
                QMessageBox.information(
                    self, "Plugin Parameters",
                    f"Found {plugin_count} translatable strings in plugins.js.\n\n"
                    "Only values containing Japanese display text were extracted.\n"
                    "Asset filenames and internal identifiers are skipped.\n\n"
                    "Review the entries in the Plugins section of the file tree.\n"
                    "Skip any entries that look like command triggers or tags\n"
                    "rather than player-visible text.",
                )

        # Window title
        folder = os.path.basename(path)
        self.setWindowTitle(f"{self.handler.display_name} Translator — {folder}")

        # Offer folder rename for engines without actors (no pre-translate step)
        if not self.handler.has_actors:
            self._pre_translate_folder_title(path)

        # Offer window scaler for VX Ace projects
        if self._project_type == "rpgmaker_ace":
            self._offer_vxace_scaler(path)

        # Offer wizard vs manual mode for new projects
        wizard_chosen = False
        wizard_chosen = self._show_wizard_choice()

        # Preload model into VRAM (wizard handles its own Ollama calls)
        if not wizard_chosen and not self.client.is_cloud:
            self._preload_model()

    def _show_wizard_choice(self) -> bool:
        """Show wizard vs manual mode choice after opening a project.

        Returns True if wizard mode was chosen and executed.
        """
        from .translation_wizard import WizardChoiceDialog, TranslationWizard

        dlg = WizardChoiceDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.choice == WizardChoiceDialog.WIZARD:
            wizard = TranslationWizard(self)
            wizard.exec()
            return True
        return False

    @staticmethod
    def _pick_newest_save(*paths: str) -> str | None:
        """Return the most recently modified path that exists, or None."""
        candidates = [(p, os.path.getmtime(p)) for p in paths if os.path.isfile(p)]
        if not candidates:
            return None
        return max(candidates, key=lambda x: x[1])[0]

    def _close_project(self):
        """Close the current project and reset to empty state."""
        if not self.project.entries:
            return

        reply = QMessageBox.question(
            self, "Close Project",
            "Close the current project?\n\n"
            "Make sure you have saved your state first.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Stop any running batch translation first (refuses if it won't stop)
        if not self._ensure_idle_for_project_change():
            return
        self._autosave()

        # Reset project
        self._reset_project_runtime_state()
        self.project = TranslationProject()
        self._sync_project_type("rpgmaker_mv")
        self.file_tree.load_project(self.project)
        self.trans_table.set_entries([])
        self.event_viewer.set_entries([])

        # Disable project-dependent actions
        for action in self._project_actions():
            action.setEnabled(False)
        self.stop_action.setEnabled(False)

        self.pipeline_bar.setVisible(False)

        self.setWindowTitle(self._APP_TITLE)
        self._show_workspace(False)
        self.statusbar.showMessage("Project closed.", 5000)

    # ── Project lifecycle helpers ─────────────────────────────────

    def _project_actions(self) -> list:
        """All menu actions that require an open project."""
        return [
            # Project
            self.close_action, self.save_action, self.save_as_action,
            self.rename_action, self.import_action, self.import_folder_action,
            self.scan_plugin_edits_action,
            # Translate
            self.batch_db_action, self.batch_dialogue_action,
            self.batch_action, self.batch_actor_action, self.wordwrap_action,
            self.find_replace_action, self.cleanup_action,
            self.strip_actor_codes_action, self.polish_action,
            self.consistency_action, self.reset_all_action,
            self.translate_images_action,
            # Glossary
            self.load_vocab_action, self.export_vocab_action,
            self.scan_glossary_action, self.scan_project_glossary_action,
            self.apply_glossary_action,
            # Game
            self.export_action, self.restore_action, self.open_rpgmaker_action,
            self.txt_export_action, self.create_patch_action,
            self.export_zip_action,
        ]

    def _set_batch_running(self, running: bool):
        """Enable/disable every action that starts an engine run."""
        has_project = bool(self.project.entries)
        for action in (self.batch_action, self.batch_db_action,
                       self.batch_dialogue_action, self.batch_actor_action,
                       self.polish_action):
            action.setEnabled(has_project and not running)
        self.stop_action.setEnabled(running)

    def _reset_batch_ui(self):
        """Return the batch controls / progress display to the idle state."""
        self.queue_panel.mark_batch_finished()
        self._set_batch_running(False)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("")
        self.file_tree.refresh_stats(self.project)

    def _begin_run(self, kind: str):
        """Shared bookkeeping for every engine run started from this window.

        kind: "batch" (full/DB/dialogue/actor batch), "selected"
        (Translate Selected / cleanup retranslation) or "polish".
        Batch callers set self._batch_dupe_map after calling this.
        """
        self._run_kind = kind
        self._batch_project = self.project
        self._user_stopped = False
        self._batch_dupe_map = {}
        self._dupe_fill_count = 0
        self._batch_start_time = time.time()
        self._batch_done_count = 0
        self._set_batch_running(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

    def _reset_project_runtime_state(self):
        """Forget per-project runtime state before another project replaces it."""
        self._last_save_path = ""
        self._actors_ready = False
        self.client.actor_genders = {}
        self.client.actor_names = {}
        self.client.actor_names_en = {}
        self.client.actor_context = ""
        self._batch_all_chained = False
        self._batch_dupe_map = {}
        self._dupe_fill_count = 0
        self._old_translations = {}
        self._vocab_genders = {}
        self._is_cleanup_retranslation = False
        self._user_stopped = False
        self._batch_project = None

    def _check_engine_idle(self) -> bool:
        """Return True if a new engine run may start (nothing else in flight)."""
        if self._busy:
            self.statusbar.showMessage("Busy — please wait for the current step to finish.", 5000)
            return False
        if self.engine.is_running:
            self.statusbar.showMessage(
                "A translation run is already in progress — stop it first.", 5000)
            return False
        return True

    def _wait_for_engine_stop(self, timeout_s: float) -> bool:
        """Pump events until the engine's threads have exited (or timeout)."""
        deadline = time.monotonic() + timeout_s
        while self.engine.is_running and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.05)
        return not self.engine.is_running

    def _ensure_idle_for_project_change(self) -> bool:
        """Before open/load/close: stop a running batch (after confirming).

        Returns False if the user declined or the batch didn't stop in time,
        so results from the old project can never land in a new one.
        """
        if self._busy:
            self.statusbar.showMessage("Busy — please wait for the current step to finish.", 5000)
            return False
        if not self.engine.is_running:
            return True
        reply = QMessageBox.question(
            self, "Translation Running",
            "A translation run is in progress.\n\n"
            "Stop it now? Completed entries are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        self._stop_translation()
        self.statusbar.showMessage("Stopping translation...")
        if not self._wait_for_engine_stop(15):
            QMessageBox.information(
                self, "Still Stopping",
                "The translation workers are finishing their current request.\n"
                "Please try again in a moment.",
            )
            return False
        return True

    def _pre_translate_info(self, entries, actors_raw):
        """Translate game title + actor names/profiles before the gender dialog.

        Uses batch mode when batch_size > 1 (DazedMTL mode / cloud APIs)
        to translate all names in 1-2 API calls instead of one per field.

        Returns:
            (actor_translations, translated_title) where actor_translations is
            {actor_id: {"name": ..., "nickname": ..., "profile": ...}}
        """
        # Check availability first
        if not self.client.is_available():
            return {}, ""

        entry_by_id = {e.id: e for e in entries}
        batch_size = self.engine.batch_size if self.engine else 1

        # Find game title entry
        title_entry = None
        for e in entries:
            if e.id == "System.json/gameTitle":
                title_entry = e
                break

        # Collect all items that need translation (skip already-translated)
        translated_title = ""
        actor_translations = {}
        to_translate = []  # (key, text, hint) for batch

        if title_entry:
            if title_entry.status in ("translated", "reviewed"):
                translated_title = title_entry.translation
            else:
                to_translate.append(("gameTitle", title_entry.original, "game title"))

        field_hints = {
            "name": "character's personal name",
            "nickname": "character's nickname or title",
            "profile": "character's biography",
        }
        for actor in actors_raw:
            aid = actor["id"]
            if aid not in actor_translations:
                actor_translations[aid] = {}
            for field in ("name", "nickname", "profile"):
                text = actor.get(field, "")
                if not text:
                    continue
                entry_id = f"Actors.json/{aid}/{field}"
                entry = entry_by_id.get(entry_id)
                if entry and entry.status in ("translated", "reviewed") and entry.translation:
                    actor_translations[aid][field] = entry.translation
                    continue
                to_translate.append((f"actor_{aid}_{field}", text, field_hints[field]))

        if not to_translate:
            return actor_translations, translated_title

        progress = QProgressDialog(
            "Translating character info...", "Skip", 0, len(to_translate), self
        )
        progress.setWindowTitle("Pre-translating")
        progress.setMinimumDuration(0)
        progress.setValue(0)

        # ── Batch mode: send all names in chunks ──
        if batch_size > 1:
            progress.setLabelText(
                f"Batch translating {len(to_translate)} names..."
            )
            QApplication.processEvents()

            results = {}
            for i in range(0, len(to_translate), batch_size):
                if progress.wasCanceled():
                    break
                chunk = to_translate[i:i + batch_size]
                progress.setLabelText(
                    f"Translating names {i + 1}-{min(i + len(chunk), len(to_translate))} "
                    f"of {len(to_translate)}..."
                )
                QApplication.processEvents()
                batch_results = self.client.translate_names_batch(chunk)
                results.update(batch_results)
                progress.setValue(min(i + len(chunk), len(to_translate)))
                QApplication.processEvents()

            # Apply batch results
            for key, text, _hint in to_translate:
                translated = results.get(key, "")
                if not translated:
                    continue
                if key == "gameTitle":
                    translated_title = translated
                    if title_entry and title_entry.status == "untranslated":
                        title_entry.translation = translated
                        title_entry.status = "translated"
                elif key.startswith("actor_"):
                    _, aid_str, field = key.split("_", 2)
                    aid = int(aid_str)
                    if aid not in actor_translations:
                        actor_translations[aid] = {}
                    actor_translations[aid][field] = translated
                    entry_id = f"Actors.json/{aid}/{field}"
                    entry = entry_by_id.get(entry_id)
                    if entry and entry.status == "untranslated":
                        entry.translation = translated
                        entry.status = "translated"

        # ── Single mode: one API call per name ──
        else:
            for idx, (key, text, hint) in enumerate(to_translate):
                progress.setLabelText(f"Translating {hint}...")
                QApplication.processEvents()
                if progress.wasCanceled():
                    break

                result = self.client.translate_name(text, hint=hint)
                if result and result != text:
                    if key == "gameTitle":
                        translated_title = result
                        if title_entry and title_entry.status == "untranslated":
                            title_entry.translation = result
                            title_entry.status = "translated"
                    elif key.startswith("actor_"):
                        _, aid_str, field = key.split("_", 2)
                        aid = int(aid_str)
                        if aid not in actor_translations:
                            actor_translations[aid] = {}
                        actor_translations[aid][field] = result
                        entry_id = f"Actors.json/{aid}/{field}"
                        entry = entry_by_id.get(entry_id)
                        if entry and entry.status == "untranslated":
                            entry.translation = result
                            entry.status = "translated"
                progress.setValue(idx + 1)

        progress.close()
        return actor_translations, translated_title

    def _ensure_actors_ready(self) -> bool:
        """Run actor pre-translate + gender dialog once before first batch.

        Returns True if ready to proceed, False if user cancelled or
        no project is open.
        """
        if self._actors_ready:
            return True
        if self._busy:
            return False
        self._busy = True
        try:
            return self._run_actor_setup()
        finally:
            self._busy = False

    @staticmethod
    def _add_protagonist_hint(actors_raw: list):
        """Mark Actor 1 as the protagonist in the in-memory actor list.

        Without a profile the LLM has no hint that Actor 1 is the main
        character.  The hint only feeds client.actor_context — game files
        (including the data_original backup) are never modified.
        """
        actor1 = next((a for a in actors_raw if a.get("id") == 1), None)
        if actor1 and not (actor1.get("profile") or "").strip():
            actor1["profile"] = "Protagonist of the game."

    def _run_actor_setup(self) -> bool:
        """Body of _ensure_actors_ready (runs under the _busy guard)."""
        path = self.project.project_path
        if not path:
            return False

        entries = self.project.entries
        actors_raw = self.handler.load_actors(path)

        # Pre-translate game title + actor info so the user can read them
        translated_title = ""
        actor_translations = {}
        raw_title = self.handler.get_game_title(path)
        title_id = "System.rvdata2/game_title" if self._project_type == "rpgmaker_ace" else "System.json/gameTitle"
        has_jp_title = any(e.id == title_id for e in entries)
        if actors_raw or has_jp_title:
            actor_translations, translated_title = self._pre_translate_info(
                entries, actors_raw
            )
        if not translated_title and raw_title and not has_jp_title:
            translated_title = raw_title

        # Auto-glossary: add translated actor names to project glossary
        for aid, tl in actor_translations.items():
            for field_name in ("name", "nickname"):
                en = tl.get(field_name, "")
                if not en:
                    continue
                actor = next((a for a in actors_raw if a["id"] == aid), None)
                if not actor:
                    continue
                jp = actor.get(field_name, "")
                if jp and en != jp and jp not in self.project.glossary:
                    self.project.glossary[jp] = en

        # Apply vocab.txt gender overrides (if loaded)
        if hasattr(self, "_vocab_genders") and self._vocab_genders:
            for actor in actors_raw:
                jp_name = actor.get("name", "")
                if jp_name in self._vocab_genders:
                    actor["auto_gender"] = self._vocab_genders[jp_name]

        # Ask protagonist gender upfront (Actor 1 is protagonist in ~all RPG Maker games)
        if actors_raw:
            actor1 = next((a for a in actors_raw if a["id"] == 1), None)
            if actor1 and actor1.get("auto_gender", "") not in ("male", "female"):
                tl1 = actor_translations.get(1, {})
                name = tl1.get("name") or actor1.get("name", "Actor 1")
                box = QMessageBox(self)
                box.setWindowTitle("Protagonist Gender")
                box.setText(
                    f"Is the protagonist \"{name}\" male or female?\n\n"
                    "This ensures correct pronouns (he/she) throughout the game."
                )
                male_btn = box.addButton("Male", QMessageBox.ButtonRole.YesRole)
                female_btn = box.addButton("Female", QMessageBox.ButtonRole.NoRole)
                box.exec()
                if box.clickedButton() == male_btn:
                    actor1["auto_gender"] = "male"
                else:
                    actor1["auto_gender"] = "female"

        # Show gender assignment dialog with translated names
        if actors_raw:
            dlg = ActorGenderDialog(actors_raw, self, translations=actor_translations)
            if dlg.exec():
                genders = dlg.get_genders()
            else:
                genders = {a["id"]: a["auto_gender"] for a in actors_raw
                           if a["auto_gender"] != "unknown"}
            # Protagonist hint goes into the LLM context only (never to disk)
            self._add_protagonist_hint(actors_raw)
            _ctx_parser = self.handler.parser if hasattr(self.handler.parser, 'build_actor_context') else self.parser
            actor_ctx = _ctx_parser.build_actor_context(actors_raw, genders)
            self.client.actor_context = actor_ctx
            self.client.actor_genders = genders
            self.client.actor_names = {a["id"]: a["name"] for a in actors_raw}
            self.client.actor_names_en = {
                aid: tl["name"] for aid, tl in actor_translations.items()
                if tl.get("name")}
            self.project.actor_genders = genders
        else:
            self.client.actor_context = ""
            self.client.actor_genders = {}
            self.client.actor_names = {}
            self.client.actor_names_en = {}

        # Rebuild glossary with any new actor name entries
        self._rebuild_glossary()

        # Update speaker contexts: replace JP actor names with EN translations
        if self.handler.has_speaker_processing:
            self._update_speaker_names(actors_raw, actor_translations)
            self.trans_table.refresh_speaker_filter()

        # Offer to rename folder to English title (only on first run)
        if translated_title:
            new_path = self._rename_project_folder(path, translated_title)
            self.project.project_path = new_path
            if new_path != path:
                self.image_panel.set_project(new_path, self.client)

        self._actors_ready = True
        return True

    def _actor_translations_from_entries(self, actors_raw):
        """Build actor_translations dict from already-translated entries."""
        entry_by_id = {e.id: e for e in self.project.entries}
        actor_tl = {}
        for actor in actors_raw:
            aid = actor["id"]
            for field in ("name", "nickname", "profile"):
                entry = entry_by_id.get(f"Actors.json/{aid}/{field}")
                if entry and entry.translation:
                    if aid not in actor_tl:
                        actor_tl[aid] = {}
                    actor_tl[aid][field] = entry.translation
        return actor_tl

    def _update_spell_glossary(self):
        """Feed project + general glossary terms into spell checker."""
        merged = dict(self._general_glossary)
        if self.project:
            merged.update(self.project.glossary)
        if merged:
            self.trans_table.update_spell_glossary(merged)

    def _update_speaker_names(self, actors_raw, actor_translations):
        """Replace JP speaker names in entry contexts with EN translations."""
        # Build JP → EN name map from actors
        jp_to_en = {}
        for actor in actors_raw:
            aid = actor["id"]
            jp_name = actor.get("name", "")
            en_name = actor_translations.get(aid, {}).get("name", "")
            if jp_name and en_name and jp_name != en_name:
                jp_to_en[jp_name] = en_name
        # Also include translated NPC speaker names (MZ params[4] / namebox)
        if self.project:
            for entry in self.project.entries:
                if (entry.field == "speaker_name"
                        and entry.translation
                        and entry.original != entry.translation):
                    jp_to_en[entry.original] = entry.translation
        if not jp_to_en:
            return
        for entry in self.project.entries:
            if not entry.context or "[Speaker:" not in entry.context:
                continue
            m = re.search(r'\[Speaker:\s*(.+?)\]', entry.context)
            if m and m.group(1).strip() in jp_to_en:
                old_name = m.group(1).strip()
                entry.context = entry.context.replace(
                    f"[Speaker: {old_name}]",
                    f"[Speaker: {jp_to_en[old_name]}]",
                )

    def _backfill_db_glossary(self) -> int:
        """Add DB name glossary entries from already-translated entries.

        Scans translated name fields from all database files (Actors, Items,
        Weapons, Armors, Skills, Enemies, States, Classes) and adds missing
        glossary mappings so the LLM uses consistent terms in dialogue.

        Called on load_state to handle projects saved before auto-glossary
        covered all DB types (or before this feature existed at all).
        Writes directly to project.glossary (doesn't require client.glossary).

        Returns the number of entries added.
        """
        if not self.project:
            return 0
        before = len(self.project.glossary)
        for entry in self.project.entries:
            fields = self.handler.auto_glossary_fields.get(entry.file)
            is_map_name = (
                entry.file.startswith("Map")
                and entry.file.endswith(".json")
                and entry.field == self._AUTO_GLOSSARY_MAP_FIELD
            )
            if not is_map_name and (not fields or entry.field not in fields):
                continue
            jp = entry.original
            en = entry.translation
            if jp and en and jp != en and jp not in self.project.glossary:
                # Skip terms already covered by the general glossary
                if jp not in self._general_glossary:
                    self.project.glossary[jp] = en
        return len(self.project.glossary) - before

    def _title_case(self, text: str) -> str:
        """Title-case text, keeping prepositions/articles lowercase.

        First word is always capitalized.  Uses _TITLE_SMALL_WORDS set.
        """
        words = text.split(" ")
        result = []
        for i, w in enumerate(words):
            if not w:
                result.append(w)
            elif i == 0 or w.lower() not in self._TITLE_SMALL_WORDS:
                result.append(w[0].upper() + w[1:])
            else:
                result.append(w.lower())
        return " ".join(result)

    def _maybe_add_to_glossary(self, entry):
        """Auto-add translated DB name fields to glossary for LLM consistency."""
        fields = self.handler.auto_glossary_fields.get(entry.file)
        is_map_name = (
            entry.file.startswith("Map")
            and entry.file.endswith(".json")
            and entry.field == self._AUTO_GLOSSARY_MAP_FIELD
        )
        is_speaker = entry.field == "speaker_name"
        if not is_map_name and not is_speaker and (not fields or entry.field not in fields):
            return
        jp = entry.original
        en = entry.translation
        if jp and en and jp != en and jp not in self.client.glossary:
            self.client.glossary[jp] = en
            # Don't duplicate into project glossary if already in general
            if jp not in self._general_glossary:
                self.project.glossary[jp] = en

    def _offer_vxace_scaler(self, path: str):
        """Offer to inject a window scaler script for VX Ace games."""
        try:
            from ..vxace_scaler import (
                detect_resolution, inject_scaler, is_already_injected,
                HAS_RUBYMARSHAL,
            )
        except ImportError:
            return
        if not HAS_RUBYMARSHAL:
            return

        scripts_path = os.path.join(path, "Data", "Scripts.rvdata2")
        if not os.path.exists(scripts_path):
            return

        already = is_already_injected(scripts_path)
        base_w, base_h = detect_resolution(scripts_path)

        # Build the dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Window Scaler")
        layout = QVBoxLayout(dlg)

        if already:
            layout.addWidget(QLabel(
                f"Window scaler already installed.\n"
                f"Base resolution: {base_w}x{base_h}\n"
                f"Use PgUp/PgDn in-game to resize."
            ))
            btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            btn_box.accepted.connect(dlg.accept)
            layout.addWidget(btn_box)
            dlg.exec()
            return

        layout.addWidget(QLabel(
            f"This VX Ace game runs at {base_w}x{base_h}.\n\n"
            f"Inject a window scaler script?\n"
            f"  - PgUp / PgDn to resize in-game\n"
            f"  - Scales: 1x, 1.5x, 2x, 2.5x, 3x\n\n"
            f"Pick the default scale on launch:"
        ))

        from PyQt6.QtWidgets import QComboBox
        combo = QComboBox()
        scales = [
            ("1x (original)", 1.0),
            (f"1.5x ({int(base_w*1.5)}x{int(base_h*1.5)})", 1.5),
            (f"2x ({base_w*2}x{base_h*2})", 2.0),
            (f"2.5x ({int(base_w*2.5)}x{int(base_h*2.5)})", 2.5),
            (f"3x ({base_w*3}x{base_h*3})", 3.0),
        ]
        for label, _ in scales:
            combo.addItem(label)
        combo.setCurrentIndex(2)  # Default to 2x
        layout.addWidget(combo)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        chosen_scale = scales[combo.currentIndex()][1]
        ok = inject_scaler(scripts_path, default_scale=chosen_scale)
        if ok:
            self.statusbar.showMessage(
                f"Window scaler injected: {base_w}x{base_h} default "
                f"{chosen_scale}x — PgUp/PgDn to resize in-game", 8000
            )
        else:
            QMessageBox.warning(
                self, "Scaler Error",
                "Failed to inject window scaler.\n"
                "Check that Data/Scripts.rvdata2 is writable."
            )

    def _pre_translate_folder_title(self, path: str):
        """Pre-translate the folder name and offer a rename (actor-less engines)."""
        folder_name = os.path.basename(path.rstrip("/\\"))
        if not self.client.is_available():
            return
        self.statusbar.showMessage("Translating game title...")
        QApplication.processEvents()
        translated = self.client.translate_name(folder_name, hint="game title")
        self.statusbar.clearMessage()
        if translated and translated != folder_name:
            new_path = self._rename_project_folder(path, translated)
            self.project.project_path = new_path
            if new_path != path:
                folder = os.path.basename(new_path)
                self.setWindowTitle(
                    f"{self.handler.display_name} Translator \u2014 {folder}")
                self.image_panel.set_project(new_path, self.client)

    def _rename_project_folder(self, path: str, translated_title: str) -> str:
        """Offer to rename the project folder to 'English Title - WIP'.

        Returns the (possibly new) project path.
        """
        if not translated_title:
            return path

        # Sanitize for filesystem — remove characters illegal on Windows
        safe_name = re.sub(r'[\\/:*?"<>|]', '', translated_title).strip()
        # Collapse multiple spaces
        safe_name = re.sub(r'\s+', ' ', safe_name)
        if not safe_name:
            return path

        new_name = f"{safe_name} - WIP"
        parent = os.path.dirname(path)
        new_path = os.path.join(parent, new_name)

        if os.path.normpath(new_path) == os.path.normpath(path):
            return path  # Already named correctly

        if os.path.exists(new_path):
            # Target already exists — use it without renaming
            return path

        reply = QMessageBox.question(
            self, "Rename Project Folder",
            f"Rename folder to English title?\n\n"
            f"From: {os.path.basename(path)}\n"
            f"To: {new_name}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return path

        try:
            os.rename(path, new_path)
            # Update autosave path only if it's inside the old folder
            if self._last_save_path and os.path.dirname(self._last_save_path) == path:
                self._last_save_path = os.path.join(
                    new_path, os.path.basename(self._last_save_path)
                )
            return new_path
        except OSError as e:
            QMessageBox.warning(
                self, "Rename Failed",
                f"Could not rename folder:\n{e}\n\n"
                "Continuing with original folder name."
            )
            return path

    def _rename_folder(self):
        """Translate the folder name and rename the project folder."""
        if not self.project.project_path or not os.path.isdir(self.project.project_path):
            QMessageBox.warning(self, "No Project", "Open a project first.")
            return

        folder_name = os.path.basename(self.project.project_path)

        # Translate the folder name via Ollama
        translated = folder_name
        if self.client.is_available():
            self.statusbar.showMessage("Translating folder name...")
            QApplication.processEvents()
            result = self.client.translate_name(folder_name, hint="game title")
            if result and result != folder_name:
                translated = result
            self.statusbar.clearMessage()

        suggested = f"{translated} - WIP"
        suggested = re.sub(r'[\\/:*?"<>|]', '', suggested).strip()
        suggested = re.sub(r'\s+', ' ', suggested)

        new_name, ok = QInputDialog.getText(
            self, "Rename Folder",
            f"Current: {folder_name}\n"
            f"Translated: {translated}\n\n"
            f"New folder name:",
            text=suggested,
        )
        if not ok or not new_name.strip():
            return

        new_name = new_name.strip()
        new_name = re.sub(r'[\\/:*?"<>|]', '', new_name).strip()
        new_name = re.sub(r'\s+', ' ', new_name)
        if not new_name:
            QMessageBox.warning(self, "Invalid Name",
                                "The folder name contains only invalid characters.")
            return

        parent = os.path.dirname(self.project.project_path)
        new_path = os.path.join(parent, new_name)

        if os.path.normpath(new_path) == os.path.normpath(self.project.project_path):
            return

        if os.path.exists(new_path):
            QMessageBox.warning(self, "Already Exists",
                                f"A folder named '{new_name}' already exists.")
            return

        try:
            old_path = self.project.project_path
            os.rename(old_path, new_path)
            self.project.project_path = new_path
            # Update autosave path only if it's inside the old folder
            if self._last_save_path and os.path.dirname(self._last_save_path) == old_path:
                self._last_save_path = os.path.join(
                    new_path, os.path.basename(self._last_save_path)
                )
            self.setWindowTitle(f"{self.handler.display_name} Translator \u2014 {new_name}")
            self.image_panel.set_project(new_path, self.client)
            self.statusbar.showMessage(f"Renamed folder to: {new_name}", 5000)
        except OSError as e:
            QMessageBox.warning(self, "Rename Failed",
                                f"Could not rename folder:\n{e}")

    def _save_state(self):
        """Save state to default project path (no dialog)."""
        if not self.project.entries:
            return
        if not self._last_save_path:
            if self.project.project_path:
                self._last_save_path = os.path.join(
                    self.project.project_path, "_translation_state.json"
                )
            else:
                self._save_state_as()
                return
        self.project.save_state(self._last_save_path)
        self.statusbar.showMessage(
            f"Saved to {os.path.basename(self._last_save_path)}", 3000)

    def _save_state_as(self):
        """Save state with file dialog for custom location."""
        default_dir = self.project.project_path or ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Translation State", default_dir, "JSON Files (*.json)"
        )
        if path:
            self.project.save_state(path)
            self._last_save_path = path
            self.statusbar.showMessage(f"State saved to {path}", 3000)

    def _load_state(self):
        """Load a previously saved translation state via file dialog."""
        if not self._ensure_idle_for_project_change():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Load Translation State", "", "JSON Files (*.json)"
        )
        if not path:
            return
        # Save the outgoing project before it is replaced
        self._autosave()
        if not self._restore_from_state(path):
            return
        self._enable_project_actions()

        # Initialize image panel
        if self.project.project_path:
            self.image_panel.set_project(self.project.project_path, self.client)

        self.statusbar.showMessage(
            f"Loaded state: {self.project.total} entries "
            f"({self.project.translated_count} translated)", 5000
        )
        name = os.path.basename(self.project.project_path) if self.project.project_path else "Restored"
        self.setWindowTitle(f"{self.handler.display_name} Translator \u2014 {name}")

    def _restore_from_state(self, path: str) -> bool:
        """Load a save file and restore full project state.

        Returns True on success, False on error (shows warning dialog).
        """
        try:
            project = TranslationProject.load_state(path)
        except Exception as e:
            QMessageBox.warning(
                self, "Can't Load Save State",
                f"The save file could not be read:\n{e}\n\n"
                "If it was written by a newer version or edited by hand, try "
                "the autosave (_translation_autosave.json) in the game folder.")
            return False
        self._reset_project_runtime_state()
        self.project = project

        # Restore project type from saved state
        self._sync_project_type(self.project.project_type)
        is_tyrano = self._project_type == "tyranoscript"

        # If saved project_path is stale (folder renamed/moved), update it
        # to the directory containing the save file (works for saves inside
        # the project folder like _translation_state.json).
        if self.project.project_path and not os.path.isdir(self.project.project_path):
            save_dir = os.path.dirname(os.path.abspath(path))
            if self.handler.is_valid_project_dir(save_dir):
                self.project.project_path = save_dir

        # RPG Maker MV/MZ-specific: merge plugin entries added after the state was saved
        if self.handler.has_plugin_system and self.project.project_path:
            self._merge_new_plugin_entries()

        self.file_tree.load_project(self.project)
        self.trans_table.set_entries(self.project.entries)
        self.event_viewer.set_entries(self.project.entries)

        # vocab.txt is only auto-offered on a fresh open (Glossary > Import
        # Vocab File covers resumed projects) so a resume never alters the
        # saved project glossary.

        self._rebuild_glossary()
        self._update_spell_glossary()

        # Restore actor context from saved genders
        if self.handler.has_actors and self.project.actor_genders and self.project.project_path:
            actors_raw = self.handler.load_actors(self.project.project_path)
            if actors_raw:
                actor_names = {a["id"]: a["name"] for a in actors_raw}
                self._add_protagonist_hint(actors_raw)
                _ctx_parser = self.handler.parser if hasattr(self.handler.parser, 'build_actor_context') else self.parser
                self.client.actor_context = _ctx_parser.build_actor_context(
                    actors_raw, self.project.actor_genders
                )
                self.client.actor_genders = self.project.actor_genders
                self.client.actor_names = actor_names
                actor_tl = self._actor_translations_from_entries(actors_raw)
                self.client.actor_names_en = {
                    aid: tl["name"] for aid, tl in actor_tl.items()
                    if tl.get("name")}
                # Update speaker contexts with translated actor names
                if self.handler.has_speaker_processing:
                    self._update_speaker_names(actors_raw, actor_tl)
                    self.trans_table.refresh_speaker_filter()
            self._actors_ready = True
        else:
            self._actors_ready = not self.handler.has_actors

        self._backfill_db_glossary()
        self._last_save_path = path
        return True

    def _merge_new_plugin_entries(self) -> int:
        """Extract plugin + System.json entries and merge any missing from project.

        Handles saves created before plugin extraction or new System.json
        term fields (params, basic) were added — new entries are appended
        without duplicating existing ones.

        Returns the number of entries added.
        """
        existing_ids = {e.id for e in self.project.entries}
        new_entries = self.parser._parse_plugins(self.project.project_path)
        # Also re-parse System.json for newly supported term fields
        data_dir = self.parser._find_data_dir(self.project.project_path)
        if data_dir:
            new_entries.extend(self.parser._parse_system(data_dir))
        added = [e for e in new_entries if e.id not in existing_ids]
        if added:
            self.project.entries.extend(added)
            # Keep get_entry_by_id() in sync, or results for these are dropped
            self.project._build_index()
        return len(added)

    def _enable_project_actions(self):
        """Enable all project-dependent menu actions."""
        has_path = bool(self.project.project_path)
        self._show_workspace(True)
        for action in self._project_actions():
            action.setEnabled(True)
        # Actions that need the game folder on disk
        for action in (self.rename_action, self.export_action,
                       self.restore_action, self.open_rpgmaker_action):
            action.setEnabled(has_path)
        # Install package uses the MV/MZ exporter
        self.export_zip_action.setEnabled(
            bool(getattr(self.handler, "has_plugin_system", False)))
        self._set_batch_running(self.engine.is_running)

        # Reset pipeline bar for manual mode (wizard controls its own flow)
        if not self._wizard_active:
            self.pipeline_bar.set_engine(self.handler.pipeline_steps)
            self.pipeline_bar.reset()
            # Auto-detect already-completed steps
            if not self.handler.has_db_split:
                # Engines without DB split: single "Translate" step covers all entries
                all_done = all(
                    e.status != "untranslated" for e in self.project.entries
                ) if self.project.entries else False
                if all_done:
                    self.pipeline_bar.mark_done("dialogue")
            else:
                db_entries = [e for e in self.project.entries if e.file in self.handler.db_files]
                dialogue_entries = [e for e in self.project.entries if e.file not in self.handler.db_files]
                db_done = all(e.status != "untranslated" for e in db_entries) if db_entries else False
                dlg_done = all(e.status != "untranslated" for e in dialogue_entries) if dialogue_entries else False
                if db_done:
                    self.pipeline_bar.mark_done("db")
                if dlg_done:
                    self.pipeline_bar.mark_done("dialogue")

    def _sync_project_type(self, ptype: str):
        """Update project type on client, handler, and system prompt."""
        # Resolve legacy alias from old save files
        from ..engine_handler import _KEY_ALIASES
        ptype = _KEY_ALIASES.get(ptype, ptype)
        self._project_type = ptype
        self.handler = self._engine_handlers.get(ptype, self._engine_handlers["rpgmaker_mv"])
        self.client.project_type = ptype
        # Auto-switch system prompt unless user has customized it
        from ..ai_client import _PROMPT_REGISTRY
        current = self.client.system_prompt.strip()
        known_prompts = {p.strip() for p in _PROMPT_REGISTRY.values()}
        # Only auto-switch if using a known engine prompt (not user-customized)
        if current in known_prompts or not current:
            self.client.system_prompt = build_system_prompt(
                target_language=self.client.target_language,
                model=self.client.model,
                project_type=ptype,
            )
        # Apply per-engine settings (saved overrides or handler defaults)
        self._apply_engine_settings(ptype)

    def _apply_engine_settings(self, engine_key: str):
        """Apply per-engine settings from saved overrides or handler defaults."""
        handler = self.handler
        # Load saved per-engine overrides
        overrides = self._engine_overrides.get(engine_key, {})

        # Context size
        ctx = overrides.get("context_size", handler.default_context_size)
        if handler.parser:
            handler.parser.context_size = ctx
        for h in self._engine_handlers.values():
            if h.parser and h.parser is not handler.parser:
                # Also set on alternate parsers for consistency
                if hasattr(h.parser, 'context_size'):
                    h.parser.context_size = ctx

        # Batch size
        batch = overrides.get("batch_size", handler.default_batch_size)
        if self.engine:
            self.engine.batch_size = batch

        # Workers
        if "workers" in overrides and self.engine:
            self.engine.num_workers = overrides["workers"]

        # Word wrap chars/line
        ww = overrides.get("wordwrap_chars", handler.default_wordwrap_chars)
        if self.plugin_analyzer:
            self.plugin_analyzer._manual_chars_per_line = ww
            if ww > 0:
                self.plugin_analyzer.chars_per_line = ww

        # Per-engine model override (falls back to the global model so an
        # override from a previous engine never sticks)
        model = overrides.get("model", "")
        self.client.model = model or self._global_model

    def _save_engine_settings(self):
        """Save current settings as per-engine overrides for the active engine."""
        engine_key = self._project_type
        overrides = {}
        if self.handler.parser:
            overrides["context_size"] = self.handler.parser.context_size
        if self.engine:
            overrides["batch_size"] = self.engine.batch_size
            overrides["workers"] = self.engine.num_workers
        if self.plugin_analyzer:
            overrides["wordwrap_chars"] = getattr(
                self.plugin_analyzer, '_manual_chars_per_line', 0)
        # Preserve model override if one was set
        existing = self._engine_overrides.get(engine_key, {})
        if "model" in existing:
            overrides["model"] = existing["model"]
        self._engine_overrides[engine_key] = overrides

    def _ensure_ollama_ready(self):
        """Start Ollama if needed. Called on-demand before translation."""
        if self.client.is_cloud:
            return True
        if not self.client.is_available():
            self.client.restart_server(self.engine.num_workers)
        if not self.client.is_available():
            QMessageBox.warning(self, "Ollama", "Cannot connect to Ollama. Please start it manually.")
            return False
        return True

    def _preload_model(self):
        """Unload stale models and load the active one into VRAM.

        Clears any previously loaded models first to free VRAM,
        then sends a blank request with keep_alive=-1 so the model
        stays resident and ready for instant inference.
        """
        model = self.client.model
        self.statusbar.showMessage(f"Loading {model} into VRAM...", 5000)

        def work():
            # Clear other models from VRAM first, then load the active one
            unloaded = self.client.unload_models()
            return unloaded, self.client.preload_model()

        def on_done(result):
            unloaded, ok = result
            cleared = f"Cleared {unloaded} model(s) from VRAM — " if unloaded else ""
            if ok:
                self.statusbar.showMessage(
                    f"{cleared}{model} loaded — ready to translate", 5000)
            else:
                self.statusbar.showMessage(
                    f"Could not preload {model} — will load on first translate", 5000)

        def on_error(err):
            self.statusbar.showMessage(
                f"Could not preload {model} — will load on first translate", 5000)

        # Fire-and-forget: loading can take up to ~2 minutes
        run_in_thread(self, work, on_done=on_done, on_error=on_error)

    def _import_translations(self):
        """Import translations from an older version's save state."""
        if not self.project:
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Old Translation State", "", "JSON Files (*.json)"
        )
        if not path:
            return

        try:
            old_project = TranslationProject.load_state(path)
        except Exception as e:
            QMessageBox.warning(self, "Can't Load Save State",
                                f"The old save file could not be read:\n{e}")
            return

        old_translated = sum(
            1 for e in old_project.entries
            if e.status in ("translated", "reviewed")
        )
        current_untranslated = self.project.untranslated_count

        reply = QMessageBox.question(
            self, "Import Translations",
            f"Old project: {len(old_project.entries)} entries "
            f"({old_translated} translated)\n"
            f"Current project: {self.project.total} entries "
            f"({current_untranslated} untranslated)\n\n"
            f"Import matching translations into untranslated entries?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        stats = self.project.import_translations(old_project)

        # Also import glossary entries that don't conflict
        imported_glossary = 0
        for jp, en in old_project.glossary.items():
            if jp not in self.project.glossary:
                self.project.glossary[jp] = en
                imported_glossary += 1
        self._rebuild_glossary()

        # Refresh UI
        self.trans_table.set_entries(self.project.entries)
        self.event_viewer.set_entries(self.project.entries)
        self.file_tree.load_project(self.project)

        total_imported = stats["by_id"] + stats["by_text"]
        QMessageBox.information(
            self, "Import Complete",
            f"Imported {total_imported} translations:\n"
            f"  \u2022 {stats['by_id']} matched by exact position\n"
            f"  \u2022 {stats['by_text']} matched by identical text\n"
            f"  \u2022 {stats['new']} new entries (need translation)\n"
            f"  \u2022 {stats['skipped']} already translated (kept)\n"
            + (f"  \u2022 {imported_glossary} glossary entries imported\n"
               if imported_glossary else "")
        )

    def _import_from_game_folder(self):
        """Import translations from an already-translated game folder."""
        if not self.project:
            return

        folder = QFileDialog.getExistingDirectory(
            self, "Select Game Folder to Import From"
        )
        if not folder:
            return

        from ..rpgmaker_mv import RPGMakerMVParser, _has_japanese

        parser = RPGMakerMVParser()
        try:
            donor_entries = parser.load_project_raw(folder)
        except FileNotFoundError as e:
            QMessageBox.warning(self, "Can't Read Game Folder", str(e))
            return
        except Exception as e:
            QMessageBox.warning(
                self, "Can't Read Game Folder",
                f"Failed to read the game folder:\n{e}"
            )
            return

        if not donor_entries:
            QMessageBox.warning(
                self, "No Entries",
                "No text entries found in that game folder."
            )
            return

        # Detect if columns would be swapped: donor has JP text but
        # project originals are non-JP (user opened the translated game
        # and is importing the JP original).
        swap = False
        donor_by_id = {e.id: e.original for e in donor_entries}
        sample_donor_jp = 0
        sample_proj_jp = 0
        sample_count = 0
        for entry in self.project.entries:
            if entry.status != "untranslated":
                continue
            dt = donor_by_id.get(entry.id)
            if dt is None or dt == entry.original:
                continue
            if _has_japanese(dt):
                sample_donor_jp += 1
            if _has_japanese(entry.original):
                sample_proj_jp += 1
            sample_count += 1
            if sample_count >= 50:
                break

        if sample_count > 0 and sample_donor_jp > sample_proj_jp:
            # Donor looks more Japanese than project — likely reversed
            reply = QMessageBox.question(
                self, "Import — Column Order",
                "The selected folder appears to contain the Japanese "
                "original, while your project contains the translated "
                "text.\n\n"
                "Swap columns so the Japanese text becomes the Original "
                "and your current text becomes the Translation?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            swap = (reply == QMessageBox.StandardButton.Yes)

        current_untranslated = self.project.untranslated_count

        # --- Import options dialog ---
        dlg = QDialog(self)
        dlg.setWindowTitle("Import from Game Folder")
        dlg_layout = QVBoxLayout(dlg)

        info_label = QLabel(
            f"Donor game: {len(donor_entries)} text entries\n"
            f"Current project: {self.project.total} entries "
            f"({current_untranslated} untranslated)")
        dlg_layout.addWidget(info_label)

        cross_version_cb = QCheckBox(
            "Cross-version text matching (use when game versions differ)")
        cross_version_cb.setToolTip(
            "Matches entries by structural position when IDs don't align.\n"
            "May normalise near-duplicate lines — leave OFF for faithful import.")
        dlg_layout.addWidget(cross_version_cb)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        dlg_layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Build structural translation map only if opted in
        text_map = {}
        if cross_version_cb.isChecked():
            try:
                text_map = parser.build_cross_version_map(
                    folder, self.project.project_path)
            except Exception:
                pass  # Fall back to ID-only matching

        stats = self.project.import_from_game_folder(
            donor_entries, swap=swap, text_map=text_map)

        # Refresh UI
        self.trans_table.set_entries(self.project.entries)
        self.event_viewer.set_entries(self.project.entries)
        self.file_tree.load_project(self.project)

        total_imported = stats['by_text'] + stats['imported']
        QMessageBox.information(
            self, "Import Complete",
            f"Imported {total_imported} translations:\n"
            f"  \u2022 {stats['by_text']} matched by structure\n"
            f"  \u2022 {stats['imported']} matched by ID\n"
            f"  \u2022 {stats['identical']} identical (not translated in donor)\n"
            f"  \u2022 {stats['new']} new entries (need translation)\n"
            f"  \u2022 {stats['skipped']} already translated (kept)\n"
        )

    # ── Scan plugin edits ────────────────────────────────────────

    def _scan_plugin_edits(self):
        """Compare a selected original plugins.js vs the project's plugins.js."""
        if not self.project or not self.project.project_path:
            return

        from ..rpgmaker_mv import RPGMakerMVParser
        from .plugin_diff_dialog import PluginDiffDialog
        from ..project_model import TranslationEntry

        parser = RPGMakerMVParser()

        # Try auto-detect first (plugins_original.js as JP backup)
        diffs = parser.diff_plugins(self.project.project_path)

        if not diffs:
            # No backup found or no diffs — ask user to pick the other file
            default_dir = self.project.project_path
            for sub in ("js", os.path.join("www", "js")):
                candidate = os.path.join(self.project.project_path,
                                         sub, "plugins.js")
                if os.path.isfile(candidate):
                    default_dir = os.path.dirname(candidate)
                    break

            other_path, _ = QFileDialog.getOpenFileName(
                self, "Select plugins.js to compare against",
                default_dir,
                "JavaScript Files (*.js);;All Files (*)",
            )
            if not other_path:
                return

            diffs = parser.diff_plugins(self.project.project_path,
                                        other_path=other_path)

        if not diffs:
            QMessageBox.information(
                self, "Scan Plugin Edits",
                "No parameter differences found between\n"
                f"the selected file and the project's plugins.js."
            )
            return

        dlg = PluginDiffDialog(diffs, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        accepted = dlg.accepted_diffs()
        if not accepted:
            return

        # Build set of existing entry IDs to avoid duplicates
        existing_ids = {e.id for e in self.project.entries}

        added = 0
        skipped = 0
        for entry_id, original, translation in accepted:
            if entry_id in existing_ids:
                skipped += 1
                continue
            entry = TranslationEntry(
                id=entry_id,
                file="plugins.js",
                field="plugin_param",
                original=original,
                translation=translation,
                status="translated",
            )
            self.project.entries.append(entry)
            existing_ids.add(entry_id)
            added += 1

        if added:
            # Invalidate cached index so tree view sees new file
            self.project._build_index()
            self.trans_table.set_entries(self.project.entries)
            self.event_viewer.set_entries(self.project.entries)
            self.file_tree.load_project(self.project)

        msg = f"Imported {added} plugin translations."
        if skipped:
            msg += f"\n{skipped} entries skipped (already in project)."
        QMessageBox.information(self, "Scan Plugin Edits", msg)

    # ── Glossary scan from translated game ─────────────────────────

    # Fields worth harvesting as glossary terms (short names / labels)
    _GLOSSARY_SCAN_FIELDS = {
        "Actors.json": ("name", "nickname"),
        "Classes.json": ("name",),
        "Items.json": ("name",),
        "Weapons.json": ("name",),
        "Armors.json": ("name",),
        "Skills.json": ("name",),
        "Enemies.json": ("name",),
        "States.json": ("name",),
        "System.json": ("terms",),
    }

    # ── Vocab.txt support (DazedMTL format) ──────────────────────

    _VOCAB_FILENAMES = ("vocab.txt", "Vocab.txt", "VOCAB.txt")

    @staticmethod
    def _parse_vocab_file(filepath: str) -> tuple[dict, dict]:
        """Parse a DazedMTL-style vocab.txt.

        Format: ``JP (EN)`` or ``JP (EN) - Gender``

        Returns:
            (glossary_dict, gender_dict)
            glossary_dict: {jp_text: en_text}
            gender_dict:   {jp_name: "female"|"male"|"unknown"}
        """
        import re
        pattern = re.compile(
            r'^(.+?)\s*\((.+?)\)(?:\s*-\s*(Female|Male))?\s*$',
            re.IGNORECASE,
        )
        glossary = {}
        genders = {}
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("```"):
                    continue
                # Skip description / header lines
                if line.startswith("Here are") or line.startswith("\\N["):
                    continue
                m = pattern.match(line)
                if not m:
                    continue
                jp = m.group(1).strip()
                en = m.group(2).strip()
                gender = m.group(3)
                if jp and en:
                    glossary[jp] = en
                    if gender:
                        genders[jp] = gender.lower()
        return glossary, genders

    def _check_vocab_file(self, project_path: str) -> bool:
        """Auto-detect vocab.txt in project folder and offer to import.

        Returns True if vocab was found and the user accepted (so caller
        can skip the default glossary prompt).
        """
        vocab_path = None
        for name in self._VOCAB_FILENAMES:
            candidate = os.path.join(project_path, name)
            if os.path.isfile(candidate):
                vocab_path = candidate
                break
        if not vocab_path:
            return False

        try:
            glossary, genders = self._parse_vocab_file(vocab_path)
        except (OSError, UnicodeDecodeError):
            return False

        if not glossary:
            return False

        reply = QMessageBox.question(
            self, "Vocab File Detected",
            f"Found {os.path.basename(vocab_path)} with {len(glossary)} terms"
            + (f" and {len(genders)} character genders" if genders else "")
            + ".\n\nLoad into project glossary instead of default glossary?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False

        # Merge vocab into the project glossary (existing terms are kept),
        # then backfill project terms
        for jp, en in glossary.items():
            self.project.glossary.setdefault(jp, en)
        # Re-add auto-glossary from already-translated DB entries on top
        backfilled = self._backfill_db_glossary()

        # Store gender info for actor detection
        if genders:
            for jp_name, gender in genders.items():
                en_name = glossary.get(jp_name, jp_name)
                self._vocab_genders[jp_name] = gender
                self._vocab_genders[en_name] = gender

        self.statusbar.showMessage(
            f"Loaded {len(glossary)} vocab terms"
            + (f" + {backfilled} project terms" if backfilled else "")
            + (f" + {len(genders)} genders" if genders else ""),
            5000,
        )
        return True

    def _load_vocab_file(self):
        """Manually load a vocab.txt file."""
        if not self.project:
            return

        filepath, _ = QFileDialog.getOpenFileName(
            self, "Select Vocab File", "",
            "Text Files (*.txt);;All Files (*)"
        )
        if not filepath:
            return

        try:
            glossary, genders = self._parse_vocab_file(filepath)
        except (OSError, UnicodeDecodeError) as e:
            QMessageBox.warning(self, "Can't Read Vocab File",
                                f"The file could not be read:\n{e}")
            return

        if not glossary:
            QMessageBox.information(
                self, "No Terms Found",
                "No glossary terms found in that file.\n"
                "Expected format: Japanese (English) or Japanese (English) - Gender"
            )
            return

        added = 0
        for jp, en in glossary.items():
            if jp not in self.project.glossary:
                self.project.glossary[jp] = en
                added += 1

        if genders:
            if not hasattr(self, "_vocab_genders"):
                self._vocab_genders = {}
            for jp_name, gender in genders.items():
                en_name = glossary.get(jp_name, jp_name)
                self._vocab_genders[jp_name] = gender
                self._vocab_genders[en_name] = gender

        self._rebuild_glossary()

        QMessageBox.information(
            self, "Vocab Loaded",
            f"Added {added} terms to project glossary"
            + (f" + {len(genders)} character genders" if genders else "")
            + f"\n({len(glossary) - added} already existed)"
        )

    def _export_vocab_file(self):
        """Export glossary as a DazedMTL-compatible vocab.txt."""
        if not self.project:
            return

        # Merge general + project glossary (project overrides general)
        merged = {}
        if hasattr(self, "_general_glossary") and self._general_glossary:
            merged.update(self._general_glossary)
        if self.project.glossary:
            merged.update(self.project.glossary)

        if not merged:
            QMessageBox.information(
                self, "Export Vocab",
                "No glossary terms to export."
            )
            return

        # Build gender lookup from actor_genders + actor entries
        genders = {}
        if self.project.actor_genders:
            # actor_genders: {actor_id: "female"/"male"/"unknown"}
            for entry in self.project.entries:
                if entry.file == "Actors.json" and entry.field == "name":
                    # Extract actor ID from entry.id
                    # Format: Actors.json/n/name
                    import re as _re
                    m = _re.search(r'/(\d+)/', entry.id)
                    if m:
                        actor_id = int(m.group(1))
                        gender = self.project.actor_genders.get(actor_id)
                        if gender and gender != "unknown":
                            jp_name = entry.original
                            en_name = entry.translation or jp_name
                            genders[jp_name] = gender.capitalize()
                            genders[en_name] = gender.capitalize()

        # Default path
        default_dir = self.project.project_path or ""
        default_path = os.path.join(default_dir, "vocab.txt") if default_dir else "vocab.txt"

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Vocab File", default_path,
            "Text Files (*.txt);;All Files (*)",
        )
        if not path:
            return

        lines = []
        for jp, en in sorted(merged.items()):
            gender = genders.get(jp, "")
            if gender:
                lines.append(f"{jp} ({en}) - {gender}")
            else:
                lines.append(f"{jp} ({en})")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        QMessageBox.information(
            self, "Export Vocab",
            f"Exported {len(lines)} terms to:\n{path}"
        )

    def _scan_game_for_glossary(self):
        """Scan a translated game folder and harvest JP→EN pairs for glossary."""
        if not self.project:
            return

        folder = QFileDialog.getExistingDirectory(
            self, "Select Translated Game Folder"
        )
        if not folder:
            return

        from ..rpgmaker_mv import RPGMakerMVParser, _has_japanese

        parser = RPGMakerMVParser()
        try:
            donor_entries = parser.load_project_raw(folder)
        except FileNotFoundError as e:
            QMessageBox.warning(self, "Can't Read Game Folder", str(e))
            return
        except Exception as e:
            QMessageBox.warning(
                self, "Can't Read Game Folder",
                f"Failed to read the game folder:\n{e}")
            return

        if not donor_entries:
            QMessageBox.warning(
                self, "No Entries",
                "No text entries found in that game folder."
            )
            return

        # Build lookup from current project: entry_id → JP original
        jp_by_id = {e.id: e.original for e in self.project.entries}

        # Match donor entries against project entries to find JP→EN pairs
        candidates = []
        seen = set()
        for donor in donor_entries:
            jp_text = jp_by_id.get(donor.id)
            if not jp_text:
                continue
            en_text = donor.original  # "original" in raw parse = the EN text
            if not en_text or not jp_text:
                continue
            jp_text = jp_text.strip()
            en_text = en_text.strip()
            if not jp_text or not en_text:
                continue
            # Skip if identical (wasn't translated)
            if jp_text == en_text:
                continue
            # JP must contain Japanese, EN must not
            if not _has_japanese(jp_text) or _has_japanese(en_text):
                continue
            # DB name fields and map names are always glossary-worthy
            is_db_field = False
            fields = self._GLOSSARY_SCAN_FIELDS.get(donor.file)
            if fields and donor.field in fields:
                is_db_field = True
            is_map_name = (
                donor.file.startswith("Map") and donor.file.endswith(".json")
                and donor.field == "displayName"
            )
            if not is_db_field and not is_map_name:
                continue
            # Skip if already in general glossary
            if jp_text in self._general_glossary:
                continue
            # Deduplicate
            if jp_text in seen:
                continue
            seen.add(jp_text)
            candidates.append((jp_text, en_text))

        if not candidates:
            QMessageBox.information(
                self, "No New Terms",
                "No new glossary candidates found.\n"
                "All matching terms are already in your general glossary."
            )
            return

        from .glossary_scan_dialog import GlossaryScanDialog

        dlg = GlossaryScanDialog(candidates, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_pairs()
        if not selected:
            return

        # Add to general glossary
        for jp, en in selected:
            self._general_glossary[jp] = en

        self._rebuild_glossary()
        self._save_settings()

        QMessageBox.information(
            self, "Glossary Updated",
            f"Added {len(selected)} terms to your general glossary."
        )

    def _scan_project_for_glossary(self):
        """Scan current project's translations for glossary candidates."""
        if not self.project:
            return

        from ..rpgmaker_mv import _has_japanese

        candidates = []
        seen = set()
        for e in self.project.entries:
            if e.status not in ("translated", "reviewed"):
                continue
            jp = e.original.strip()
            en = e.translation.strip() if e.translation else ""
            if not jp or not en or jp == en:
                continue
            if not _has_japanese(jp) or _has_japanese(en):
                continue
            # DB name fields and map names are always glossary-worthy
            is_db_field = False
            fields = self._GLOSSARY_SCAN_FIELDS.get(e.file)
            if fields and e.field in fields:
                is_db_field = True
            is_map_name = (
                e.file.startswith("Map") and e.file.endswith(".json")
                and e.field == "displayName"
            )
            if not is_db_field and not is_map_name:
                continue
            if jp in self._general_glossary:
                continue
            if jp in seen:
                continue
            seen.add(jp)
            candidates.append((jp, en))

        if not candidates:
            QMessageBox.information(
                self, "No New Terms",
                "No new glossary candidates found.\n"
                "All qualifying terms are already in your general glossary."
            )
            return

        from .glossary_scan_dialog import GlossaryScanDialog

        dlg = GlossaryScanDialog(candidates, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_pairs()
        if not selected:
            return

        for jp, en in selected:
            self._general_glossary[jp] = en

        self._rebuild_glossary()
        self._save_settings()

        QMessageBox.information(
            self, "Glossary Updated",
            f"Added {len(selected)} terms to your general glossary."
        )

    def _stop_translation(self):
        """Cancel the running batch translation."""
        self._batch_all_chained = False  # Don't auto-chain to dialogue
        self._user_stopped = True        # No auto-retranslate / chaining on finish
        self.engine.cancel()
        self.stop_action.setEnabled(False)

    def _check_unwrapped_entries(self, translated: list) -> int:
        """Count dialog entries with lines exceeding chars_per_line."""
        max_chars = self.plugin_analyzer.chars_per_line
        count = 0
        for entry in translated:
            if entry.field not in ("dialog", "dialogue", "scroll_text"):
                continue
            for line in entry.translation.split("\n"):
                vis_len = self.text_processor._visual_length(line)
                if vis_len > max_chars:
                    count += 1
                    break  # count each entry once
        return count

    def _export_to_game(self):
        """Write translations back to game files."""
        if not self.project.project_path:
            QMessageBox.warning(
                self, "No Game Folder",
                "This project has no game folder on disk.\n\n"
                "Open the game folder with Project \u203a Open Project\u2026 first.")
            return

        translated = [e for e in self.project.entries if e.status in ("translated", "reviewed")]
        if not translated:
            QMessageBox.information(self, "Nothing to Export", "No translated entries to export.")
            return

        # Check for unwrapped lines (only engines with plugin word wrap)
        unwrapped = self._check_unwrapped_entries(translated) if self.handler.has_plugin_system else 0
        if unwrapped > 0:
            result = QMessageBox.warning(
                self, "Lines May Overflow",
                f"{unwrapped} dialogue entries have lines longer than "
                f"{self.plugin_analyzer.chars_per_line} characters and may "
                f"overflow the text box in-game.\n\n"
                f"Apply word wrap before exporting?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if result == QMessageBox.StandardButton.Cancel:
                return
            if result == QMessageBox.StandardButton.Yes:
                count = self.text_processor.process_all(self.project.entries)
                self.trans_table.refresh()
                self.statusBar().showMessage(
                    f"Word wrap applied to {count} entries.", 5000)

        # Confirmation dialog with checkbox
        dlg = QDialog(self)
        dlg.setWindowTitle("Apply Translation to Game")
        layout = QVBoxLayout(dlg)
        backup_name = self.handler.backup_description
        layout.addWidget(QLabel(
            f"This will overwrite {len(set(e.file for e in translated))} "
            f"file(s) in:\n{self.project.project_path}\n\n"
            f"Original files will be backed up to {backup_name} "
            f"(first export only)."
        ))
        checkbox = QCheckBox("I understand this will modify my game files")
        layout.addWidget(checkbox)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setEnabled(False)
        checkbox.toggled.connect(ok_btn.setEnabled)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            # Sync font setting to parser before export
            if hasattr(self.parser, 'game_font'):
                self.parser.game_font = self._game_font
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            self.statusbar.showMessage("Writing translations to game files…")
            self.statusbar.repaint()  # paint now; no input re-entrancy
            try:
                self.handler.save_project(
                    self.project.project_path, self.project.entries)
            finally:
                QApplication.restoreOverrideCursor()
                self.statusbar.clearMessage()

            # MV/MZ post-export: plugin injection
            plugin_msg = ""
            if self.handler.has_plugin_system:
                has_ww_tags = any(
                    e.translation and "<WordWrap>" in e.translation
                    for e in translated
                )
                if has_ww_tags and not self.plugin_analyzer.has_wordwrap_plugin:
                    cpl = self.plugin_analyzer.chars_per_line
                    if self.parser.inject_wordwrap_plugin(
                            self.project.project_path, max_chars=cpl):
                        plugin_msg = f"\nWord wrap plugin injected ({cpl} chars/line)."

                if self._disable_splash:
                    if self.parser.disable_splash_plugin(self.project.project_path):
                        plugin_msg += "\n'Made with RPG Maker' splash disabled."

                if self._show_translation_splash:
                    try:
                        from ..splash_generator import inject_splash
                        if inject_splash(self.project.project_path):
                            plugin_msg += "\nTranslation splash screen injected."
                    except Exception as exc:
                        log.warning("Splash injection failed: %s", exc)

            # RM2K/2K3: auto-copy EasyRPG Player for locale-independent play
            if self.handler.key == "rpgmaker_2k":
                easyrpg_msg = self._ensure_easyrpg_player(
                    self.project.project_path)
                if easyrpg_msg:
                    plugin_msg += "\n" + easyrpg_msg

            QMessageBox.information(
                self, "Export Complete",
                self.handler.get_export_message(len(translated)) + plugin_msg
            )
            if not self._wizard_active:
                self.pipeline_bar.mark_done("export")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", str(e))

    # ── EasyRPG Player auto-download ─────────────────────────────

    _EASYRPG_URL = ("https://easyrpg.org/downloads/player/0.8.1.1/"
                    "easyrpg-player-0.8.1.1-windows-x64.zip")

    def _ensure_easyrpg_player(self, game_dir: str) -> str:
        """Copy EasyRPG Player into the game folder for locale-free play.

        Downloads on first use, caches in tools/ next to main.py.
        Returns a status message string, or empty if already present.
        """
        dest = os.path.join(game_dir, "Player.exe")
        if os.path.isfile(dest):
            return ""

        # Check local cache
        tools_dir = os.path.join(app_dir(), "tools")
        cached = os.path.join(tools_dir, "easyrpg-player.exe")

        if not os.path.isfile(cached):
            # Download
            try:
                import io
                import zipfile
                import urllib.request
                log.info("Downloading EasyRPG Player…")
                with urllib.request.urlopen(self._EASYRPG_URL, timeout=30) as r:
                    zip_data = r.read()
                with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                    # Find the exe inside the zip
                    exe_names = [n for n in zf.namelist()
                                 if n.lower().endswith(".exe")]
                    if not exe_names:
                        log.warning("No exe found in EasyRPG zip")
                        return ""
                    os.makedirs(tools_dir, exist_ok=True)
                    with open(cached, "wb") as f:
                        f.write(zf.read(exe_names[0]))
                log.info("EasyRPG Player cached at %s", cached)
            except Exception as exc:
                log.warning("Failed to download EasyRPG Player: %s", exc)
                return ""

        # Copy to game folder
        try:
            shutil.copy2(cached, dest)
            return "EasyRPG Player.exe added — run it instead of RPG_RT.exe."
        except Exception as exc:
            log.warning("Failed to copy EasyRPG Player: %s", exc)
            return ""

    def _restore_originals(self):
        """Restore the original Japanese game files from backup."""
        if not self.project or not self.project.project_path:
            return

        # Engines with parser-based restore (everything except MV/MZ)
        if hasattr(self.handler.parser, 'restore_originals') and self._project_type not in ("rpgmaker_mv", "rpgmaker_mz"):
            reply = QMessageBox.question(
                self, "Restore Original Game Files",
                "This will overwrite the game's translated files with the "
                "original Japanese versions from backup.\n\n"
                "Your translation state is NOT affected \u2014 only the game "
                "files.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            try:
                self.handler.restore_originals(self.project.project_path)
                QMessageBox.information(
                    self, "Restore Complete",
                    self.handler.get_restore_message()
                )
            except FileNotFoundError as e:
                QMessageBox.information(self, "No Backup Found", str(e))
            except Exception as e:
                QMessageBox.critical(self, "Restore Failed", str(e))
            return

        # MV/MZ: directory-based restore with atomic swap
        data_dir = self.parser._find_data_dir(self.project.project_path)
        backup_dir = data_dir + "_original" if data_dir else None

        if not data_dir:
            QMessageBox.warning(
                self, "Data Folder Not Found",
                "Could not find the game's data/ folder.\n\n"
                "Make sure the project points at the game's top-level folder.")
            return

        if not backup_dir or not os.path.isdir(backup_dir):
            QMessageBox.information(
                self, "No Backup Found",
                "No data_original/ backup exists. Export to game first to create one."
            )
            return

        reply = QMessageBox.question(
            self, "Restore Originals",
            "This will overwrite the current game files with the original "
            "Japanese versions from backup.\n\n"
            "Your translation state is NOT affected — only the game files.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            import shutil
            # Atomic swap: rename current → temp, copy backup → data, delete temp
            temp_dir = data_dir + "_restoring"
            # Clean up leftover temp dir from a previously interrupted restore
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
            os.rename(data_dir, temp_dir)
            try:
                shutil.copytree(backup_dir, data_dir)
            except Exception:
                # Copy failed — restore the original data dir
                os.rename(temp_dir, data_dir)
                raise
            shutil.rmtree(temp_dir)

            # RPG Maker: also restore plugins.js if backup exists
            plugins_restored = False
            plugins_path = self.parser._find_plugins_file(self.project.project_path)
            if plugins_path:
                backup_path = os.path.join(
                    os.path.dirname(plugins_path),
                    os.path.basename(plugins_path).replace("plugins.", "plugins_original.")
                )
                if os.path.isfile(backup_path):
                    shutil.copy2(backup_path, plugins_path)
                    plugins_restored = True

            # Clean up injected word wrap plugin
            self.parser.remove_wordwrap_plugin(self.project.project_path)
            self.plugin_analyzer.inject_wordwrap = False

            msg = "Original Japanese files have been restored.\n"
            if plugins_restored:
                msg += "plugins.js has also been restored from backup.\n"
            msg += "The backups are still available."
            QMessageBox.information(self, "Restore Complete", msg)
        except Exception as e:
            QMessageBox.critical(self, "Restore Failed", str(e))

    # ── Open in RPG Maker ─────────────────────────────────────────

    def _open_in_rpgmaker(self):
        """Create a workspace project with directory junctions and open in RPG Maker."""
        if not self.project or not self.project.project_path:
            QMessageBox.warning(self, "No Project Open",
                                "Open a game folder first (Project \u203a Open Project\u2026).")
            return

        project_path = self.project.project_path

        # Find content root (where data/ and js/ live)
        content_root = self.parser.find_content_root(project_path)
        if not content_root:
            QMessageBox.warning(
                self, "Data Folder Not Found",
                "Could not find game data directory.\n"
                "Make sure the project has a data/ folder."
            )
            return

        # Detect MV vs MZ
        engine = self.parser.detect_engine(project_path)
        if not engine:
            QMessageBox.warning(
                self, "Unknown RPG Maker Version",
                "Could not detect RPG Maker version.\n"
                "Expected rpg_core.js (MV) or rmmz_core.js (MZ) in the js/ folder."
            )
            return

        # Create workspace folder
        workspace = os.path.join(project_path, "_rpgmaker_workspace")
        os.makedirs(workspace, exist_ok=True)

        # Create directory junctions for large asset folders
        junction_dirs = ["data", "Data", "img", "audio", "js", "fonts", "icon", "movies"]
        for dirname in junction_dirs:
            source = os.path.join(content_root, dirname)
            if not os.path.isdir(source):
                continue
            link = os.path.join(workspace, dirname)
            if os.path.exists(link):
                continue  # Junction already exists
            try:
                # mklink /J creates a directory junction (no admin needed)
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", link, source],
                    capture_output=True, text=True, check=True,
                )
            except subprocess.CalledProcessError as e:
                QMessageBox.critical(
                    self, "Junction Error",
                    f"Failed to create directory junction for {dirname}:\n{e.stderr}"
                )
                return

        # Copy small files
        for filename in ("index.html", "package.json"):
            src = os.path.join(content_root, filename)
            dst = os.path.join(workspace, filename)
            if os.path.isfile(src) and not os.path.isfile(dst):
                shutil.copy2(src, dst)

        # Create MZ-specific empty dirs if needed
        if engine == "mz":
            for dirname in ("css", "effects"):
                d = os.path.join(workspace, dirname)
                if not os.path.isdir(d):
                    os.makedirs(d, exist_ok=True)

        # Create the marker file
        if engine == "mv":
            marker = os.path.join(workspace, "Game.rpgproject")
            marker_content = "RPGMV 1.6.3"
        else:
            marker = os.path.join(workspace, "game.rmmzproject")
            marker_content = "RPGMZ 1.10.0"

        if not os.path.isfile(marker):
            with open(marker, "w", encoding="utf-8") as f:
                f.write(marker_content)

        # Open in RPG Maker
        engine_label = "RPG Maker MV" if engine == "mv" else "RPG Maker MZ"
        try:
            os.startfile(marker)
            self.statusbar.showMessage(
                f"Opening in {engine_label}... Workspace: _rpgmaker_workspace/", 10000
            )
        except OSError:
            QMessageBox.information(
                self, "Open Manually",
                f"No application is associated with .{'rpgproject' if engine == 'mv' else 'rmmzproject'} files.\n\n"
                f"Please open this file manually in {engine_label}:\n\n"
                f"{marker}"
            )

    def _open_settings(self):
        """Open the settings dialog."""
        # Capture current engine settings before showing dialog
        self._save_engine_settings()
        # The dialog's main model field is the global model; per-engine
        # overrides live on its Engines tab and are re-applied afterwards.
        self.client.model = self._global_model
        dlg = SettingsDialog(
            self.client, self, parser=self.parser, dark_mode=self._dark_mode,
            plugin_analyzer=self.plugin_analyzer, engine=self.engine,
            export_review_file=self._export_review_file,
            disable_splash=self._disable_splash,
            show_translation_splash=self._show_translation_splash,
            engine_overrides=self._engine_overrides,
            engine_handlers=self._engine_handlers,
        )
        dlg._active_engine_key = self._project_type
        if dlg.exec():
            # Apply dark mode if changed
            if dlg.dark_mode != self._dark_mode:
                self._dark_mode = dlg.dark_mode
                self._apply_dark_mode()
                self.trans_table.set_dark_mode(self._dark_mode)
            self._export_review_file = dlg.export_review_file
            self._disable_splash = dlg.disable_splash
            self._show_translation_splash = dlg.show_translation_splash
            if hasattr(dlg, 'game_font'):
                self._game_font = dlg.game_font
            # Capture global values before per-engine overrides are applied
            self._global_model = self.client.model
            self._global_wordwrap = getattr(
                self.plugin_analyzer, "_manual_chars_per_line", 0)
            # Apply updated engine overrides from dialog
            self._engine_overrides = dlg.engine_overrides
            self._apply_engine_settings(self._project_type)
            self._save_settings()
            # Preload model into VRAM if model changed (avoids cold-start delay)
            if not self.client.is_cloud:
                self._preload_model()
        else:
            # Cancelled: restore the active engine's model override
            self._apply_engine_settings(self._project_type)

    def _open_glossary(self):
        """Open the standalone glossary editor."""
        dlg = GlossaryDialog(self, self._general_glossary, self.project.glossary)
        if dlg.exec():
            self._general_glossary = dlg.general_glossary
            self.project.glossary = dlg.project_glossary
            self._rebuild_glossary()
            self._save_settings()

    # ── Filtering ──────────────────────────────────────────────────

    def _filter_by_file(self, filename: str):
        """Show only entries from a specific file."""
        # Script Strings virtual category
        if filename == "__SCRIPT_ALL__":
            entries = [e for e in self.project.entries
                       if e.field == "script_variable"]
        elif filename.startswith("__SCRIPT__"):
            real_file = filename[len("__SCRIPT__"):]
            entries = [e for e in self.project.get_entries_for_file(real_file)
                       if e.field == "script_variable"]
        else:
            entries = self.project.get_entries_for_file(filename)
        self.trans_table.filter_by_file(entries)

    def _show_all_entries(self):
        """Show all entries."""
        self.trans_table.clear_file_filter()

    # ── Engine signal handlers ─────────────────────────────────────

    def _on_error(self, entry_id: str, error_msg: str):
        """Handle translation error for a single entry."""
        self.statusbar.showMessage(f"Error translating {entry_id}: {error_msg}", 5000)
        self.queue_panel.mark_entry_error(entry_id, error_msg)

    def _on_server_down(self, reason: str):
        """Translation engine detected the server is down. Pause and prompt."""
        if self._closing or not self._is_batch_project_current():
            return
        self._autosave()
        # Don't pile multiple dialogs on top of each other if more signals fire
        if self._server_down_dialog_open:
            return
        self._server_down_dialog_open = True
        self._finished_during_server_down = False
        # Resume replays the engine's last job (same mode + entries)
        can_resume = bool(getattr(self.engine, "last_job", None))
        resume = False
        try:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("LLM Server Unavailable")
            box.setText(
                "Translation paused — the LLM server appears to be down.\n\n"
                "Multiple connection errors fired in a short window so we "
                "stopped to avoid grinding through failed batches.\n\n"
                f"Last error: {reason[:200]}\n\n"
                "Progress has been auto-saved. Restart Ollama (or check your "
                "API key/connection)"
                + (" and click Resume to continue from where we left off."
                   if can_resume else ", then start the run again.")
            )
            resume_btn = None
            if can_resume:
                resume_btn = box.addButton("Resume Translation", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Stop", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            resume = resume_btn is not None and box.clickedButton() is resume_btn
        finally:
            self._server_down_dialog_open = False

        finished_already = self._finished_during_server_down
        self._finished_during_server_down = False
        if resume:
            # Replays the same job once the cancelled workers have exited —
            # completed entries are skipped automatically, and no finished
            # signal is emitted in between.
            self.engine.resume_last_job()
        else:
            self._stop_translation()
            if finished_already:
                self._on_batch_finished()

    def _is_batch_project_current(self) -> bool:
        """False if the running batch belongs to a project that was replaced."""
        return self._batch_project is None or self._batch_project is self.project

    def _deferred_start(self, starter, delay_ms: int = 500):
        """Start a follow-up run after a short delay; reset the UI if it can't.

        Aborts if the user pressed Stop, the window is closing, or the
        project changed in the meantime.
        """
        project = self.project

        def run():
            if self._user_stopped or self._closing or self.project is not project:
                self._user_stopped = False
                self._is_cleanup_retranslation = False
                self._batch_all_chained = False
                self._reset_batch_ui()
                return
            if not starter():
                self._is_cleanup_retranslation = False
                self._reset_batch_ui()

        QTimer.singleShot(delay_ms, run)

    def _on_batch_finished(self):
        """Handle batch translation/polish completing."""
        if self._closing:
            return
        # Results of a batch from a replaced project must not touch this one
        if not self._is_batch_project_current():
            self._batch_project = None
            self._reset_batch_ui()
            return
        # Server-down dialog still open: decide after the user answers
        if self._server_down_dialog_open:
            self._finished_during_server_down = True
            return

        # When wizard controls the pipeline, let it handle progression
        if self._wizard_active:
            self._set_batch_running(False)
            return

        stopped = self._user_stopped
        self._user_stopped = False
        run_kind = self._run_kind
        is_cleanup_pass = self._is_cleanup_retranslation
        self._is_cleanup_retranslation = False

        # Final pass: restore any control codes the LLM dropped
        codes_fixed = self._restore_missing_codes()

        # Run automated post-processing cleanup
        from ..post_processor import run_post_processing
        pp_result = run_post_processing(self.project.entries,
                                        glossary=self.project.glossary,
                                        project_type=self._project_type)

        # Only a completed (not stopped) full batch — or the cleanup
        # retranslation it spawned — continues the pipeline.
        completed_batch = not stopped and (
            run_kind == "batch" or (run_kind == "selected" and is_cleanup_pass))

        # Auto-retranslate flagged entries (one pass only, only those entries)
        if (completed_batch and not is_cleanup_pass
                and pp_result.retranslate_ids):
            self._autosave()
            ids = list(pp_result.retranslate_ids)
            self.statusbar.showMessage(
                f"Cleanup found {pp_result.total_entries_fixed} issues, "
                f"retranslating {len(ids)} broken entries...",
                5000,
            )
            self._is_cleanup_retranslation = True
            self._deferred_start(lambda: self._translate_selected(ids))
            return

        mode = self._current_batch_mode
        chained = self._batch_all_chained

        # Batch All: DB phase done → rebuild glossary → start dialogue phase
        if completed_batch and chained and mode == "db":
            self._batch_all_chained = False
            self._backfill_db_glossary()
            self._rebuild_glossary()
            self._autosave()
            self.file_tree.refresh_stats(self.project)
            self.pipeline_bar.mark_done("db")

            # Check if there are dialogue entries left
            untranslated_dialogue = [
                e for e in self.project.entries
                if e.status == "untranslated" and e.file not in self.handler.db_files
            ]
            if untranslated_dialogue:
                db_done = sum(
                    1 for e in self.project.entries
                    if e.file in self.handler.db_files
                    and e.status in ("translated", "reviewed")
                )
                glossary_size = len(self.client.glossary)
                self.statusbar.showMessage(
                    f"DB phase done ({db_done} entries). "
                    f"Glossary: {glossary_size} terms. "
                    f"Starting dialogue phase ({len(untranslated_dialogue)} entries)...",
                    5000,
                )
                # Small delay so the user sees the status message
                self._deferred_start(lambda: self._start_batch(mode="dialogue"))
                return
            # else: no dialogue left, fall through to normal finish
        if not completed_batch:
            self._batch_all_chained = False

        self._reset_batch_ui()

        # Update pipeline bar
        if completed_batch:
            if mode in ("db", "all"):
                self.pipeline_bar.mark_done("db")
            if mode in ("dialogue", "all"):
                self.pipeline_bar.mark_done("dialogue")
        if stopped:
            msg = "Stopped"
        elif run_kind == "polish":
            msg = "Polish complete"
        else:
            msg = "Batch complete"
        msg += f" — {self.project.translated_count}/{self.project.total} translated"
        if codes_fixed:
            msg += f" ({codes_fixed} codes restored)"
        if pp_result.total_entries_fixed:
            msg += f" ({pp_result.total_entries_fixed} cleaned up)"
        # Show cost for cloud providers
        cost_str = self.client.format_session_cost()
        if cost_str:
            msg += f" | {cost_str}"
        self.statusbar.showMessage(msg, 15000)
        # After a full batch, warn about name collisions (different JP → same EN)
        if completed_batch and mode in ("db", "all", "dialogue"):
            self._warn_name_collisions()
        # Auto-export review file if enabled
        if self._export_review_file and not stopped:
            self._auto_export_review()

    def _is_name_entry(self, entry) -> bool:
        """True for DB name-type fields and map display names."""
        fields = self.handler.auto_glossary_fields.get(entry.file)
        return bool((fields and entry.field in fields) or (
            entry.file.startswith("Map") and entry.field == self._AUTO_GLOSSARY_MAP_FIELD
        ))

    def _warn_name_collisions(self):
        """Detect different JP names that translated to the same EN text.

        Shows a warning dialog listing collisions so the user can fix them
        before proceeding to dialogue translation.
        """
        # Build reverse map: EN translation → list of (JP original, file, field)
        en_to_sources: dict[str, list[tuple[str, str, str]]] = {}
        for entry in self.project.entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            if not self._is_name_entry(entry) or not entry.translation:
                continue
            en = entry.translation.strip()
            if not en:
                continue
            en_to_sources.setdefault(en, []).append(
                (entry.original, entry.file, entry.field)
            )

        # Find collisions: same EN text from different JP originals
        collisions = []
        for en, sources in en_to_sources.items():
            unique_jp = set(src[0] for src in sources)
            if len(unique_jp) > 1:
                collisions.append((en, sources))

        if not collisions:
            return

        # Build readable message (limit to first 15 to avoid huge dialog)
        lines = []
        for en, sources in sorted(collisions)[:15]:
            unique_sources = {}
            for jp, file, field in sources:
                unique_sources.setdefault(jp, []).append(f"{file}:{field}")
            detail_parts = [f'  "{jp}" ({", ".join(locs)})' for jp, locs in unique_sources.items()]
            lines.append(f'"{en}" ← different JP sources:\n' + "\n".join(detail_parts))

        extra = ""
        if len(collisions) > 15:
            extra = f"\n... and {len(collisions) - 15} more collision(s)"

        # Save collisions to file for later review
        if self.project.project_path:
            col_path = os.path.join(
                self.project.project_path, "_name_collisions.txt")
            try:
                with open(col_path, "w", encoding="utf-8") as f:
                    f.write(f"# Name Collisions Report\n")
                    f.write(f"# {len(collisions)} collision(s) found\n")
                    f.write(f"# Different JP terms translated to the same EN text.\n")
                    f.write(f"# Fix these before running Batch Dialogue.\n\n")
                    for en, sources in sorted(collisions):
                        f.write(f'EN: "{en}"\n')
                        unique_sources: dict[str, list[str]] = {}
                        for jp, file, field in sources:
                            unique_sources.setdefault(jp, []).append(
                                f"{file}:{field}")
                        for jp, locs in unique_sources.items():
                            f.write(f'  JP: "{jp}" ({", ".join(locs)})\n')
                        f.write("\n")
            except OSError:
                pass

        # Collect colliding entry IDs for actions
        colliding_ids: list[str] = []
        for entry in self.project.entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            if not entry.translation or not self._is_name_entry(entry):
                continue
            tl = entry.translation.strip()
            if any(tl == en for en, _ in collisions):
                colliding_ids.append(entry.id)

        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QScrollArea, QWidget
        dlg = QDialog(self)
        dlg.setWindowTitle("Name Collisions Detected")
        dlg.setMinimumSize(640, 480)
        v = QVBoxLayout(dlg)

        header = QLabel(
            f"Found <b>{len(collisions)}</b> name collision(s) — different "
            f"Japanese terms translated to the same English text.<br><br>"
            f"Pick an action below."
        )
        header.setWordWrap(True)
        v.addWidget(header)

        body = QLabel("\n\n".join(lines) + extra)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        wrap = QWidget(); wl = QVBoxLayout(wrap); wl.addWidget(body); wl.addStretch()
        scroll.setWidget(wrap)
        v.addWidget(scroll, 1)

        v.addWidget(QLabel("Full list saved to _name_collisions.txt"))

        # Action buttons
        row = QHBoxLayout()
        btn_view = QPushButton("View in Table")
        btn_auto = QPushButton("Auto-fix (add disambiguator)")
        btn_retry = QPushButton("Retranslate Now")
        btn_close = QPushButton("Close")
        for b in (btn_view, btn_auto, btn_retry, btn_close):
            row.addWidget(b)
        v.addLayout(row)

        result = {"action": None}

        def _view():
            result["action"] = "view"
            dlg.accept()

        def _auto():
            result["action"] = "auto"
            dlg.accept()

        def _retry():
            result["action"] = "retry"
            dlg.accept()

        btn_view.clicked.connect(_view)
        btn_auto.clicked.connect(_auto)
        btn_retry.clicked.connect(_retry)
        btn_close.clicked.connect(dlg.reject)
        dlg.exec()

        action = result["action"]
        if action == "view":
            self._filter_table_to_ids(colliding_ids)
        elif action == "auto":
            self._auto_fix_collisions(collisions)
        elif action == "retry":
            self._retranslate_collisions(colliding_ids)

    def _filter_table_to_ids(self, entry_ids: list[str]):
        """Filter the translation table to show only the given entry IDs."""
        if not entry_ids:
            return
        id_set = set(entry_ids)
        self.trans_table.set_id_filter(id_set)
        # Clear file tree selection so the ID filter takes precedence
        if hasattr(self.file_tree, "tree"):
            self.file_tree.tree.clearSelection()
        self.statusBar().showMessage(
            f"Showing {len(id_set)} colliding entries", 8000
        )

    def _auto_fix_collisions(self, collisions: list):
        """Append disambiguators to colliding translations to make them unique.

        For each collision group ("Powerful Vibrator" → 3 different JP), keep
        the first as-is and add " (2)", " (3)" to the rest.
        """
        # Build EN → list of (entry, jp) for fast lookup
        en_to_entries: dict[str, list] = {}
        for entry in self.project.entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            # Same filter as collision detection — never touch dialogue
            if not entry.translation or not self._is_name_entry(entry):
                continue
            tl = entry.translation.strip()
            en_to_entries.setdefault(tl, []).append(entry)

        fixed = 0
        for en, _ in collisions:
            entries = en_to_entries.get(en, [])
            # Group by original JP — entries with same JP keep same EN
            seen_jp = {}
            for entry in entries:
                if entry.original not in seen_jp:
                    seen_jp[entry.original] = []
                seen_jp[entry.original].append(entry)
            # Keep first JP group as-is, suffix the rest
            counter = 2
            for i, (jp, group) in enumerate(seen_jp.items()):
                if i == 0:
                    continue
                suffix = f" ({counter})"
                for entry in group:
                    entry.translation = en + suffix
                    fixed += 1
                counter += 1

        self._autosave()
        self.trans_table.refresh()
        self.statusBar().showMessage(
            f"Auto-fixed {fixed} colliding entries with disambiguator suffixes",
            8000,
        )

    def _retranslate_collisions(self, entry_ids: list[str]):
        """Queue colliding entries for retranslation with a uniqueness hint."""
        id_set = set(entry_ids)
        targets = [e for e in self.project.entries if e.id in id_set]
        if not targets:
            return
        # Reset to untranslated so batch picks them up
        for entry in targets:
            entry.status = "untranslated"
        self._autosave()
        self.trans_table.refresh()
        QMessageBox.information(
            self, "Retranslation Queued",
            f"{len(targets)} entries marked untranslated. Run Batch DB or "
            f"Batch All to retranslate them.",
        )

    def _is_polish_run(self) -> bool:
        """True while a grammar-polish run is active (ours or the wizard's)."""
        if self._run_kind == "polish":
            return True
        return any(getattr(w, "mode", "") == "polish"
                   for w in getattr(self.engine, "_workers", []))

    def _on_checkpoint(self):
        """Auto-save during batch translation (every 25 entries)."""
        if self._closing or not self._is_batch_project_current():
            return
        # Auto-fix dropped control codes before saving
        self._restore_missing_codes()
        self._autosave()
        self.event_viewer.refresh_stats()

    def _on_status_changed(self):
        """Handle status change from manual edits in TranslationTable."""
        self.file_tree.refresh_stats(self.project)
        self.event_viewer.refresh_current_event()

    def _on_event_viewer_status_changed(self):
        """Handle status change from Event Viewer (mark reviewed, inline edits).

        Fires per keystroke, so the refresh is debounced (~150ms).
        """
        self._ev_status_timer.start()

    def _refresh_after_event_viewer_change(self):
        """Debounced refresh for Event Viewer edits."""
        self.file_tree.refresh_stats(self.project)
        self.trans_table._model.refresh_all()
        self.trans_table._update_stats()

    # ── Dark mode ──────────────────────────────────────────────────

    def _apply_dark_mode(self):
        """Apply the dark (Catppuccin Mocha) or light (Latte) theme everywhere."""
        theme.apply(self._dark_mode)
        # Widgets that paint per-item colors re-read the palette
        self.trans_table.set_dark_mode(self._dark_mode)
        self.event_viewer.set_dark_mode(self._dark_mode)
        self.queue_panel.apply_theme()
        self.pipeline_bar.apply_theme()
        self.gpu_monitor.apply_theme()
        self.image_panel.apply_theme()
        self.file_tree.apply_theme()
        self._welcome.apply_theme()

    # ── Glossary merge ─────────────────────────────────────────────

    def _on_glossary_add(self, jp_term: str, en_term: str, glossary_type: str):
        """Handle glossary entry added from translation table right-click."""
        if glossary_type == "project":
            self.project.glossary[jp_term] = en_term
        else:
            self._general_glossary[jp_term] = en_term
            self._save_settings()
        self._rebuild_glossary()
        label = "project" if glossary_type == "project" else "general"
        self.statusBar().showMessage(
            f"Added to {label} glossary: {jp_term} \u2192 {en_term}", 5000
        )

    def _rebuild_glossary(self):
        """Merge general + project glossaries into client.glossary.

        Project-specific entries override general entries if both define
        the same Japanese term.
        """
        self.client.glossary = {**self._general_glossary, **self.project.glossary}

    # ── Persistent settings ───────────────────────────────────────

    def _load_settings(self):
        """Load saved settings from _settings.json on startup."""
        try:
            with open(self._SETTINGS_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            return  # No saved settings — use defaults
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Keep the broken file so the user's settings aren't silently lost
            backup = self._SETTINGS_FILE + ".bak"
            try:
                shutil.copy2(self._SETTINGS_FILE, backup)
            except OSError:
                backup = ""
            log.warning("Settings file is corrupt (%s) — using defaults", e)
            msg = ("Your settings file could not be read and defaults are "
                   "being used.\n\n" + str(e))
            if backup:
                msg += f"\n\nThe unreadable file was copied to:\n{backup}"
            QTimer.singleShot(
                0, lambda: QMessageBox.warning(self, "Settings Reset", msg))
            return
        except OSError:
            return
        if not isinstance(cfg, dict):
            return

        if "ollama_url" in cfg:
            self.client.base_url = cfg["ollama_url"]
        if "model" in cfg:
            self.client.model = cfg["model"]
            self._global_model = cfg["model"]
        if "system_prompt" in cfg:
            self.client.system_prompt = cfg["system_prompt"]
        if "workers" in cfg:
            self.engine.num_workers = cfg["workers"]
        if "context_size" in cfg:
            self.parser.context_size = cfg["context_size"]
        if "dark_mode" in cfg:
            self._dark_mode = cfg["dark_mode"]
        if "wordwrap_override" in cfg and cfg["wordwrap_override"] > 0:
            self.plugin_analyzer._manual_chars_per_line = cfg["wordwrap_override"]
            self.plugin_analyzer.chars_per_line = cfg["wordwrap_override"]
            self._global_wordwrap = cfg["wordwrap_override"]
        if "general_glossary" in cfg and isinstance(cfg["general_glossary"], dict):
            self._general_glossary = cfg["general_glossary"]
        if "target_language" in cfg:
            self.client.target_language = cfg["target_language"]
        if "batch_size" in cfg:
            self.engine.batch_size = cfg["batch_size"]
        if "max_history" in cfg:
            self.engine.max_history = cfg["max_history"]
        # vision_model removed — main model handles image OCR
        if "extract_script_strings" in cfg:
            self.parser.extract_script_strings = cfg["extract_script_strings"]
        if "single_401_mode" in cfg:
            self.parser.single_401_mode = cfg["single_401_mode"]
        if "provider" in cfg:
            self.client.provider = cfg["provider"]
        if "api_key" in cfg:
            self.client.api_key = cfg["api_key"]
        if "prompt_preset" in cfg:
            self.client._prompt_preset = cfg["prompt_preset"]
        if "dazed_mode" in cfg:
            self.client.dazed_mode = cfg["dazed_mode"]
        # auto_tune setting removed; ignore legacy values silently
        if "export_review_file" in cfg:
            self._export_review_file = cfg["export_review_file"]
        if "disable_splash" in cfg:
            self._disable_splash = cfg["disable_splash"]
        if "show_translation_splash" in cfg:
            self._show_translation_splash = cfg["show_translation_splash"]
        if "inject_wordwrap" in cfg:
            self.plugin_analyzer.inject_wordwrap = cfg["inject_wordwrap"]
        if "game_font" in cfg:
            self._game_font = cfg["game_font"]
        if "extract_comments" in cfg:
            self.parser.extract_comments = cfg["extract_comments"]
        if "engine_settings" in cfg and isinstance(cfg["engine_settings"], dict):
            self._engine_overrides = cfg["engine_settings"]
        if isinstance(cfg.get("ui_state"), dict):
            self._ui_state = cfg["ui_state"]

    def _save_settings(self):
        """Persist current settings to _settings.json."""
        # Capture current engine's settings before saving
        self._save_engine_settings()
        cfg = {
            "ollama_url": self.client.base_url,
            # Global values — per-engine overrides live in engine_settings
            "model": self._global_model,
            "system_prompt": self.client.system_prompt,
            "provider": self.client.provider,
            "api_key": self.client.api_key,
            "prompt_preset": getattr(self.client, "_prompt_preset", "Custom"),
            "dazed_mode": self.client.dazed_mode,
            "workers": self.engine.num_workers,
            "batch_size": self.engine.batch_size,
            "max_history": self.engine.max_history,
            "context_size": self.parser.context_size,
            "dark_mode": self._dark_mode,
            "wordwrap_override": self._global_wordwrap,
            "general_glossary": self._general_glossary,
            "target_language": self.client.target_language,
            # vision_model removed — main model handles image OCR
            "extract_script_strings": self.parser.extract_script_strings,
            "single_401_mode": self.parser.single_401_mode,
            "export_review_file": self._export_review_file,
            "disable_splash": self._disable_splash,
            "show_translation_splash": self._show_translation_splash,
            "inject_wordwrap": self.plugin_analyzer.inject_wordwrap,
            "game_font": getattr(self, "_game_font", "Consolas"),
            "extract_comments": self.parser.extract_comments,
            "engine_settings": self._engine_overrides,
            "ui_state": (self._capture_window_state()
                         if hasattr(self, "text_splitter")
                         else getattr(self, "_ui_state", {})),
        }
        # Atomic write: a crash mid-write must never leave a truncated file
        tmp = self._SETTINGS_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._SETTINGS_FILE)
        except OSError:
            pass  # Non-critical — settings just won't persist

    # ── Auto-save ──────────────────────────────────────────────────

    def _autosave(self):
        """Auto-save project state if there are entries and a save path exists."""
        if not self.project.entries:
            return
        if not self._last_save_path:
            # Auto-save next to project if possible
            if self.project.project_path:
                self._last_save_path = os.path.join(
                    self.project.project_path, "_translation_state.json"
                )
            else:
                return
        try:
            self.project.save_state(self._last_save_path)
            self.statusbar.showMessage("Auto-saved", 2000)
        except Exception as e:
            self.statusbar.showMessage(f"Auto-save failed: {e}", 5000)

    # ── Model suggestion ────────────────────────────────────────────

    def _show_model_suggestion(self):
        """Show GPU-aware model recommendation dialog (first launch)."""
        installed = self.client.list_models() if self.client.is_available() else []
        dlg = ModelSuggestionDialog(installed_models=installed, parent=self)
        dlg.model_selected.connect(self._on_suggested_model)
        dlg.exec()

    def _on_suggested_model(self, tag: str):
        """Apply model from suggestion dialog."""
        self.client.model = tag
        self._global_model = tag
        self._save_settings()
        self.statusbar.showMessage(f"Model set to {tag}", 5000)

    # ── Progress ETA ───────────────────────────────────────────────

    def _on_progress(self, current: int, total: int, text: str):
        """Update progress bar with ETA during batch translation."""
        self._batch_done_count = current
        # Effective progress = engine progress + instant dupe fills
        dupe_offset = getattr(self, "_dupe_fill_count", 0)
        effective = current + dupe_offset

        # Progress bar max includes dupes; engine total doesn't
        bar_total = self.progress_bar.maximum()
        self.progress_bar.setValue(effective)

        # ETA based on LLM-only throughput (dupes are instant, don't count)
        eta_str = ""
        elapsed = time.time() - self._batch_start_time
        if current > 0 and elapsed > 0:
            llm_rate = elapsed / current  # seconds per actual LLM call
            remaining_llm = max(0, total - current)  # LLM calls left
            remaining = remaining_llm * llm_rate
            if remaining > 3600:
                eta_str = f" | ETA: {remaining/3600:.1f}h"
            elif remaining > 60:
                eta_str = f" | ETA: {remaining/60:.0f}m"
            else:
                eta_str = f" | ETA: {remaining:.0f}s"

        # Append running cost for cloud providers
        cost_str = ""
        if self.client.is_cloud and self.client.session_cost > 0:
            cost_str = f" | ${self.client.session_cost:,.4f}"

        self.progress_label.setText(
            f"Translating {effective}/{bar_total}{eta_str}{cost_str}: {text}"
        )

    # ── Pipeline bar ──────────────────────────────────────────────

    def _on_pipeline_step(self, step_key: str):
        """Handle Next Step button click from the pipeline bar."""
        if step_key == "dialogue" and not self.handler.has_db_split:
            # Engines without DB split: single "Translate" step covers all entries
            self._start_batch(mode="all")
            return
        actions = {
            "db": self._batch_translate_db,
            "dialogue": self._batch_translate_dialogue,
            "cleanup": self._cleanup_translations,
            "wordwrap": self._apply_wordwrap,
            "export": self._export_to_game,
        }
        action = actions.get(step_key)
        if action:
            action()

    # ── Translation memory ─────────────────────────────────────────

    def _batch_translate(self):
        """Batch All: DB first → auto-glossary → dialogue second."""
        if not self._check_engine_idle():
            return
        self._batch_all_chained = True
        if not self._start_batch(mode="db"):
            self._batch_all_chained = False

    def _batch_translate_db(self):
        """Stage 1: Translate only DB entries (names, descriptions, terms).

        Translate DB first so the user can QA names before they become
        glossary entries used in dialogue.
        """
        self._start_batch(mode="db")

    def _batch_translate_dialogue(self):
        """Stage 2: Translate only dialogue/event entries.

        Warns if no DB name glossary entries exist yet, since translating
        dialogue without glossary terms may produce inconsistent names.
        """
        # Check if any DB names have been glossary'd
        # Skip this check for engines without DB/dialogue split
        if not self.handler.db_files:
            self._start_batch(mode="dialogue")
            return
        db_glossary_count = sum(
            1 for e in self.project.entries
            if e.file in self.handler.db_files
            and e.field in (self.handler.auto_glossary_fields.get(e.file) or ())
            and e.status in ("translated", "reviewed")
        )
        if db_glossary_count == 0:
            reply = QMessageBox.warning(
                self, "No DB Names Translated",
                "No database names (items, skills, enemies, etc.) have been "
                "translated yet.\n\n"
                "Translating dialogue without glossary terms may produce "
                "inconsistent item/character names.\n\n"
                "Recommended: Run 'Batch DB' first to translate names, "
                "then QA them before translating dialogue.\n\n"
                "Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._start_batch(mode="dialogue")

    def _batch_translate_by_actor(self):
        """Translate dialogue grouped by speaker — female speakers first.

        Groups all untranslated entries by the speaker identified in code 101
        headers, cross-references with actor gender data, and orders:
          1. Female speaker dialogue
          2. Male speaker dialogue
          3. Unknown/no speaker dialogue
          4. Non-dialogue entries (DB, choices, plugins, etc.)

        This gives the LLM strong, consistent gender context per character
        and lets the user QA one character's lines at a time.
        """
        if not self._check_engine_idle():
            return
        if not self._ensure_ollama_ready():
            return

        # First batch: run actor pre-translate + gender dialog
        if not self._ensure_actors_ready():
            return

        if not self.client.actor_genders:
            QMessageBox.warning(
                self, "No Actor Data",
                "No actor gender data available.\n\n"
                "Open a project first (or load a state) so the translator\n"
                "knows which characters are male/female."
            )
            return

        # Glossary prefill (same as _start_batch)
        self._current_batch_mode = "all"
        gp_count = self._run_glossary_prefill()

        untranslated = [e for e in self.project.entries if e.status == "untranslated"]
        if not untranslated:
            QMessageBox.information(self, "Done", "All entries are already translated!")
            return

        # Build name → gender lookup from actor data
        name_to_gender = {}
        for actor_id, name in self.client.actor_names.items():
            gender = self.client.actor_genders.get(actor_id, "")
            if gender in ("female", "male"):
                name_to_gender[name] = gender

        # Group entries by speaker gender
        _speaker_re = re.compile(r'\[Speaker:\s*(.+?)\]')
        female_entries = []
        male_entries = []
        other_dialog = []
        non_dialog = []

        for entry in untranslated:
            # Only dialogue/scroll/choice entries have speaker context
            if entry.field not in ("dialog", "dialogue", "scroll_text", "choice"):
                non_dialog.append(entry)
                continue

            speaker = ""
            if entry.context:
                m = _speaker_re.search(entry.context)
                if m:
                    speaker = m.group(1).strip()

            gender = name_to_gender.get(speaker, "")
            if gender == "female":
                female_entries.append(entry)
            elif gender == "male":
                male_entries.append(entry)
            else:
                other_dialog.append(entry)

        # Combine: female → male → ungendered → non-dialogue
        ordered = female_entries + male_entries + other_dialog + non_dialog

        # Deduplicate: only send unique text to the LLM once
        to_translate, dupe_map = self._dedup_entries(ordered)
        total_with_dupes = len(ordered)
        dupe_total = total_with_dupes - len(to_translate)

        # Build summary for confirmation
        parts = []
        if female_entries:
            parts.append(f"  Female speakers: {len(female_entries)}")
        if male_entries:
            parts.append(f"  Male speakers: {len(male_entries)}")
        if other_dialog:
            parts.append(f"  Other dialogue: {len(other_dialog)}")
        if non_dialog:
            parts.append(f"  Non-dialogue (DB/plugins): {len(non_dialog)}")
        if dupe_total:
            parts.append(f"  Duplicates (auto-fill): {dupe_total}")
        summary = "\n".join(parts)

        if gp_count:
            summary += f"\n\n  (Pre-filled glossary: {gp_count} entries)"

        reply = QMessageBox.question(
            self, "Batch by Actor",
            f"Translating {len(to_translate)} unique entries "
            f"(+{dupe_total} duplicates) grouped by speaker gender:\n\n"
            f"{summary}\n\n"
            "Female speakers are translated first, then male, then the rest.\n"
            "Each entry gets explicit speaker gender hints for the LLM.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._begin_run("batch")
        self._batch_dupe_map = dupe_map
        self.progress_bar.setMaximum(total_with_dupes)

        # Queue panel shows unique entries only
        self.queue_panel.load_queue(to_translate)
        self.tabs.setCurrentWidget(self.queue_panel)

        self.engine.translate_batch(to_translate,
                                    memory_source=self.project.entries)

    def _run_glossary_prefill(self) -> int:
        """Fill untranslated entries whose full text is an exact glossary key.

        When the entire original text (stripped) matches a glossary key exactly,
        we use the glossary translation directly, skipping the LLM call.
        Respects self._current_batch_mode for db/dialogue filtering.
        Returns the number of entries filled.
        """
        glossary = self.client.glossary
        if not glossary:
            return 0

        mode = getattr(self, "_current_batch_mode", "all")
        count = 0
        for e in self.project.entries:
            if e.status != "untranslated":
                continue
            if mode == "db" and e.file not in self.handler.db_files:
                continue
            if mode == "dialogue" and e.file in self.handler.db_files:
                continue
            stripped = e.original.strip()
            if stripped in glossary:
                e.translation = glossary[stripped]
                e.status = "translated"
                self.trans_table.update_entry(e.id, e.translation, emit=False)
                self.queue_panel.mark_prefill(e.id, e.translation, "Glossary")
                self._maybe_add_to_glossary(e)
                count += 1

        if count:
            self.trans_table._notify_status_changed()
            self.file_tree.refresh_stats(self.project)

        return count

    @staticmethod
    def _dedup_entries(entries: list) -> tuple:
        """Separate entries into unique seeds and duplicate copies.

        Returns:
            (to_translate, dupe_map)
            - to_translate: list of entries to send to the LLM (one per
              unique original text), ordered DB → CE → Troops → Maps.
            - dupe_map: dict mapping original text → list of duplicate
              entries (excludes the seed).  Empty list for unique text.
        """
        dupe_map: dict[str, list] = {}
        to_translate = []

        for e in entries:
            if e.original in dupe_map:
                dupe_map[e.original].append(e)
            else:
                dupe_map[e.original] = []
                to_translate.append(e)

        # Sort: DB first, then CommonEvents, Troops, Maps/other
        # CE often contains shared dialogue that appears in Maps,
        # so translating CE first maximises instant dupe fills.
        def _file_order(entry):
            f = entry.file
            if f in ("Actors.json", "Classes.json", "Items.json",
                     "Weapons.json", "Armors.json", "Skills.json",
                     "States.json", "Enemies.json", "System.json"):
                return (0, f)
            if f == "CommonEvents.json":
                return (1, f)
            if f == "Troops.json":
                return (2, f)
            return (3, f)

        to_translate.sort(key=_file_order)
        return to_translate, dupe_map

    def _start_batch(self, mode: str = "all") -> bool:
        """Shared batch translation logic.

        Args:
            mode: "all" = everything, "db" = DB/System only,
                  "dialogue" = non-DB only (maps, events, plugins).

        Returns:
            True if batch was started, False if skipped/cancelled.
        """
        if not self._check_engine_idle():
            return False
        if not self._ensure_ollama_ready():
            return False

        # First batch: run actor pre-translate + gender dialog
        if not self._ensure_actors_ready():
            return False

        self._current_batch_mode = mode

        # Update pipeline bar
        if not self._wizard_active:
            if not self.handler.has_db_split:
                step = "dialogue"  # Single translate step (no DB/dialogue split)
            else:
                step = "db" if mode == "db" else "dialogue" if mode == "dialogue" else "db"
            self.pipeline_bar.mark_active(step)

        # Glossary prefill: exact-match entries skip LLM entirely
        gp_count = self._run_glossary_prefill()
        if gp_count:
            self.statusbar.showMessage(
                f"Pre-filled glossary: {gp_count} entries", 3000
            )

        untranslated = [e for e in self.project.entries if e.status == "untranslated"]
        if mode == "db":
            untranslated = [e for e in untranslated if e.file in self.handler.db_files]
        elif mode == "dialogue":
            untranslated = [e for e in untranslated if e.file not in self.handler.db_files]

        if not untranslated:
            if not self._wizard_active:
                labels = {"all": "All entries", "db": "DB entries", "dialogue": "Dialogue entries"}
                QMessageBox.information(self, "Done", f"{labels[mode]} are already translated!")
            return False

        # Deduplicate: only send unique text to the LLM once.
        # Duplicates are filled instantly when their seed completes.
        to_translate, dupe_map = self._dedup_entries(untranslated)
        total_with_dupes = len(untranslated)
        dupe_total = total_with_dupes - len(to_translate)

        if dupe_total:
            self.statusbar.showMessage(
                f"Queuing {len(to_translate)} unique entries "
                f"({dupe_total} duplicates will auto-fill)", 5000)

        self._begin_run("batch")
        self._batch_dupe_map = dupe_map  # used by _on_entry_done
        self.progress_bar.setMaximum(total_with_dupes)

        # Reset cost tracking for this batch
        if self.client.is_cloud:
            self.client.reset_session_cost()

        # Populate the queue panel with unique entries only
        self.queue_panel.load_queue(to_translate)
        self.tabs.setCurrentWidget(self.queue_panel)

        self.engine.translate_batch(to_translate,
                                    memory_source=self.project.entries)
        return True

    # ── Polish Grammar ──────────────────────────────────────────────

    def _polish_translations(self):
        """Run all translated entries through the LLM for grammar cleanup."""
        if not self._check_engine_idle():
            return
        if not self._ensure_ollama_ready():
            return

        to_polish = [
            e for e in self.project.entries
            if e.status in ("translated", "reviewed")
            and e.translation and e.translation.strip()
        ]
        if not to_polish:
            QMessageBox.information(self, "Nothing to Polish",
                                    "No translated entries to polish.")
            return

        reply = QMessageBox.question(
            self, "Polish Grammar",
            f"This will run {len(to_polish)} translated entries through the LLM\n"
            "to fix grammar and improve fluency (English → English).\n\n"
            "Original Japanese text is not changed — only the English translation\n"
            "gets cleaned up. This may take a while.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._begin_run("polish")
        self.progress_bar.setMaximum(len(to_polish))

        # Populate queue panel for polish
        self.queue_panel.load_queue(to_polish)
        self.tabs.setCurrentWidget(self.queue_panel)

        self.engine.polish_batch(to_polish)

    # ── Word Wrap ──────────────────────────────────────────────────

    def _apply_wordwrap(self):
        """Apply word wrapping to all translated entries."""
        if not self.project.entries:
            return

        if self._project_type in ("srpgstudio", "tyranoscript"):
            # Engine-specific word wrap (no plugin analyzer needed)
            if self._project_type == "srpgstudio":
                self._apply_wordwrap_srpg()
            else:
                self._apply_wordwrap_tyrano()
            return

        cpl = self.plugin_analyzer.chars_per_line
        summary = self.plugin_analyzer.get_summary()

        dlg = QDialog(self)
        dlg.setWindowTitle("Apply Word Wrap")
        dlg.setMinimumWidth(420)
        layout = QVBoxLayout(dlg)

        header = QLabel("Word Wrap Settings")
        header.setStyleSheet("font-size: 12pt; font-weight: bold; margin-bottom: 4px;")
        layout.addWidget(header)

        info = QLabel(
            f"Detected settings:\n\n{summary}\n\n"
            f"This will redistribute text across lines (~{cpl} chars/line).\n"
            "Entries that overflow their text box will be flagged."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        checkbox = QCheckBox("I understand this will modify my translations")
        layout.addWidget(checkbox)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Apply Word Wrap")
        ok_btn.setEnabled(False)
        checkbox.toggled.connect(ok_btn.setEnabled)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Disable plugin injection — manual breaks only
        self.plugin_analyzer.inject_wordwrap = False

        count = self.text_processor.process_all(self.project.entries)
        self.trans_table.refresh()

        overflows = self.text_processor.overflow_entries
        expanded = self.text_processor.expanded_count
        extra = self.text_processor.extra_lines
        msg = f"Modified {count} entries.\nWrapped to ~{cpl} chars/line."
        if expanded:
            msg += (f"\n\n{expanded} entries needed extra lines "
                    f"(+{extra} 401 commands will be added on export).")
        if overflows:
            msg += (f"\n\n{len(overflows)} entries exceed one message box "
                    "and will auto-paginate in-game.")
            # Show first few overflow files
            files = sorted(set(f for _, f in overflows))
            if len(files) <= 10:
                msg += "\n\nAffected files:\n" + "\n".join(f"  {f}" for f in files)
            else:
                msg += f"\n\nAcross {len(files)} files."
        QMessageBox.information(self, "Word Wrap Applied", msg)
        if not self._wizard_active:
            self.pipeline_bar.mark_done("wordwrap")

    def _apply_wordwrap_tyrano(self):
        """Apply TyranoScript word wrap using [r] tags derived from JP line lengths."""
        # Read all .ks source files to detect line budget
        scenario_dir = self.tyrano_parser._find_scenario_dir(
            self.project.project_path)
        if not scenario_dir:
            QMessageBox.warning(
                self, "Scenario Folder Not Found",
                "Cannot find the data/scenario folder for this TyranoScript game.")
            return

        from pathlib import Path
        ks_contents = []
        for ks_path in Path(scenario_dir).rglob("*.ks"):
            try:
                ks_contents.append(ks_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue

        budget = TyranoScriptParser.detect_line_budget(ks_contents)

        # Confirm with user
        dlg = QDialog(self)
        dlg.setWindowTitle("Apply Word Wrap")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)

        header = QLabel("TyranoScript Word Wrap")
        header.setStyleSheet("font-size: 12pt; font-weight: bold; margin-bottom: 4px;")
        layout.addWidget(header)

        info = QLabel(
            f"Detected line budget: ~{budget} English characters per line\n"
            f"(derived from original JP line lengths in {len(ks_contents)} .ks files)\n\n"
            "This will insert [r] line break tags into translated dialogue\n"
            "at word boundaries to fit the game's text box."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        checkbox = QCheckBox("I understand this will modify my translations")
        layout.addWidget(checkbox)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Apply Word Wrap")
        ok_btn.setEnabled(False)
        checkbox.toggled.connect(ok_btn.setEnabled)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        # Apply word wrap to all translated dialogue entries
        count = 0
        for entry in self.project.entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            if not entry.translation:
                continue
            if entry.field not in ("dialog",):
                continue
            wrapped = TyranoScriptParser.wordwrap_translation(
                entry.translation, budget)
            if wrapped != entry.translation:
                entry.translation = wrapped
                count += 1

        self.trans_table.refresh()
        QMessageBox.information(
            self, "Word Wrap Applied",
            f"Modified {count} entries.\n"
            f"Wrapped to ~{budget} chars/line using [r] tags."
        )
        if not self._wizard_active:
            self.pipeline_bar.mark_done("wordwrap")

    def _apply_wordwrap_srpg(self):
        """Apply word wrap for SRPG Studio — insert \\n at word boundaries."""
        _SRPG_CHARS_PER_LINE = 50  # conservative for portrait message box

        dlg = QDialog(self)
        dlg.setWindowTitle("Apply Word Wrap — SRPG Studio")
        dlg.setMinimumWidth(400)
        layout = QVBoxLayout(dlg)

        header = QLabel("SRPG Studio Word Wrap")
        header.setStyleSheet("font-size: 12pt; font-weight: bold;")
        layout.addWidget(header)

        # Chars per line spinner
        cpl_row = QHBoxLayout()
        cpl_row.addWidget(QLabel("Characters per line:"))
        from PyQt6.QtWidgets import QSpinBox
        cpl_spin = QSpinBox()
        cpl_spin.setRange(20, 80)
        cpl_spin.setValue(_SRPG_CHARS_PER_LINE)
        cpl_row.addWidget(cpl_spin)
        cpl_row.addStretch()
        layout.addLayout(cpl_row)

        info = QLabel(
            "This will re-wrap translated dialogue lines to fit the\n"
            "SRPG Studio message window (~50 chars with portrait).\n\n"
            "Existing line breaks within short lines are preserved."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        checkbox = QCheckBox("I understand this will modify my translations")
        layout.addWidget(checkbox)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_btn.setText("Apply Word Wrap")
        ok_btn.setEnabled(False)
        checkbox.toggled.connect(ok_btn.setEnabled)
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        max_chars = cpl_spin.value()
        count = 0
        for entry in self.project.entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            if not entry.translation or entry.field != "dialogue":
                continue
            wrapped = self._wordwrap_srpg_text(entry.translation, max_chars)
            if wrapped != entry.translation:
                entry.translation = wrapped
                count += 1

        self.trans_table.refresh()
        self._autosave()
        QMessageBox.information(
            self, "Word Wrap Applied",
            f"Modified {count} dialogue entries.\n"
            f"Wrapped to ~{max_chars} chars/line."
        )
        if not self._wizard_active:
            self.pipeline_bar.mark_done("wordwrap")

    @staticmethod
    def _wordwrap_srpg_text(text: str, max_chars: int) -> str:
        """Wrap text at word boundaries, inserting \\n.

        Preserves existing short line breaks (intentional formatting).
        Only re-wraps lines that exceed max_chars.
        """
        paragraphs = text.split('\n')
        result_lines = []

        for para in paragraphs:
            if len(para) <= max_chars:
                result_lines.append(para)
                continue
            # Wrap this long line at word boundaries
            words = para.split(' ')
            current_line = ''
            for word in words:
                if not current_line:
                    current_line = word
                elif len(current_line) + 1 + len(word) <= max_chars:
                    current_line += ' ' + word
                else:
                    result_lines.append(current_line)
                    current_line = word
            if current_line:
                result_lines.append(current_line)

        return '\n'.join(result_lines)

    # Same regex as ollama_client for fixing contraction spacing:
    #   "I 've" → "I've"   "Couldn' t" → "Couldn't"   "do n't" → "don't"
    # Handles space before and/or after the apostrophe (ASCII ' or curly ')
    _CONTRACTION_RE = re.compile(
        r"\b(\w+)\s*(['\u2019])\s*(ve|re|ll|t|s|d|m)\b", re.IGNORECASE)

    # Japanese speech/quote brackets that produce redundant "" in translations
    _JP_SPEECH_BRACKETS = set('\u300c\u300d\u300e\u300f')  # 「」『』

    def _cleanup_translations(self):
        """Run all automated post-processing fixes on translations."""
        if not self.project.entries:
            return

        from ..post_processor import run_post_processing

        # Run post-processor (word-per-line, placeholder leaks, spacing, etc.)
        result = run_post_processing(self.project.entries,
                                     glossary=self.project.glossary,
                                     project_type=self._project_type)

        # Also run quote/contraction fixes
        quotes_fixed = 0
        contractions_fixed = 0
        for entry in self.project.entries:
            if not entry.translation or entry.status not in ("translated", "reviewed"):
                continue
            original_text = entry.translation

            # Strip dialogue quotes: if original had 「」or『』, remove first/last "
            if self._JP_SPEECH_BRACKETS & set(entry.original):
                t = entry.translation
                first = t.find('"')
                last = t.rfind('"')
                if first != -1 and last > first:
                    entry.translation = t[:first] + t[first + 1:last] + t[last + 1:]

            # Fix contraction spacing (I 've → I've, Couldn' t → Couldn't)
            entry.translation = self._CONTRACTION_RE.sub(r"\1\2\3", entry.translation)

            if entry.translation != original_text:
                if '"' in original_text and '"' not in entry.translation:
                    quotes_fixed += 1
                else:
                    contractions_fixed += 1

        if result.total_entries_fixed or quotes_fixed or contractions_fixed:
            self.trans_table.refresh()
            self.file_tree.refresh_stats(self.project)

        parts = []
        if result.total_entries_fixed:
            parts.append(str(result))
        if quotes_fixed:
            parts.append(f"Stripped quotes from {quotes_fixed} entries")
        if contractions_fixed:
            parts.append(f"Fixed contractions in {contractions_fixed} entries")

        QMessageBox.information(
            self, "Clean Up Translations",
            "\n".join(parts) if parts else "No issues found — translations are clean."
        )
        if not self._wizard_active:
            self.pipeline_bar.mark_done("cleanup")

    # ── Strip Duplicate Actor Codes ────────────────────────────────

    # Matches leading \n[N] or \N[N], optionally wrapped in \c[N]...\c[0]
    # Captures the actor ID in group 1
    _LEADING_ACTOR_CODE_RE = re.compile(
        r'^(?:\\[Cc]\[\d+\])?\\[Nn]\[(\d+)\](?:\\[Cc]\[0\])?'
    )
    # Extract actor ID from a namebox string like \n[1] or \N<\n[1]>
    _NAMEBOX_ACTOR_ID_RE = re.compile(r'\\[Nn]\[(\d+)\]')

    def _strip_duplicate_actor_codes(self):
        """Strip leading \\n[N] from translations where namebox has the same actor.

        Only strips when the entry's namebox contains \\n[N] with the SAME
        actor ID as the leading code in the translation.  This avoids stripping
        cases where Speaker A mentions Speaker B by name (different actor ID).
        Also handles colored variants like \\c[27]\\n[1]\\c[0].
        """
        if not self.project.entries:
            return
        count = 0
        for entry in self.project.entries:
            if not entry.translation or entry.status not in ("translated", "reviewed"):
                continue
            if entry.field not in ("dialog", "scroll_text"):
                continue
            # Only strip when namebox identifies the same actor
            if not entry.namebox:
                continue
            nb_match = self._NAMEBOX_ACTOR_ID_RE.search(entry.namebox)
            if not nb_match:
                continue
            namebox_actor_id = nb_match.group(1)
            # Check first line of translation for matching actor code
            lines = entry.translation.split("\n")
            m = self._LEADING_ACTOR_CODE_RE.match(lines[0])
            if m and m.group(1) == namebox_actor_id:
                lines[0] = lines[0][m.end():]
                entry.translation = "\n".join(lines)
                count += 1

        if count:
            self.trans_table.refresh()
            self.file_tree.refresh_stats(self.project)

        QMessageBox.information(
            self, "Strip Duplicate Actor Codes",
            f"Stripped leading \\n[N] from {count} entries."
            if count else "No duplicate actor codes found."
        )

    # ── Fix Missing Codes ─────────────────────────────────────────

    def _restore_missing_codes(self) -> int:
        """Silently restore missing control codes in all translated entries.

        Scans every translated entry, compares control codes in the original
        to the translation, and auto-inserts any missing codes at the
        position they occupied in the original (start → prepend, end → append).

        Called automatically at each checkpoint and batch finish, so the LLM
        never needs to be re-invoked for dropped codes.

        Returns the number of entries fixed.
        """
        if not self.project.entries:
            return 0

        from .. import CONTROL_CODE_RE, TYRANO_CODE_RE
        code_re = TYRANO_CODE_RE if self._project_type == "tyranoscript" else CONTROL_CODE_RE

        fixed = 0
        for entry in self.project.entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            if not entry.translation:
                continue

            orig_codes = code_re.findall(entry.original)
            if not orig_codes:
                continue

            # Check which codes are missing (handle duplicates correctly)
            trans_check = entry.translation
            missing = []
            for code in orig_codes:
                if code in trans_check:
                    trans_check = trans_check.replace(code, "", 1)
                else:
                    missing.append(code)

            if not missing:
                continue

            # Insert missing codes based on their position in the original
            orig_len = len(entry.original)
            prepend = []
            append = []
            for code in missing:
                pos = entry.original.find(code)
                if pos < 0:
                    prepend.append(code)
                elif orig_len > 0 and pos / orig_len > 0.85:
                    append.append(code)
                else:
                    prepend.append(code)

            new_translation = "".join(prepend) + entry.translation + "".join(append)
            if new_translation != entry.translation:
                entry.translation = new_translation
                fixed += 1

        return fixed

    # ── Apply Glossary ─────────────────────────────────────────────

    def _apply_glossary(self):
        """Find translated entries with glossary mismatches and fix via replacement.

        For each glossary entry (JP → EN), scans translated entries where
        the original contains the JP term but the translation doesn't
        contain the expected EN term.  Builds a reverse lookup of old
        translations for each JP term and does direct string replacement.
        """
        if not self.project.entries or not self.client.glossary:
            QMessageBox.information(
                self, "Apply Glossary", "No entries or glossary is empty."
            )
            return

        # Build reverse lookup: for each glossary JP term, find what it was
        # previously translated as (from entries where original == jp_term exactly)
        old_translations: dict[str, set[str]] = {}  # jp_term → {old_en, ...}
        for entry in self.project.entries:
            if not entry.translation or entry.status not in ("translated", "reviewed"):
                continue
            jp = entry.original.strip()
            if jp in self.client.glossary:
                en = entry.translation.strip()
                expected = self.client.glossary[jp]
                if en and en != expected:
                    old_translations.setdefault(jp, set()).add(en)

        # Build list of mismatches: (entry, jp_term, expected_en)
        # Reviewed entries are user-approved — never rewritten here
        mismatches = []
        for entry in self.project.entries:
            if entry.status != "translated":
                continue
            if not entry.translation:
                continue
            for jp_term, en_term in self.client.glossary.items():
                if jp_term in entry.original and en_term not in entry.translation:
                    mismatches.append((entry, jp_term, en_term))

        if not mismatches:
            QMessageBox.information(
                self, "Apply Glossary",
                f"All {self.project.translated_count} translated entries "
                f"are consistent with the glossary ({len(self.client.glossary)} terms)."
            )
            return

        # Summarize by glossary term
        from collections import Counter
        term_counts = Counter(jp for _, jp, _en in mismatches)
        summary_lines = []
        for jp_term, count in term_counts.most_common(20):
            en_term = self.client.glossary[jp_term]
            old = old_translations.get(jp_term)
            if old:
                old_str = ", ".join(sorted(old)[:3])
                summary_lines.append(
                    f"  {jp_term}: {old_str} \u2192 {en_term} ({count} entries)")
            else:
                summary_lines.append(
                    f"  {jp_term} \u2192 {en_term} ({count} entries)")
        if len(term_counts) > 20:
            summary_lines.append(f"  ... and {len(term_counts) - 20} more terms")

        # Unique entries affected
        mismatch_ids = set()
        for entry, _jp, _en in mismatches:
            mismatch_ids.add(entry.id)

        # Replacement count (old_en → glossary EN), scoped per JP term
        replacements = sum(
            len(old_translations.get(jp, ())) for jp in term_counts)

        can_replace = bool(replacements)
        summary = (
            f"Found {len(mismatch_ids)} entries with glossary mismatches "
            f"({len(term_counts)} terms):\n\n"
            + "\n".join(summary_lines)
        )
        if can_replace:
            summary += (
                f"\n\nApply will replace old terms with glossary terms "
                f"({replacements} replacements). No LLM needed."
            )
        else:
            summary += (
                "\n\nNo old translations found to replace automatically.\n"
                "Use Retranslate to send these entries back to the LLM."
            )

        msg = QMessageBox(self)
        msg.setWindowTitle("Apply Glossary")
        msg.setText(summary)
        if can_replace:
            apply_btn = msg.addButton("Apply", QMessageBox.ButtonRole.AcceptRole)
        else:
            apply_btn = None
        retranslate_btn = msg.addButton("Retranslate", QMessageBox.ButtonRole.ActionRole)
        view_btn = msg.addButton("View Only", QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Cancel)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == apply_btn and can_replace:
            # Per entry, replace only the old renderings of JP terms that
            # actually occur in that entry's original — whole words only,
            # longest old term first.
            fixed = 0
            for entry in self.project.entries:
                if entry.id not in mismatch_ids:
                    continue
                original_translation = entry.translation
                pairs = []
                for jp_term, olds in old_translations.items():
                    if jp_term not in entry.original:
                        continue
                    new_en = self.client.glossary[jp_term]
                    pairs.extend((old_en, new_en) for old_en in olds)
                pairs.sort(key=lambda x: len(x[0]), reverse=True)
                for old_en, new_en in pairs:
                    pattern = r'(?<!\w)' + re.escape(old_en) + r'(?!\w)'
                    entry.translation = re.sub(
                        pattern, lambda _m, n=new_en: n, entry.translation)
                if entry.translation != original_translation:
                    fixed += 1
            self.trans_table.refresh()
            self.file_tree.load_project(self.project)
            # Check how many are still mismatched after replacement
            still_mismatched = 0
            for entry in self.project.entries:
                if entry.id not in mismatch_ids:
                    continue
                for jp_term, en_term in self.client.glossary.items():
                    if jp_term in entry.original and en_term not in entry.translation:
                        still_mismatched += 1
                        break
            msg_text = f"Fixed {fixed} entries via text replacement."
            if still_mismatched:
                msg_text += (
                    f"\n{still_mismatched} entries still have mismatches "
                    f"(may need retranslation)."
                )
            QMessageBox.information(self, "Apply Glossary", msg_text)
        elif clicked == retranslate_btn:
            # Retranslate only the mismatched entries (not the whole project)
            self.statusbar.showMessage(
                f"Retranslating {len(mismatch_ids)} entries...", 3000
            )
            ids = [e.id for e in self.project.entries if e.id in mismatch_ids]
            self._translate_selected(ids)
        elif clicked == view_btn:
            # Filter table to show only mismatch entries
            mismatch_entries = [e for e in self.project.entries if e.id in mismatch_ids]
            self.trans_table._entries = mismatch_entries
            self.trans_table._apply_filter()
            self.file_tree.clearSelection()
            self.statusbar.showMessage(
                f"Showing {len(mismatch_entries)} entries with glossary mismatches "
                f"({len(term_counts)} terms). Click a file or clear search to reset.",
                10000,
            )

    # ── Consistency Pass ──────────────────────────────────────────

    def _reset_all_for_retranslation(self):
        """Mark all translated entries as untranslated for batch retranslation."""
        if not self.project.entries:
            return
        translated = [e for e in self.project.entries
                      if e.status in ("translated", "reviewed")]
        if not translated:
            QMessageBox.information(self, "Nothing to Reset",
                                   "No translated entries to reset.")
            return
        reply = QMessageBox.question(
            self, "Reset All for Retranslation",
            f"This will mark {len(translated)} translated entries as untranslated.\n"
            f"Existing translations will be cleared.\n\n"
            f"Batch Translate will then redo all entries.\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        for entry in translated:
            entry.status = "untranslated"
            entry.translation = ""
        self.trans_table.refresh()
        self.event_viewer.refresh()
        self.file_tree.load_project(self.project)
        self.statusBar().showMessage(
            f"Reset {len(translated)} entries for retranslation.", 5000)

    def _consistency_pass(self):
        """Fix name variants, capitalization, and term inconsistencies."""
        if not self.project.entries:
            return

        translated = sum(
            1 for e in self.project.entries
            if e.status in ("translated", "reviewed") and e.translation
        )
        if translated == 0:
            QMessageBox.information(
                self, "Consistency Pass", "No translated entries to check."
            )
            return

        reply = QMessageBox.question(
            self, "Consistency Pass",
            f"Run consistency checks on {translated} translated entries?\n\n"
            "This will fix:\n"
            "  1. Name capitalization (knight \u2192 Knight)\n"
            "  2. Duplicate-original standardization (most common wins)\n"
            "  3. Glossary name variant spelling\n\n"
            "Reviewed entries keep capitalization and variant fixes\n"
            "but are excluded from duplicate standardization.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        caps, dupes, variants = self._run_consistency_fixes()
        total = caps + dupes + variants

        self.trans_table.refresh()
        self.file_tree.refresh_stats(self.project)

        if total == 0:
            QMessageBox.information(
                self, "Consistency Pass",
                "No inconsistencies found \u2014 all entries look consistent."
            )
        else:
            lines = []
            if caps:
                lines.append(f"  Capitalization fixed: {caps} entries")
            if dupes:
                lines.append(f"  Duplicates standardized: {dupes} entries")
            if variants:
                lines.append(f"  Name variants replaced: {variants} entries")
            QMessageBox.information(
                self, "Consistency Pass Complete",
                f"Fixed {total} entries:\n\n" + "\n".join(lines)
            )
            self._autosave()

    def _run_consistency_fixes(self) -> tuple:
        """Pure-Python consistency fixes across all translated entries.

        Phase 1: Capitalize name/displayName fields (skip prepositions).
        Phase 2: Standardize entries with identical originals to most-common
                 translation (skip reviewed entries).
        Phase 3: Fix name spelling variants using DB name entries as canonical
                 source (fuzzy-match, safe against substring collisions).

        Returns (caps_fixed, dupes_fixed, variants_fixed).
        """
        from collections import Counter, defaultdict
        from difflib import SequenceMatcher

        entries = self.project.entries
        caps_fixed = 0
        dupes_fixed = 0
        variants_fixed = 0

        # ── Phase 1: Capitalize name fields ──
        for entry in entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            if not entry.translation or entry.field not in self._CAPITALIZE_FIELDS:
                continue
            capped = self._title_case(entry.translation)
            if capped != entry.translation:
                entry.translation = capped
                caps_fixed += 1

        # ── Phase 2: Same-original standardization ──
        # Group by original text (only "translated" entries — skip "reviewed")
        groups: dict[str, list] = defaultdict(list)
        for entry in entries:
            if entry.status == "translated" and entry.translation:
                groups[entry.original].append(entry)

        for _original, group in groups.items():
            if len(group) < 2:
                continue
            translations = Counter(e.translation for e in group)
            if len(translations) < 2:
                continue
            canonical = translations.most_common(1)[0][0]
            for entry in group:
                if entry.translation != canonical:
                    entry.translation = canonical
                    dupes_fixed += 1

        # ── Phase 3: Name variant replacement ──
        # Build canonical names from proper-noun entries only:
        # actor names/nicknames and map displayNames.
        # NOT classes, items, enemies, etc. — those are common nouns that
        # would cause false positives ("Warrior" replacing "warrior" in dialogue).
        _PROPER_NOUN_FIELDS = {
            "Actors.json": ("name", "nickname"),
        }
        name_map: dict[str, str] = {}  # JP name → EN canonical
        for entry in entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            if not entry.translation:
                continue
            fields = _PROPER_NOUN_FIELDS.get(entry.file)
            is_proper = (fields and entry.field in fields) or (
                entry.file.startswith("Map")
                and entry.field == self._AUTO_GLOSSARY_MAP_FIELD
            )
            if is_proper:
                name_map[entry.original] = entry.translation

        if not name_map:
            return caps_fixed, dupes_fixed, variants_fixed

        # Build set of all canonical EN names (to avoid replacing one
        # canonical name with another — e.g. "Lilian" is NOT a variant
        # of "Lian" even though they're similar)
        canonical_en = set(name_map.values())

        # Sort longest JP first to prevent substring collisions
        # (リリアン before リアン)
        sorted_names = sorted(name_map.items(), key=lambda x: -len(x[0]))

        for entry in entries:
            if entry.status not in ("translated", "reviewed"):
                continue
            if not entry.translation:
                continue

            modified = entry.translation
            for jp_name, en_canonical in sorted_names:
                if jp_name not in entry.original:
                    continue
                if en_canonical in modified:
                    continue  # Already correct

                # Fuzzy-match each word in place (re.sub keeps the original
                # whitespace — newlines between dialogue lines survive)
                def _fix_word(m, en_canonical=en_canonical):
                    word = m.group(0)
                    # Strip trailing punctuation for comparison
                    stripped = word.rstrip(".,!?;:'\")-]}")
                    suffix = word[len(stripped):]

                    # Skip if this word is itself a canonical name
                    if stripped in canonical_en:
                        return word

                    # Only match proper-noun-like words (capitalized)
                    if (
                        stripped
                        and stripped[0].isupper()
                        and len(stripped) >= 3
                        and abs(len(stripped) - len(en_canonical)) <= 2
                        and stripped != en_canonical
                        and SequenceMatcher(
                            None, stripped.lower(), en_canonical.lower()
                        ).ratio() > 0.75
                    ):
                        return en_canonical + suffix
                    return word

                modified = re.sub(r'\S+', _fix_word, modified)

            if modified != entry.translation:
                entry.translation = modified
                variants_fixed += 1

        return caps_fixed, dupes_fixed, variants_fixed

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

    def _auto_export_review(self):
        """Auto-export a review TXT file after batch translation.

        File is named: Review_{Provider}_{Model}_{Date}.txt
        Saved next to the project state file.
        """
        if not self.project.entries or not self.project.project_path:
            return
        from datetime import datetime

        # Build filename: Review_DeepSeek_deepseek-chat_2026-02-15.txt
        provider = self.client.provider.replace(" ", "").replace("(", "").replace(")", "")
        model = self.client.model.replace("/", "-").replace(":", "-")
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"Review_{provider}_{model}_{date_str}.txt"
        path = os.path.join(self.project.project_path, filename)

        translated = [e for e in self.project.entries
                      if e.status in ("translated", "reviewed")]
        if not translated:
            return

        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"# Translation Review\n")
                f.write(f"# Project: {self.project.project_path}\n")
                f.write(f"# Provider: {self.client.provider}\n")
                f.write(f"# Model: {self.client.model}\n")
                f.write(f"# Date: {date_str}\n")
                cost_str = self.client.format_session_cost()
                if cost_str:
                    f.write(f"# {cost_str}\n")
                f.write(f"# Entries: {len(translated)} translated"
                        f" / {self.project.total} total\n\n")

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

            self.statusbar.showMessage(
                f"Review file saved: {filename}", 8000)
        except OSError as e:
            self.statusbar.showMessage(
                f"Review file export failed: {e}", 5000)

    # ── Translation patch create / apply ─────────────────────────

    def _create_patch(self):
        """Create a distributable translation patch zip."""
        if not self.project.entries:
            return

        translated = self.project.translated_count
        if translated == 0:
            QMessageBox.information(
                self, "Create Patch",
                "No translated entries to export."
            )
            return

        # Get game title for metadata
        game_title = ""
        for e in self.project.entries:
            if e.field == "gameTitle" and e.translation:
                game_title = e.translation
                break
        if not game_title:
            for e in self.project.entries:
                if e.field == "gameTitle":
                    game_title = e.original
                    break

        # Prompt for patch version
        version, ok = QInputDialog.getText(
            self, "Patch Version",
            f"Creating patch for: {game_title or 'RPG Maker Game'}\n"
            f"Entries: {translated} translated, {self.project.reviewed_count} reviewed\n\n"
            f"Patch version:",
            text="1.0",
        )
        if not ok:
            return

        # Default filename
        safe_title = "".join(c for c in game_title if c.isalnum() or c in " _-")[:50].strip()
        default_name = f"{safe_title} Translation Patch v{version}.zip" if safe_title else "translation_patch.zip"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Translation Patch", default_name, "Zip Files (*.zip)"
        )
        if not path:
            return

        try:
            self.project.export_patch(path, game_title=game_title,
                                      patch_version=version)
            QMessageBox.information(
                self, "Patch Created",
                f"Translation patch saved to:\n{path}\n\n"
                f"{translated} translated entries\n"
                f"{len(self.project.glossary)} glossary entries\n\n"
                f"This file contains NO game data — safe to distribute."
            )
        except Exception as e:
            QMessageBox.warning(self, "Patch Not Created",
                                f"Failed to create the patch:\n{e}")

    def _export_patch_zip(self):
        """Export translated game files + install.bat as a distributable zip."""
        if not self.project.entries:
            return
        if not self.project.project_path or not os.path.isdir(self.project.project_path):
            QMessageBox.warning(
                self, "Project Folder Not Found",
                f"The project folder no longer exists:\n"
                f"{self.project.project_path or '(not set)'}\n\n"
                "Open the game project first, then try again."
            )
            return
        data_dir = self.parser._find_data_dir(self.project.project_path)
        if not data_dir:
            QMessageBox.warning(
                self, "Data Directory Not Found",
                f"Could not find a data/ folder in:\n"
                f"{self.project.project_path}\n\n"
                "The patch zip needs the original game files to produce\n"
                "translated JSON files.\n\n"
                "Make sure the game's data/ directory exists in this folder."
            )
            return

        translated = [e for e in self.project.entries
                      if e.status in ("translated", "reviewed")]
        if not translated:
            QMessageBox.information(
                self, "Nothing to Export",
                "No translated entries to export."
            )
            return

        # Check for unwrapped lines that would overflow in-game
        unwrapped = self._check_unwrapped_entries(translated)
        if unwrapped > 0:
            result = QMessageBox.warning(
                self, "Lines May Overflow",
                f"{unwrapped} dialogue entries have lines longer than "
                f"{self.plugin_analyzer.chars_per_line} characters and may "
                f"overflow the text box in-game.\n\n"
                f"Apply word wrap before exporting?",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
            )
            if result == QMessageBox.StandardButton.Cancel:
                return
            if result == QMessageBox.StandardButton.Yes:
                count = self.text_processor.process_all(self.project.entries)
                self.trans_table.refresh()
                self.statusBar().showMessage(
                    f"Word wrap applied to {count} entries.", 5000)

        # Get game title
        game_title = ""
        for e in self.project.entries:
            if e.field == "gameTitle" and e.translation:
                game_title = e.translation
                break
        if not game_title:
            for e in self.project.entries:
                if e.field == "gameTitle":
                    game_title = e.original
                    break

        # Default filename — prefer English game title, fall back to folder name
        safe_title = "".join(
            c for c in game_title if c.isalnum() or c in " _-"
        )[:50].strip()
        if not safe_title:
            safe_title = os.path.basename(self.project.project_path)

        # Detect RJ/RE/VJ number from folder name (DLsite product ID)
        folder = os.path.basename(self.project.project_path)
        rj_match = re.search(r'((?:RJ|RE|VJ)\d+)', folder, re.IGNORECASE)
        rj_prefix = rj_match.group(1).upper() if rj_match else ""

        if rj_prefix and rj_prefix not in safe_title.upper():
            default_name = f"{rj_prefix} - {safe_title} - ENG Translation Patch.zip"
        else:
            default_name = f"{safe_title} - ENG Translation Patch.zip"

        path, _ = QFileDialog.getSaveFileName(
            self, "Export Patch Zip", default_name, "Zip Files (*.zip)"
        )
        if not path:
            return

        try:
            # Strip <WordWrap> tags if no plugin to handle them — on copies,
            # so the project's own translations are left untouched
            import copy
            inject_ww = self.plugin_analyzer.should_inject_plugin()
            export_entries = self.project.entries
            if not self.plugin_analyzer.has_wordwrap_plugin and not inject_ww:
                export_entries = []
                for e in self.project.entries:
                    if e.translation and re.search(r'<WordWrap>', e.translation,
                                                   flags=re.IGNORECASE):
                        e = copy.copy(e)
                        e.translation = re.sub(
                            r'<WordWrap>', '', e.translation, flags=re.IGNORECASE)
                    export_entries.append(e)

            self.parser.export_patch_zip(
                self.project.project_path, export_entries,
                path, game_title=game_title,
                inject_wordwrap=self.plugin_analyzer.should_inject_plugin())
            QMessageBox.information(
                self, "Install Package Created",
                f"Saved to:\n{path}\n\n"
                f"{len(translated)} translated entries.\n"
                f"Includes complete data folder + install/uninstall scripts.\n\n"
                f"End users: extract into the game folder and run install.bat."
            )
        except Exception as e:
            QMessageBox.warning(self, "Package Not Created",
                                f"Failed to create the install package:\n{e}")

    def _export_game_as_patch(self):
        """Package a game folder's current files into a distributable zip.

        Works without an active project.  Useful when the game already has
        translations applied (e.g. from a previous patch) and you want to
        create a redistributable install package from the current state.
        """
        # Default to current project folder if one is loaded
        default_dir = ""
        if (self.project and self.project.project_path
                and os.path.isdir(self.project.project_path)):
            default_dir = self.project.project_path

        game_path = QFileDialog.getExistingDirectory(
            self, "Select Game Folder (containing data/ and Game.exe)",
            default_dir
        )
        if not game_path:
            return

        # Validate data folder exists
        data_dir = self.parser._find_data_dir(game_path)
        if not data_dir:
            QMessageBox.warning(
                self, "Data Directory Not Found",
                f"Could not find a data/ folder in:\n{game_path}\n\n"
                "Select the game folder that contains the data/ directory."
            )
            return

        # Warn if no backup exists (might be packaging untranslated files)
        backup_dir = data_dir + "_original"
        if not os.path.isdir(backup_dir):
            reply = QMessageBox.question(
                self, "No Original Backup Found",
                f"No backup folder found at:\n{backup_dir}\n\n"
                "This means the data/ folder may still contain untranslated "
                "Japanese files, or originals were never backed up.\n\n"
                "Package the current data/ files anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Get game title from System.json
        game_title = self.parser.get_game_title(game_path)

        # Build default filename
        safe_title = "".join(
            c for c in game_title if c.isalnum() or c in " _-"
        )[:50].strip()
        if not safe_title:
            safe_title = os.path.basename(game_path)

        folder = os.path.basename(game_path)
        rj_match = re.search(r'((?:RJ|RE|VJ)\d+)', folder, re.IGNORECASE)
        rj_prefix = rj_match.group(1).upper() if rj_match else ""

        if rj_prefix and rj_prefix not in safe_title.upper():
            default_name = f"{rj_prefix} - {safe_title} - ENG Translation Patch.zip"
        else:
            default_name = f"{safe_title} - ENG Translation Patch.zip"

        path, _ = QFileDialog.getSaveFileName(
            self, "Save Patch Zip", default_name, "Zip Files (*.zip)"
        )
        if not path:
            return

        try:
            stats = self.parser.export_game_folder_as_patch(
                game_path, path, game_title=game_title
            )
            msg = (
                f"Saved to:\n{path}\n\n"
                f"{stats['data_files']} data file(s) packaged"
            )
            if stats['has_plugins']:
                msg += " + plugins.js"
            msg += ".\n"
            if stats['data_original_exists']:
                msg += (
                    "\ndata_original/ detected \u2014 data/ contains "
                    "translated files (as expected)."
                )
            msg += (
                "\n\nEnd users: extract into the game folder and run install.bat."
            )
            QMessageBox.information(self, "Patch Created", msg)
        except Exception as e:
            QMessageBox.warning(
                self, "Patch Not Created", f"Failed to create the patch zip:\n{e}"
            )

    # ── Re-translate with diff ─────────────────────────────────────

    def _translate_selected(self, entry_ids: list):
        """Translate specific selected entries (allows re-translation).

        Returns True if a run was started.
        """
        if not self._check_engine_idle():
            return False
        if not self._ensure_ollama_ready():
            return False

        entries = [self.project.get_entry_by_id(eid) for eid in entry_ids]
        entries = [e for e in entries if e is not None]
        if not entries:
            return False

        # Store old translations for diff display
        self._old_translations = {e.id: e.translation for e in entries if e.translation}

        # Force re-translate by temporarily marking as untranslated
        for e in entries:
            if e.status in ("translated", "reviewed"):
                e.status = "untranslated"

        self._begin_run("selected")  # no dupe-filling for manual retranslate
        self.progress_bar.setMaximum(len(entries))

        # No project-wide memory_source here: an explicit retranslate must not
        # be pre-filled with the (possibly bad) translation of a duplicate.
        self.engine.translate_batch(entries)
        return True

    def _on_entry_done(self, entry_id: str, translation: str):
        """Handle a single entry translation completing, with diff info."""
        # Late results from a replaced project must not land in this one
        # (results arriving while closing still land so they get saved)
        if not self._is_batch_project_current():
            return
        entry = self.project.get_entry_by_id(entry_id)
        if entry and self._is_polish_run():
            # Polish only rewrites the English text: keep status (reviewed
            # stays reviewed), no title-casing, no dupe filling.
            entry.translation = translation
        elif entry:
            # Check for diff with previous translation
            old = self._old_translations.get(entry_id, "")
            if old and old != translation:
                self.statusbar.showMessage(
                    f"Re-translated: was \"{old[:40]}...\" -> now \"{translation[:40]}...\"",
                    5000,
                )
            # Title-case name-type fields (e.g. "iron sword" → "Iron Sword")
            if entry.field in self._CAPITALIZE_FIELDS and translation:
                translation = self._title_case(translation)
            entry.translation = translation
            entry.status = "translated"
            # Auto-glossary: add translated DB names so the LLM
            # uses them consistently in subsequent dialogue entries
            self._maybe_add_to_glossary(entry)
        self.trans_table.update_entry(entry_id, translation)
        self.event_viewer.update_entry(entry_id, translation)
        # Update queue panel
        self.queue_panel.mark_entry_done(entry_id, translation, source="LLM")

        # Instantly fill duplicates — no checkpoint delay
        dupe_map = self._batch_dupe_map
        if entry and entry.original in dupe_map:
            dupes = dupe_map[entry.original]
            for dupe in dupes:
                if dupe.status == "untranslated":
                    dupe.translation = translation
                    dupe.status = "translated"
                    self.trans_table.update_entry(dupe.id, translation, emit=False)
                    self._maybe_add_to_glossary(dupe)
                    self._dupe_fill_count += 1
            # Update progress bar with dupe fills
            effective = self._batch_done_count + self._dupe_fill_count
            self.progress_bar.setValue(effective)

        self.file_tree.refresh_stats(self.project)

    # ── Retranslate single entry with correction ──────────────────

    def _retranslate_with_correction(self, entry_id: str, correction: str):
        """Retranslate a single entry with user's correction hint."""
        if not self._ensure_ollama_ready():
            return

        entry = self.project.get_entry_by_id(entry_id)
        if not entry:
            return

        old_translation = entry.translation
        project = self.project
        self.statusbar.showMessage(f"Retranslating with correction: {correction}...")

        def on_done(new_translation):
            if self.project is not project:
                return  # project was replaced meanwhile
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

        def on_failed(err):
            self.statusbar.showMessage(f"Retranslation failed: {err}", 5000)

        # Run in a background thread to avoid freezing the UI
        run_in_thread(
            self, self.client.translate,
            text=entry.original, context=entry.context,
            correction=correction, old_translation=old_translation,
            field=entry.field,
            on_done=on_done, on_error=on_failed,
        )

    # ── Polish selected entries ───────────────────────────────────

    def _polish_selected(self, entry_ids: list):
        """Polish grammar on selected entries via background thread."""
        if not self._ensure_ollama_ready():
            return

        entries = [self.project.get_entry_by_id(eid) for eid in entry_ids]
        entries = [e for e in entries if e and e.translation and e.translation.strip()]
        if not entries:
            return

        project = self.project
        jobs = [(e.id, e.translation) for e in entries]
        self.statusbar.showMessage(f"Polishing {len(entries)} entries...")

        def work():
            # Each call may raise (ConnectionError / ValueError) — report it
            # and keep going so the task always finishes.
            results, errors = [], []
            for eid, text in jobs:
                try:
                    polished = self.client.polish(text=text)
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e) or e.__class__.__name__)
                    continue
                if polished and polished != text:
                    results.append((eid, polished))
            return results, errors

        def on_done(result):
            results, errors = result
            if self.project is not project:
                return
            for eid, polished in results:
                entry = self.project.get_entry_by_id(eid)
                if entry:
                    entry.translation = polished
                    self.trans_table.update_entry(eid, polished)
            self.file_tree.refresh_stats(self.project)
            msg = f"Polished {len(results)}/{len(entries)} entries"
            if errors:
                msg += f" ({len(errors)} failed: {errors[-1][:80]})"
            self.statusbar.showMessage(msg, 8000 if errors else 5000)

        def on_failed(err):
            self.statusbar.showMessage(f"Polish failed: {err}", 5000)

        run_in_thread(self, work, on_done=on_done, on_error=on_failed)

    # ── Translation variants ──────────────────────────────────────

    def _show_variants(self, entry_id: str):
        """Generate 3 translation variants and let the user pick one."""
        if not self._ensure_ollama_ready():
            return

        entry = self.project.get_entry_by_id(entry_id)
        if not entry:
            return

        project = self.project
        self.statusbar.showMessage("Generating 3 translation variants...")

        def on_done(variants):
            if self.project is not project:
                return
            self.statusbar.showMessage(
                f"Generated {len(variants)} variant(s)", 3000
            )
            if not variants:
                QMessageBox.warning(self, "No Variants", "Failed to generate any variants.")
                return
            if len(variants) == 1:
                # Only one unique translation — apply it directly
                reply = QMessageBox.question(
                    self, "Single Variant",
                    "The model produced only one unique translation "
                    "(all attempts gave the same result).\n\n"
                    "Apply it?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    entry.translation = variants[0]
                    entry.status = "translated"
                    self.trans_table.update_entry(entry_id, variants[0])
                return
            dlg = VariantDialog(entry.original, variants, self)
            if dlg.exec():
                chosen = dlg.get_selected()
                entry.translation = chosen
                entry.status = "translated"
                self.trans_table.update_entry(entry_id, chosen)
                self.file_tree.refresh_stats(self.project)

        def on_failed(err):
            self.statusbar.showMessage(f"Variant generation failed: {err}", 5000)

        run_in_thread(
            self, self.client.translate_variants,
            text=entry.original, context=entry.context, field=entry.field,
            count=3, on_done=on_done, on_error=on_failed,
        )

    # ── Image Translation ─────────────────────────────────────────

    def _translate_images(self):
        """Switch to Image Translation tab and initialize it."""
        if not self.project.project_path:
            QMessageBox.warning(self, "No Project Open",
                                "Open a game folder first (Project \u203a Open Project\u2026).")
            return

        self.image_panel.set_project(self.project.project_path, self.client)
        self.tabs.setCurrentWidget(self.image_panel)

    # ── Window close cleanup ──────────────────────────────────────

    def closeEvent(self, event):
        """Save, stop background threads and managed Ollama on window close."""
        if self._closing:
            event.accept()
            return
        if self.engine.is_running:
            reply = QMessageBox.question(
                self, "Translation Running",
                "A translation run is in progress.\n\n"
                "Stop it and exit? Completed entries will be saved.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self._closing = True
        self._autosave_timer.stop()

        # Stop batch translation (also interrupts in-flight client waits)
        self._batch_all_chained = False
        self._user_stopped = True
        self.engine.cancel()
        self.client.cancel_event.set()  # also wakes single-entry tasks
        # Stop image translation worker if running
        self.image_panel.stop_worker()

        # Let workers finish their current request (entry_done still lands
        # so that work gets saved) — up to ~15s
        deadline = time.monotonic() + 15
        while ((self.engine.is_running or running_count())
               and time.monotonic() < deadline):
            QApplication.processEvents()
            time.sleep(0.05)
        all_done = (not self.engine.is_running
                    and wait_all(max(0, int((deadline - time.monotonic()) * 1000))))

        self._autosave()
        self._save_settings()

        # Clean up managed Ollama subprocess (if we started one)
        self.client.cleanup()

        if not all_done:
            # A thread is stuck in a blocking request; destroying a running
            # QThread aborts the process, so exit hard (state is saved).
            log.warning("Background threads still running on close — forcing exit")
            logging.shutdown()
            os._exit(0)

        super().closeEvent(event)
