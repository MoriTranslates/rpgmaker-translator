"""Shared pytest setup: import path, headless Qt, fixtures."""

import os
import shutil
import sys

# Headless Qt must be configured before PyQt6 is imported anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for p in (ROOT, TESTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest  # noqa: E402

FIXTURES = os.path.join(TESTS, "fixtures")
FAKE_MV_GAME = os.path.join(FIXTURES, "fake_mv_game")


@pytest.fixture
def fake_llm(monkeypatch):
    """Patch the AI client's HTTP layer with a deterministic fake LLM."""
    import fake_ai
    return fake_ai.install(monkeypatch)


@pytest.fixture
def mv_game(tmp_path):
    """A fresh, writable copy of the fake MV game."""
    dst = tmp_path / "fake_mv_game"
    shutil.copytree(FAKE_MV_GAME, dst)
    return str(dst)


@pytest.fixture
def mv_parser():
    from translator.rpgmaker_mv import RPGMakerMVParser
    return RPGMakerMVParser()


@pytest.fixture(scope="session")
def qapp():
    """A (headless) QApplication for signal/slot + QThread tests.

    A full QApplication rather than QCoreApplication so that widget tests
    which call ``QApplication.instance()`` later in the session still work.
    """
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app
