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


# ── No real network ──────────────────────────────────────────────────
#
# Tests used to reach the user's real Ollama (SettingsDialog's model fetch
# hit localhost:11434) and real DNS.  Besides being slow and flaky, a fetch
# thread still waiting on the server when its dialog was deleted aborted the
# whole run (see qt_helpers.py).  Every outbound connection now fails at once
# with ConnectionRefusedError, like a server that is down.  Loopback
# connections to sockets this process is listening on (socket.socketpair,
# which asyncio uses on Windows) still work.

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"}
_own_listeners: set = set()   # ports of sockets this process listens on


class NetworkBlocked(ConnectionRefusedError):
    """Raised for any real outbound connection attempted by a test."""


def _allowed(address) -> bool:
    if not isinstance(address, tuple) or len(address) < 2:
        return True   # AF_UNIX / unusual families: not network
    host, port = address[0], address[1]
    return str(host).lower() in _LOOPBACK_HOSTS and port in _own_listeners


def _install_network_guard():
    import ipaddress
    import socket

    real_listen = socket.socket.listen
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def listen(self, *args):
        result = real_listen(self, *args)
        try:
            _own_listeners.add(self.getsockname()[1])
        except (OSError, IndexError, TypeError):
            pass  # not an INET socket: nothing to allow-list
        return result

    def connect(self, address):
        if not _allowed(address):
            raise NetworkBlocked(f"tests must not open real connections ({address!r})")
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _allowed(address):
            raise NetworkBlocked(f"tests must not open real connections ({address!r})")
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if host is not None:
            name = host.decode() if isinstance(host, bytes) else str(host)
            if name.lower() not in _LOOPBACK_HOSTS:
                try:
                    ipaddress.ip_address(name)
                except ValueError:
                    raise socket.gaierror(
                        socket.EAI_NONAME,
                        f"tests must not resolve real host names ({name!r})") from None
        return real_getaddrinfo(host, *args, **kwargs)

    socket.socket.listen = listen
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo


_install_network_guard()


# ── Make Qt's fatal messages visible ─────────────────────────────────
#
# Qt's own handler sends messages to the debugger (not stderr) when the
# process has no console, and pytest's output capture swallows stderr when
# the process dies, so a qFatal abort left no trace at all.  Critical/fatal
# messages now go to the terminal's real stderr (bypassing capture); the
# rest go to sys.stderr, which pytest shows for a failing test.

_real_stderr_fd = [2]


def _install_qt_message_handler():
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    def handler(msg_type, _context, message):
        line = f"[Qt {msg_type.name}] {message}\n"
        if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
            try:
                os.write(_real_stderr_fd[0], line.encode("utf-8", "replace"))
            except OSError:
                pass  # stderr closed: nowhere left to report to
        else:
            sys.stderr.write(line)

    qInstallMessageHandler(handler)


def pytest_configure(config):
    capman = config.pluginmanager.getplugin("capturemanager")
    try:
        saved = capman._global_capturing.err.targetfd_save
    except AttributeError:
        saved = None  # -s / capture off, or pytest internals changed: fd 2 is fine
    if isinstance(saved, int):
        _real_stderr_fd[0] = saved
    _install_qt_message_handler()


@pytest.fixture(autouse=True)
def _no_real_process_listing(monkeypatch):
    """Stopping Ollama sweeps orphaned runners; tests must never see (or
    kill) the real machine's processes. Tests that need a listing set one."""
    from translator import ollama_procs
    monkeypatch.setattr(ollama_procs, "list_processes", lambda: [])


@pytest.fixture(autouse=True)
def _no_model_list_network(monkeypatch):
    """The suggestions window must never fetch from GitHub during tests."""
    from translator.widgets.model_suggestion_dialog import ModelSuggestionDialog
    monkeypatch.setattr(ModelSuggestionDialog, "_AUTO_FETCH", False)
