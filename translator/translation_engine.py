"""Translation engine — orchestrates LLM translation with Qt threading."""

import requests

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from .ollama_client import OllamaClient
from .project_model import TranslationEntry


class TranslationWorker(QObject):
    """Worker that runs translations in a background thread."""

    entry_done = pyqtSignal(str, str)       # entry_id, translation
    item_processed = pyqtSignal(str)        # text preview (for progress tracking)
    finished = pyqtSignal()
    error = pyqtSignal(str, str)            # entry_id, error_message

    def __init__(self, client: OllamaClient, entries: list, mode: str = "translate"):
        super().__init__()
        self.client = client
        self.entries = entries
        self.mode = mode  # "translate" or "polish"
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        """Process all entries in this worker's chunk."""
        for entry in self.entries:
            if self._cancelled:
                break

            if self.mode == "translate":
                # Skip already translated/reviewed or empty
                if entry.status in ("translated", "reviewed", "skipped"):
                    self.item_processed.emit("(skipped)")
                    continue
                if not entry.original.strip():
                    entry.status = "skipped"
                    self.item_processed.emit("(empty)")
                    continue
            else:
                # Polish mode: skip entries without translations
                if not entry.translation or not entry.translation.strip():
                    self.item_processed.emit("(skipped)")
                    continue

            preview = (entry.translation if self.mode == "polish" else entry.original)
            preview = preview[:50].replace("\n", " ")
            self.item_processed.emit(preview)

            try:
                if self.mode == "polish":
                    result = self.client.polish(text=entry.translation)
                else:
                    result = self.client.translate(
                        text=entry.original,
                        context=entry.context,
                        field=entry.field,
                    )
                self.entry_done.emit(entry.id, result)
            except (ConnectionError, requests.RequestException, ValueError, OSError) as e:
                self.error.emit(entry.id, str(e))

        self.finished.emit()


class TranslationEngine(QObject):
    """Manages parallel translation workers and threads."""

    progress = pyqtSignal(int, int, str)    # current, total, current_text
    entry_done = pyqtSignal(str, str)
    finished = pyqtSignal()
    error = pyqtSignal(str, str)
    checkpoint = pyqtSignal()

    CHECKPOINT_INTERVAL = 25  # auto-save every N translated entries

    def __init__(self, client: OllamaClient, parent=None):
        super().__init__(parent)
        self.client = client
        self.num_workers = 2
        self._threads = []
        self._workers = []
        self._total = 0
        self._progress_count = 0
        self._translate_count = 0
        self._finished_workers = 0

    @property
    def is_running(self) -> bool:
        return any(t.isRunning() for t in self._threads)

    def translate_batch(self, entries: list):
        """Start batch translation with parallel workers."""
        if self.is_running:
            return

        # Filter to only untranslated entries
        to_translate = [e for e in entries if e.status == "untranslated"]
        if not to_translate:
            self.finished.emit()
            return

        self._total = len(to_translate)
        self._progress_count = 0
        self._translate_count = 0
        self._finished_workers = 0
        self._threads = []
        self._workers = []

        # Split into N sequential chunks (preserves context locality)
        n = min(self.num_workers, len(to_translate))
        chunks = self._split_chunks(to_translate, n)

        for chunk in chunks:
            thread = QThread()
            worker = TranslationWorker(self.client, chunk)
            worker.moveToThread(thread)

            thread.started.connect(worker.run)
            worker.item_processed.connect(self._on_item_processed)
            worker.entry_done.connect(self._on_entry_done)
            worker.error.connect(self.error.emit)
            worker.finished.connect(self._on_worker_finished)

            self._threads.append(thread)
            self._workers.append(worker)

        # Start all threads
        for thread in self._threads:
            thread.start()

    def polish_batch(self, entries: list):
        """Start batch grammar polish with parallel workers."""
        if self.is_running:
            return

        # Filter to entries that have translations
        to_polish = [e for e in entries
                     if e.status in ("translated", "reviewed")
                     and e.translation and e.translation.strip()]
        if not to_polish:
            self.finished.emit()
            return

        self._total = len(to_polish)
        self._progress_count = 0
        self._translate_count = 0
        self._finished_workers = 0
        self._threads = []
        self._workers = []

        n = min(self.num_workers, len(to_polish))
        chunks = self._split_chunks(to_polish, n)

        for chunk in chunks:
            thread = QThread()
            worker = TranslationWorker(self.client, chunk, mode="polish")
            worker.moveToThread(thread)

            thread.started.connect(worker.run)
            worker.item_processed.connect(self._on_item_processed)
            worker.entry_done.connect(self._on_entry_done)
            worker.error.connect(self.error.emit)
            worker.finished.connect(self._on_worker_finished)

            self._threads.append(thread)
            self._workers.append(worker)

        for thread in self._threads:
            thread.start()

    def translate_single(self, entry: TranslationEntry) -> str:
        """Translate a single entry synchronously (for right-click translate)."""
        return self.client.translate(
            text=entry.original,
            context=entry.context,
            field=entry.field,
        )

    def cancel(self):
        """Cancel all running workers."""
        for worker in self._workers:
            worker.cancel()

    def _on_item_processed(self, text: str):
        """Track global progress across all workers."""
        self._progress_count += 1
        self.progress.emit(self._progress_count, self._total, text)

    def _on_entry_done(self, entry_id: str, translation: str):
        """Relay entry completion and trigger checkpoints."""
        self.entry_done.emit(entry_id, translation)
        self._translate_count += 1
        if self._translate_count % self.CHECKPOINT_INTERVAL == 0:
            self.checkpoint.emit()

    def _on_worker_finished(self):
        """Track worker completion; emit finished when all done."""
        self._finished_workers += 1
        if self._finished_workers >= len(self._workers):
            # All workers done — clean up
            for thread in self._threads:
                thread.quit()
                thread.wait()
            self._threads = []
            self._workers = []
            self.finished.emit()

    @staticmethod
    def _split_chunks(items: list, n: int) -> list:
        """Split a list into n roughly equal sequential chunks."""
        if n <= 1:
            return [items]
        k, remainder = divmod(len(items), n)
        chunks = []
        start = 0
        for i in range(n):
            size = k + (1 if i < remainder else 0)
            chunks.append(items[start:start + size])
            start += size
        return chunks
