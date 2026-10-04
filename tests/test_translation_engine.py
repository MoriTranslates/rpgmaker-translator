"""Headless TranslationEngine / worker tests (QT_QPA_PLATFORM=offscreen)."""

import time

import pytest

import fake_ai
from conftest import FAKE_MV_GAME
from translator import CONTROL_CODE_RE

pytest.importorskip("PyQt6")


def _entries():
    from translator.rpgmaker_mv import RPGMakerMVParser
    return RPGMakerMVParser().load_project(FAKE_MV_GAME)


def _run_engine(qapp, engine, start, timeout=30.0):
    """Start the engine and pump the Qt event loop until `finished`."""
    done = []
    engine.finished.connect(lambda: done.append(True))
    start()
    deadline = time.monotonic() + timeout
    while not done and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    assert done, "engine did not finish in time"


def _wire(engine, entries):
    """Mirror main_window: apply entry_done results to the entries."""
    by_id = {e.id: e for e in entries}
    errors = []

    def on_done(eid, text):
        e = by_id[eid]
        e.translation = text
        e.status = "translated"

    engine.entry_done.connect(on_done)
    engine.error.connect(lambda eid, msg: errors.append((eid, msg)))
    return errors


@pytest.mark.parametrize("batch_size,workers", [(1, 1), (1, 3), (5, 2)])
def test_engine_translates_everything(qapp, fake_llm, batch_size, workers):
    from translator.ai_client import AIClient
    from translator.translation_engine import TranslationEngine
    entries = _entries()
    engine = TranslationEngine(AIClient())
    engine.batch_size = batch_size
    engine.num_workers = workers
    errors = _wire(engine, entries)
    checkpoints = []
    engine.checkpoint.connect(lambda: checkpoints.append(1))
    progress = []
    engine.progress.connect(lambda c, t, s: progress.append((c, t)))

    _run_engine(qapp, engine, lambda: engine.translate_batch(entries))

    assert errors == []
    assert all(e.status == "translated" for e in entries)
    for e in entries:
        assert CONTROL_CODE_RE.findall(e.translation) == \
            CONTROL_CODE_RE.findall(e.original), e.id
    assert not engine.is_running
    assert progress and progress[-1][0] == progress[-1][1] == len(entries)
    assert len(checkpoints) == len(entries) // engine.CHECKPOINT_INTERVAL
    d = {e.id: e for e in entries}
    assert d["Map001.json/Ev1(EV001)/p0/dialog_1"].translation.startswith(
        "\\C[2]\\N[2]\\C[0], Good morning!")


def test_batch_worker_falls_back_to_single_on_bad_json(qapp, monkeypatch):
    from translator.ai_client import AIClient
    from translator.translation_engine import TranslationEngine
    llm = fake_ai.install(monkeypatch, batch_mode="garbage")
    entries = [e for e in _entries() if e.file == "Map001.json"]
    engine = TranslationEngine(AIClient())
    engine.batch_size = 4
    engine.num_workers = 1
    errors = _wire(engine, entries)
    _run_engine(qapp, engine, lambda: engine.translate_batch(entries))
    assert errors == []
    assert all(e.status == "translated" for e in entries)
    # Both JSON batch attempts and single-entry fallbacks were made
    assert any(c.get("format") == "json" for c in llm.chat_calls())
    assert any(c.get("format") != "json" for c in llm.chat_calls())


def test_translation_memory_prefill_skips_llm(qapp, fake_llm):
    from translator.ai_client import AIClient
    from translator.project_model import TranslationEntry
    from translator.translation_engine import TranslationEngine
    entries = [
        TranslationEntry("a", "f", "dialog", "はい", "Yes!", "reviewed"),
        TranslationEntry("b", "f", "dialog", "はい"),
    ]
    engine = TranslationEngine(AIClient())
    _wire(engine, entries)
    _run_engine(qapp, engine, lambda: engine.translate_batch(entries))
    assert entries[1].translation == "Yes!"
    assert fake_llm.chat_calls() == []


def test_polish_mode(qapp, fake_llm):
    from translator.ai_client import AIClient
    from translator.project_model import TranslationEntry
    from translator.translation_engine import TranslationEngine
    entries = [TranslationEntry(f"Map001.json/Ev1(EV001)/p0/dialog_{i}",
                                "Map001.json", "dialog", "原文",
                                f"hello \\C[2]friend\\C[0] {i}", "translated")
               for i in range(3)]
    engine = TranslationEngine(AIClient())
    engine.batch_size = 1
    _wire(engine, entries)
    _run_engine(qapp, engine, lambda: engine.polish_batch(entries))
    assert [e.translation for e in entries] == [
        f"Hello \\C[2]friend\\C[0] {i}" for i in range(3)]


def test_worker_runs_synchronously_and_keeps_history(fake_llm):
    """TranslationWorker.run() can be driven directly without threads."""
    from translator.ai_client import AIClient
    from translator.translation_engine import TranslationWorker
    entries = [e for e in _entries()
               if e.id.startswith("Map001.json/Ev2(EV002)/")]
    worker = TranslationWorker(AIClient(), entries, max_history=10)
    got = []
    worker.entry_done.connect(lambda eid, t: got.append((eid, t)))
    worker.run()
    assert [g[0] for g in got] == [e.id for e in entries]
    # The 2nd request carried the 1st translation as an assistant message
    second = fake_llm.chat_calls()[1]["messages"]
    assert any(m["role"] == "assistant" for m in second)


def test_worker_reports_errors(monkeypatch):
    from translator.ai_client import AIClient
    from translator.project_model import TranslationEntry
    from translator.translation_engine import TranslationWorker
    fake_ai.install(monkeypatch, translate_fn=lambda t: "")   # empty = error
    worker = TranslationWorker(AIClient(), [TranslationEntry("a", "f", "dialog", "はい")])
    errs = []
    worker.error.connect(lambda eid, msg: errs.append(eid))
    worker.run()
    assert errs == ["a"]


def test_group_and_distribute_events():
    from translator.translation_engine import _group_by_event, _distribute_events
    entries = _entries()
    buckets, flat = _group_by_event(entries)
    keys = {b[0].id.rsplit("/", 1)[0] for b in buckets}
    assert "Map001.json/Ev1(EV001)/p0" in keys
    assert all(e.field not in ("dialog", "choice") for e in flat)
    assigned = _distribute_events(buckets, flat, 3)
    flat_out = [e for w in assigned for ev in w for e in ev]
    assert sorted(x.id for x in flat_out) == sorted(
        x.id for x in [e for b in buckets for e in b] + flat)
