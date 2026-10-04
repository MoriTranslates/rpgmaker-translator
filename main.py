"""RPG Maker Translator — Local LLM Translation Tool.

Launch with: python main.py
"""

import os
import sys
import threading
import time
import traceback

from PyQt6.QtWidgets import QApplication, QMessageBox
from translator.resource_paths import app_dir
from translator.version import __version__
from translator.widgets.main_window import MainWindow

# Error log lives next to _settings.json (project root, or beside the .exe)
_ERROR_LOG = os.path.join(app_dir(), "_error.log")

_window = None
_showing_error = False


def _excepthook(exc_type, exc, tb):
    """Log unhandled exceptions instead of letting PyQt abort the process."""
    global _showing_error
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return

    text = "".join(traceback.format_exception(exc_type, exc, tb))
    try:
        sys.stderr.write(text)
    except Exception:
        pass
    try:
        with open(_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n{text}\n")
    except OSError:
        pass

    # Dialogs and autosave only from the GUI thread, and never re-entrantly
    if threading.current_thread() is not threading.main_thread() or _showing_error:
        return
    _showing_error = True
    try:
        saved = False
        if _window is not None:
            try:
                _window._autosave()
                saved = True
            except Exception:
                pass
        try:
            QMessageBox.critical(
                _window, "Unexpected Error",
                f"An unexpected error occurred:\n\n{exc_type.__name__}: {exc}\n\n"
                + ("Your project was auto-saved.\n" if saved else "")
                + f"Details were written to:\n{_ERROR_LOG}",
            )
        except Exception:
            pass
    finally:
        _showing_error = False


def main():
    global _window
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName("RPG Maker Translator")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("MoriTranslates")
    app.setStyle("Fusion")

    _window = MainWindow()
    _window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
