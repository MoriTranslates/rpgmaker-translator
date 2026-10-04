"""Run a blocking callable on a QThread without hand-rolled thread plumbing.

Usage::

    run_in_thread(self, self.client.translate, text,
                  on_done=lambda result: ...,
                  on_error=lambda msg: ...)

The callbacks always run on the GUI thread (delivered through a relay
QObject that lives there).  Every task is kept alive in a module-level
registry until its thread has finished; the registry then drops it and the
Python-owned thread/worker/relay are destroyed on the GUI thread, so callers
never need to hold references.  ``wait_all()`` lets the main
window drain outstanding tasks on close.
"""

import logging
import time

from PyQt6.QtCore import QObject, QThread, pyqtSignal, pyqtSlot

log = logging.getLogger(__name__)

# Live tasks — holds Python references until each thread has finished.
_registry: set = set()


class _Worker(QObject):
    """Executes the callable inside the worker thread."""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, args, kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    @pyqtSlot()
    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:  # noqa: BLE001 — reported via on_error
            log.exception("Background task failed")
            self.failed.emit(str(e) or e.__class__.__name__)
            return
        self.done.emit(result)


class _Relay(QObject):
    """Lives on the GUI thread; forwards worker results to the callbacks."""

    def __init__(self, task):
        super().__init__()
        self._task = task

    @pyqtSlot(object)
    def on_done(self, result):
        task = self._task
        if task is None:
            return
        task.thread.quit()
        if task.on_done is not None:
            task.on_done(result)

    @pyqtSlot(str)
    def on_failed(self, msg):
        task = self._task
        if task is None:
            return
        task.thread.quit()
        if task.on_error is not None:
            task.on_error(msg)
        else:
            log.warning("Background task error (unhandled): %s", msg)

    @pyqtSlot()
    def on_thread_finished(self):
        task = self._task
        if task is not None:
            task._cleanup()


class BackgroundTask:
    """Handle for one running task (thread + worker + relay)."""

    def __init__(self, parent, fn, args, kwargs, on_done, on_error):
        self.on_done = on_done
        self.on_error = on_error
        # No Qt parents: lifetime is owned by the registry so a parent widget
        # being destroyed can never delete a running QThread, and nothing is
        # destroyed while a callback (e.g. a modal dialog) is still on the stack.
        self.parent = parent
        self.thread = QThread()
        self.worker = _Worker(fn, args, kwargs)
        self.worker.moveToThread(self.thread)
        self.relay = _Relay(self)

        self.thread.started.connect(self.worker.run)
        self.worker.done.connect(self.relay.on_done)
        self.worker.failed.connect(self.relay.on_failed)
        self.thread.finished.connect(self.relay.on_thread_finished)

    def start(self):
        _registry.add(self)
        self.thread.start()

    def is_running(self) -> bool:
        return self.thread.isRunning()

    def _cleanup(self):
        # finished is delivered queued; make sure the OS thread is fully gone
        # before the last reference (and with it the QThread) is released.
        self.thread.wait()
        self.relay._task = None  # break the task <-> relay cycle
        _registry.discard(self)


def run_in_thread(parent, fn, *args, on_done=None, on_error=None, **kwargs):
    """Run ``fn(*args, **kwargs)`` on a background QThread.

    Args:
        parent: Owning widget (kept for reference; callbacks should only
            touch objects that outlive the task — the main window drains
            all tasks with wait_all() on close).
        fn: Blocking callable to run off the GUI thread.
        on_done: Called on the GUI thread with fn's return value.
        on_error: Called on the GUI thread with the exception message.

    Returns:
        The BackgroundTask handle (callers don't need to keep it).
    """
    task = BackgroundTask(parent, fn, args, kwargs, on_done, on_error)
    task.start()
    return task


def running_count() -> int:
    """Number of tasks whose threads are still running."""
    return sum(1 for t in list(_registry) if t.is_running())


def wait_all(timeout_ms: int = 0) -> bool:
    """Wait for every outstanding task's thread to finish.

    Callbacks are not delivered while waiting (no event processing).
    Returns True if all threads finished within ``timeout_ms``.
    """
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    for task in list(_registry):
        task.thread.quit()
        remaining = int(max(0.0, deadline - time.monotonic()) * 1000)
        if task.thread.isRunning() and not task.thread.wait(remaining):
            return False
    return True
