"""Test helpers for disposing Qt widgets safely and promptly.

Why this exists (2026-11-07):

* A widget handed to ``deleteLater()`` outside an event loop is not deleted
  then; it is deleted by whichever *later* test first runs an event loop
  (``QEventLoop.exec`` in ``MainWindow._call_in_worker``, a modal dialog...).
  If the widget still owns a running ``QThread`` (a SettingsDialog's model
  fetch, for example), Qt aborts the whole process with "QThread: Destroyed
  while thread is still running".  On Windows that abort is a fail-fast: no
  pytest summary, no faulthandler traceback, exit code 127 in Git Bash.
* Widgets kept alive "just in case" pile up, and every
  ``QApplication.setStyleSheet`` (each MainWindow applies the theme) re-polishes
  every live widget, so building the Nth window cost O(N) - the main reason the
  suite took 8-17 minutes.

``dispose()`` waits for the widget's worker threads, then deletes it *now*.
"""

from PyQt6.QtCore import QCoreApplication, QEvent, QProcess, QThread

THREAD_WAIT_MS = 5_000   # below the 10 s pytest-timeout limit

# Widgets whose threads would not stop: never deleted (deleting would abort
# the process); the test that made them fails instead.
_UNDELETABLE: list = []


def _stop_threads(widget) -> list:
    """Wait for every QThread owned by ``widget``; return the ones still running."""
    stuck = []
    for thread in widget.findChildren(QThread):
        if thread.isRunning():
            thread.quit()
            if not thread.wait(THREAD_WAIT_MS):
                stuck.append(thread)
    return stuck


def dispose(widget) -> None:
    """Delete ``widget`` immediately, after its worker threads have finished.

    Raises AssertionError (and keeps the widget alive) if a thread is stuck,
    so a leaking test fails loudly instead of crashing a later one.
    """
    if widget is None:
        return
    try:
        widget.objectName()
    except RuntimeError:
        return  # already deleted
    for proc in widget.findChildren(QProcess):   # e.g. the GPU monitor's nvidia-smi
        if proc.state() != QProcess.ProcessState.NotRunning:
            proc.kill()
            proc.waitForFinished(2000)
    stuck = _stop_threads(widget)
    if stuck:
        _UNDELETABLE.append(widget)
        raise AssertionError(
            f"{type(widget).__name__} still has {len(stuck)} running QThread(s) "
            f"after {THREAD_WAIT_MS} ms; not deleting it (that would abort the run)")
    widget.hide()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def dispose_main_window(win) -> None:
    """Stop a test MainWindow's timers and background work, then delete it."""
    if win is None:
        return
    try:
        win.objectName()
    except RuntimeError:
        return
    from translator.widgets.background_task import wait_all
    win._closing = True
    win._autosave_timer.stop()
    engine = win.engine
    if engine.is_running:
        engine.cancel()
        for thread in list(engine._threads):
            thread.quit()
            if not thread.wait(THREAD_WAIT_MS):
                _UNDELETABLE.append(win)
                raise AssertionError("translation engine thread did not stop; "
                                     "MainWindow kept alive")
    if not wait_all(THREAD_WAIT_MS):
        _UNDELETABLE.append(win)
        raise AssertionError("background task still running; MainWindow kept alive")
    # Deliver queued results (finished signals, registry cleanup) while the
    # window still exists, then delete it.
    QCoreApplication.processEvents()
    dispose(win)
