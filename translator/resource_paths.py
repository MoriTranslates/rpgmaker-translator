"""Resolve bundled read-only resources and the writable app directory.

Works both from a source checkout (``python main.py``) and from a
PyInstaller bundle, where ``__file__`` points inside the bundle's
``_internal`` folder (onedir) or a temp extraction dir (onefile).

- ``resource_path("assets", "TranslationSplash.png")`` -> read-only data
  shipped with the app (assets/, translator/resources/).
- ``app_dir()`` -> folder for user-writable files that historically live
  next to main.py (``_settings.json``, ``_error.log``, ``tools/``).  For a
  frozen build this is the folder containing the .exe, so the portable
  "unzip and run" layout keeps settings beside the program.
"""

from __future__ import annotations

import os
import sys

# Repo root when running from source (translator/ is one level down).
_SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_frozen() -> bool:
    """True when running from a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> str:
    """Root of the read-only bundled files (sys._MEIPASS when frozen)."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return _SOURCE_ROOT


def resource_path(*parts: str) -> str:
    """Absolute path to a bundled read-only resource, e.g. ``("assets", "x.png")``."""
    return os.path.join(bundle_dir(), *parts)


def app_dir() -> str:
    """Writable folder for settings / logs / caches kept beside the app."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _SOURCE_ROOT
