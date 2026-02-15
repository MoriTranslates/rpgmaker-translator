"""Auto-tuner — tournament-style batch_size calibration for local LLM translation.

Three-round tournament to find optimal batch_size:
  Round 1 (Survey):  Test [5, 10, 15, 20, 25, 30] — 1 batch each
  Round 2 (Semis):   Top 3 → 5 batches each, average entries/sec
  Round 3 (Finals):  Top 2 → 5 more batches each, combined avg → winner

All calibration entries are real translations — results are emitted
via entry_done signals and preserved in the project.
"""

import logging
import time

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


class AutoTunerWorker(QObject):
    """Tournament-style batch_size calibration.

    Runs in a QThread.  Consumes entries sequentially from the queue;
    every translation is emitted via entry_done so results are kept.
    Rounds 2 and 3 average multiple runs to smooth out variance
    (critical when throughput differences are small, e.g. 1.1 vs 1.0 eps).
    """

    # Real translation results (same signature as TranslationWorker)
    entry_done = pyqtSignal(str, str)       # entry_id, translation
    item_processed = pyqtSignal(str)        # text preview (for progress counter)

    # Calibration status
    progress = pyqtSignal(str)              # status message for GUI
    step_done = pyqtSignal(int, float, float)  # batch_size, entries_per_sec, elapsed
    finished = pyqtSignal(int)              # optimal batch_size
    error = pyqtSignal(str)                 # error message

    SURVEY_STEPS = [5, 10, 15, 20, 25, 30]
    REPS = 3               # batches per size in rounds 2 & 3
    WARMUP_SIZE = 2         # entries used to prime KV cache
    MIN_ENTRIES = 107       # minimum for round 1 (warmup + 5+10+15+20+25+30)

    def __init__(self, client, entries: list):
        """
        Args:
            client: AIClient instance (must have translate_batch method).
            entries: List of TranslationEntry objects to use for calibration.
                     Entries are consumed sequentially; their translations are kept.
        """
        super().__init__()
        self.client = client
        self.entries = entries
        self._cancelled = False
        self._consumed = 0

    @property
    def consumed_count(self) -> int:
        """Number of entries translated during calibration."""
        return self._consumed

    def cancel(self):
        self._cancelled = True

    def run(self):
        """Run tournament calibration and emit finished(optimal_batch_size)."""
        try:
            self._run_impl()
        except Exception as e:
            log.exception("Auto-tuner error: %s", e)
            self.error.emit(str(e))
            self.finished.emit(self.SURVEY_STEPS[0])

    def _run_impl(self):
        # Skip if cloud API — no VRAM constraints
        if getattr(self.client, 'is_cloud', False):
            log.info("Auto-tuner: cloud API detected, using batch_size=30")
            self.finished.emit(30)
            return

        available = [e for e in self.entries if e.status == "untranslated"
                     and e.original and e.original.strip()]
        if len(available) < self.MIN_ENTRIES:
            log.info("Auto-tuner: only %d untranslated entries (need %d), skipping",
                     len(available), self.MIN_ENTRIES)
            self.finished.emit(1)
            return

        offset = 0

        # ── Warmup: prime KV cache ──────────────────────────────────
        if not self._cancelled and offset + self.WARMUP_SIZE <= len(available):
            self.progress.emit("Warming up model...")
            warmup = available[offset:offset + self.WARMUP_SIZE]
            offset += self.WARMUP_SIZE
            try:
                self._translate_and_emit(warmup, self.WARMUP_SIZE)
            except Exception as e:
                log.warning("Auto-tuner warmup failed: %s", e)
                self.finished.emit(self.SURVEY_STEPS[0])
                return

        # ── Round 1: Survey — test all step sizes once ──────────────
        r1_results = []  # (batch_size, eps)

        for step_size in self.SURVEY_STEPS:
            if self._cancelled:
                break
            if offset + step_size > len(available):
                log.info("R1: not enough entries for size %d, stopping survey", step_size)
                break

            batch = available[offset:offset + step_size]
            offset += step_size

            self.progress.emit(
                f"R1 Survey: batch={step_size} "
                f"({len(r1_results)+1}/{len(self.SURVEY_STEPS)}) "
                f"[{self._consumed} translated]")

            eps, elapsed = self._timed_translate(batch, step_size)
            r1_results.append((step_size, eps))
            self.step_done.emit(step_size, eps, elapsed)
            log.info("R1: batch=%d → %.2f eps (%.1fs)", step_size, eps, elapsed)

        if not r1_results:
            self.finished.emit(self.SURVEY_STEPS[0])
            return

        # Rank by throughput
        top3 = sorted(r1_results, key=lambda r: r[1], reverse=True)[:3]
        top3_sizes = [s for s, _ in top3]
        log.info("R1 top 3: %s", [(s, f"{e:.2f}") for s, e in top3])
        self.progress.emit(
            f"R1 done — top 3: {top3_sizes} "
            f"[{self._consumed} translated]")

        # ── Round 2: Semifinals — top 3, REPS batches each ─────────
        r2_raw = {}  # size → [eps, eps, ...]

        for size in top3_sizes:
            if self._cancelled:
                break
            r2_raw[size] = []

            for rep in range(self.REPS):
                if self._cancelled:
                    break
                if offset + size > len(available):
                    log.info("R2: out of entries at size=%d rep=%d", size, rep)
                    break

                batch = available[offset:offset + size]
                offset += size

                self.progress.emit(
                    f"R2 Semis: batch={size} "
                    f"(rep {rep+1}/{self.REPS}) "
                    f"[{self._consumed} translated]")

                eps, elapsed = self._timed_translate(batch, size)
                r2_raw[size].append(eps)
                self.step_done.emit(size, eps, elapsed)

        # Average round 2
        r2_avg = {}
        for size, eps_list in r2_raw.items():
            if eps_list:
                r2_avg[size] = sum(eps_list) / len(eps_list)
                log.info("R2 avg: batch=%d → %.2f eps (%d reps)",
                         size, r2_avg[size], len(eps_list))

        if not r2_avg:
            optimal = top3_sizes[0]
            log.info("R2 had no data, using R1 winner: %d", optimal)
            self.progress.emit(f"Optimal batch_size={optimal} (R1 only)")
            self.finished.emit(optimal)
            return

        # Pick top 2
        top2 = sorted(r2_avg.items(), key=lambda r: r[1], reverse=True)[:2]
        top2_sizes = [s for s, _ in top2]
        log.info("R2 top 2: %s", [(s, f"{e:.2f}") for s, e in top2])
        self.progress.emit(
            f"R2 done — top 2: {top2_sizes} "
            f"[{self._consumed} translated]")

        # ── Round 3: Finals — top 2, REPS more batches each ────────
        r3_raw = {}  # size → [eps, ...]

        for size in top2_sizes:
            if self._cancelled:
                break
            r3_raw[size] = []

            for rep in range(self.REPS):
                if self._cancelled:
                    break
                if offset + size > len(available):
                    log.info("R3: out of entries at size=%d rep=%d", size, rep)
                    break

                batch = available[offset:offset + size]
                offset += size

                self.progress.emit(
                    f"R3 Finals: batch={size} "
                    f"(rep {rep+1}/{self.REPS}) "
                    f"[{self._consumed} translated]")

                eps, elapsed = self._timed_translate(batch, size)
                r3_raw[size].append(eps)
                self.step_done.emit(size, eps, elapsed)

        # ── Pick winner: combine R2 + R3 samples for finalists ──────
        combined = {}
        for size in top2_sizes:
            all_eps = r2_raw.get(size, []) + r3_raw.get(size, [])
            if all_eps:
                combined[size] = sum(all_eps) / len(all_eps)
                log.info("Finals: batch=%d → %.2f avg eps (%d total samples)",
                         size, combined[size], len(all_eps))

        if combined:
            # Tiebreaker: prefer larger batch (more shared context = better quality)
            optimal = max(combined, key=lambda s: (combined[s], s))
        elif r2_avg:
            optimal = max(r2_avg, key=lambda s: (r2_avg[s], s))
        else:
            optimal = top3_sizes[0]

        log.info("Auto-tuner: winner batch_size=%d [%d entries translated]",
                 optimal, self._consumed)
        self.progress.emit(f"Winner: batch_size={optimal} [{self._consumed} translated]")
        self.finished.emit(optimal)

    def _timed_translate(self, entries: list, batch_size: int) -> tuple:
        """Translate a batch and return (entries_per_sec, elapsed_seconds)."""
        try:
            start = time.perf_counter()
            success = self._translate_and_emit(entries, batch_size)
            elapsed = time.perf_counter() - start
            eps = success / elapsed if elapsed > 0 and success > 0 else 0.0
            return eps, elapsed
        except Exception as e:
            log.warning("Auto-tuner: batch=%d failed: %s", batch_size, e)
            self.error.emit(f"batch={batch_size} failed: {e}")
            return 0.0, 0.0

    def _translate_and_emit(self, entries: list, batch_size: int) -> int:
        """Translate entries via client.translate_batch and emit results.

        Returns the number of successfully translated entries.
        """
        payload = [
            (f"Line{j+1}", e.original, e.context, e.field)
            for j, e in enumerate(entries)
        ]
        key_to_entry = {f"Line{j+1}": e for j, e in enumerate(entries)}

        results = self.client.translate_batch(payload, history=None)

        success = 0
        for key, translation in results.items():
            entry = key_to_entry.get(key)
            if entry and translation:
                preview = translation[:50].replace("\n", " ")
                self.item_processed.emit(preview)
                self.entry_done.emit(entry.id, translation)
                success += 1

        self._consumed += success
        return success
