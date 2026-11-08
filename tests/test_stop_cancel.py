"""Stop must stop: cancellable requests, fatal errors that propagate,
daily-quota 429s that end the run once, and no "stop it now?" nag loop.

No network: requests.post and the OpenAI SDK are replaced with fakes.
User report: cloud runs (free OpenRouter / Gemini) got stuck, and pressing
Stop kept asking to stop again while workers sat in 17-minute requests.
"""

import os
import sys
import threading
import time
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json  # noqa: E402

import openai  # noqa: E402
import pytest  # noqa: E402
import requests  # noqa: E402

from translator import ai_client  # noqa: E402
from translator.ai_client import AIClient  # noqa: E402
from translator.project_model import TranslationEntry  # noqa: E402

pytest.importorskip("PyQt6")


def _entries(n):
    return [TranslationEntry(id=f"Map001.json/Ev1(EV001)/p0/dialog_{i}",
                             file="Map001.json", field="dialog",
                             original=f"日本語{i}") for i in range(n)]


def _pump_until(qapp, cond, timeout_s):
    deadline = time.monotonic() + timeout_s
    while not cond() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()
    return cond()


class _BlockingPost:
    """requests.post that blocks until released (a hung / very slow server)."""

    def __init__(self):
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, url, json=None, timeout=None, **kw):  # noqa: A002
        self.calls += 1
        self.entered.set()
        self.release.wait(30)
        raise requests.exceptions.ConnectionError("released by test")


# ── 1. cancel interrupts an in-flight request ────────────────────────

@pytest.mark.parametrize("batch_size", [1, 4])
def test_cancel_during_slow_request_finishes_promptly(qapp, monkeypatch, batch_size):
    from translator.translation_engine import TranslationEngine
    post = _BlockingPost()
    monkeypatch.setattr(ai_client.requests, "post", post)
    client = AIClient()
    engine = TranslationEngine(client)
    engine.batch_size = batch_size
    engine.num_workers = 1
    finished, errors, downs = [], [], []
    engine.finished.connect(lambda: finished.append(1))
    engine.error.connect(lambda eid, m: errors.append(eid))
    engine.server_down.connect(downs.append)
    entries = _entries(6)
    try:
        engine.translate_batch(entries)
        assert _pump_until(qapp, post.entered.is_set, 5)
        calls_at_cancel = post.calls
        t0 = time.monotonic()
        engine.cancel()
        done = _pump_until(qapp, lambda: bool(finished), 5)
        elapsed = time.monotonic() - t0
    finally:
        post.release.set()
        _pump_until(qapp, lambda: not engine.is_running, 10)
    assert done, "engine did not finish after cancel"
    assert elapsed < 2.5, f"cancel took {elapsed:.2f}s"
    assert post.calls == calls_at_cancel, "a request was sent after cancel"
    assert errors == [] and downs == []
    assert all(e.status == "untranslated" for e in entries)


def test_no_request_sent_once_cancelled(monkeypatch):
    post = _BlockingPost()
    monkeypatch.setattr(ai_client.requests, "post", post)
    client = AIClient()
    client.cancel_event.set()
    with pytest.raises(getattr(ai_client, "RequestCancelled", type("_Missing", (Exception,), {}))):
        client._chat(messages=[{"role": "user", "content": "hi"}])
    assert post.calls == 0


def test_cloud_request_wait_is_cancellable(monkeypatch):
    """A cloud call blocked in the SDK returns within ~1 s of cancel."""
    gate = threading.Event()

    class _Slow:
        def __init__(self, **kw):
            def create(**p):
                gate.wait(30)
                raise openai.APIConnectionError(request=None)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        def with_options(self, **kw):
            return self

    monkeypatch.setattr(openai, "OpenAI", _Slow)
    c = AIClient()
    c.provider, c.model, c.api_key = "OpenRouter", "x/y:free", "k"
    threading.Timer(0.3, c.cancel_event.set).start()
    t0 = time.monotonic()
    try:
        with pytest.raises(ConnectionError) as ei:
            c._chat(messages=[{"role": "user", "content": "hi"}])
        elapsed = time.monotonic() - t0
    finally:
        gate.set()
    assert elapsed < 2.0, f"cancel took {elapsed:.2f}s"
    assert type(ei.value).__name__ == "RequestCancelled"
    assert not isinstance(ei.value, ai_client.ServerUnreachable)


def test_cloud_timeout_is_capped_local_is_not(monkeypatch):
    seen = {}

    class _Rec:
        def __init__(self, **kw):
            msg = SimpleNamespace(content="ok")
            resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **p: resp))

        def with_options(self, **kw):
            seen["cloud"] = kw.get("timeout")
            return self

    monkeypatch.setattr(openai, "OpenAI", _Rec)
    c = AIClient()
    c.provider, c.model, c.api_key = "OpenRouter", "x/y", "k"
    c._chat(messages=[{"role": "user", "content": "hi"}], timeout=120 + 30 * 30)
    assert seen["cloud"] <= 300

    def fake_post(url, json=None, timeout=None, **kw):  # noqa: A002
        seen["local"] = timeout
        return SimpleNamespace(status_code=200, json=lambda: {"message": {"content": "ok"}})
    monkeypatch.setattr(ai_client.requests, "post", fake_post)
    local = AIClient()
    local._chat(messages=[{"role": "user", "content": "hi"}], timeout=1020)
    assert seen["local"] == 1020


def test_cancel_during_rate_limit_wait_leaves_entry_untranslated():
    """A Stop during a 429 back-off must not mark the line as an error."""
    from translator.translation_engine import TranslationWorker

    class _Limited:
        def __init__(self):
            self.cancel_event = threading.Event()
            self.calls = 0

        def translate(self, **kw):
            self.calls += 1
            self.cancel_event.set()   # user presses Stop during the wait
            raise ai_client.RateLimited("Rate limit exceeded for OpenRouter")

    c = _Limited()
    w = TranslationWorker(c, _entries(3), max_history=0)
    errors = []
    w.error.connect(lambda eid, m: errors.append(eid))
    w.run()
    assert errors == []
    assert c.calls == 1


# ── 3. fatal errors are never swallowed by retry loops ───────────────

def test_fatal_in_japanese_retry_propagates(monkeypatch):
    c = AIClient()
    replies = iter([{"message": {"content": "まだ日本語"}}])
    calls = []

    def fake_chat(**kw):
        calls.append(1)
        try:
            return next(replies)
        except StopIteration:
            raise ai_client.OutOfCredits("no credit left")
    monkeypatch.setattr(c, "_chat", fake_chat)
    with pytest.raises(ai_client.FatalProviderError):
        c.translate("日本語")
    assert len(calls) == 2


def test_fatal_in_batch_per_entry_retry_propagates(monkeypatch):
    c = AIClient()
    batch_reply = json.dumps({"Line1": "まだ日本語", "Line2": "まだ日本語", "Line3": "ok"})
    calls = []

    def fake_chat(**kw):
        calls.append(1)
        if len(calls) == 1:
            return {"message": {"content": batch_reply}}
        raise ai_client.FatalProviderError("OpenRouter answered with the paid model")
    monkeypatch.setattr(c, "_chat", fake_chat)
    payload = [(f"Line{i}", f"日本語{i}", "", "dialog") for i in (1, 2, 3)]
    with pytest.raises(ai_client.FatalProviderError):
        c.translate_batch(payload)
    assert len(calls) == 2, "kept sending after a fatal error"


def test_fatal_in_variants_propagates(monkeypatch):
    c = AIClient()
    calls = []

    def fake_chat(**kw):
        calls.append(1)
        raise ai_client.InvalidApiKey("bad key")
    monkeypatch.setattr(c, "_chat", fake_chat)
    with pytest.raises(ai_client.FatalProviderError):
        c.translate_variants("日本語")
    assert len(calls) == 1


# ── 4. daily quota 429 ends the run; per-minute 429 still backs off ──

def _rate_limit_error(body):
    from http_lib import httpx
    req = httpx.Request("POST", "https://example.invalid/chat/completions")
    resp = httpx.Response(429, request=req, json=body)
    msg = body[0]["error"]["message"] if isinstance(body, list) else body["error"]["message"]
    return openai.RateLimitError(f"Error code: 429 - {msg}", response=resp, body=body)


OPENROUTER_DAILY = {"error": {
    "message": "Rate limit exceeded: free-models-per-day. Add 10 credits to "
               "unlock 1000 free model requests per day",
    "code": 429,
    "metadata": {"headers": {"X-RateLimit-Limit": "50",
                             "X-RateLimit-Remaining": "0"}}}}
OPENROUTER_MINUTE = {"error": {
    "message": "Rate limit exceeded: free-models-per-min.", "code": 429}}
GEMINI_DAILY = [{"error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and "
               "billing details.",
    "status": "RESOURCE_EXHAUSTED",
    "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                 "violations": [{
                     "quotaMetric": "generativelanguage.googleapis.com/"
                                    "generate_content_free_tier_requests",
                     "quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}]
GEMINI_MINUTE = [{"error": {
    "code": 429,
    "message": "You exceeded your current quota, please check your plan and "
               "billing details.",
    "status": "RESOURCE_EXHAUSTED",
    "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                 "violations": [{
                     "quotaMetric": "generativelanguage.googleapis.com/"
                                    "generate_content_free_tier_requests",
                     "quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]}]}}]


def _raising_client(monkeypatch, body, calls):
    class _C:
        def __init__(self, **kw):
            def create(**p):
                calls.append(1)
                raise _rate_limit_error(body)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        def with_options(self, **kw):
            return self
    monkeypatch.setattr(openai, "OpenAI", _C)


@pytest.mark.parametrize("provider,body", [
    ("OpenRouter", OPENROUTER_DAILY), ("Google Gemini", GEMINI_DAILY)])
def test_daily_quota_429_is_fatal(monkeypatch, provider, body):
    _raising_client(monkeypatch, body, [])
    c = AIClient()
    c.provider, c.model, c.api_key = provider, "m", "k"
    with pytest.raises(ai_client.FatalProviderError, match="(?i)daily"):
        c._chat_openai(messages=[{"role": "user", "content": "hi"}])


@pytest.mark.parametrize("provider,body", [
    ("OpenRouter", OPENROUTER_MINUTE), ("Google Gemini", GEMINI_MINUTE)])
def test_per_minute_429_still_rate_limited(monkeypatch, provider, body):
    _raising_client(monkeypatch, body, [])
    c = AIClient()
    c.provider, c.model, c.api_key = provider, "m", "k"
    with pytest.raises(ai_client.RateLimited) as ei:
        c._chat_openai(messages=[{"role": "user", "content": "hi"}])
    assert not isinstance(ei.value, ai_client.FatalProviderError)


def test_daily_quota_stops_the_run_once(qapp, monkeypatch):
    from translator.translation_engine import TranslationEngine
    calls = []
    _raising_client(monkeypatch, OPENROUTER_DAILY, calls)
    c = AIClient()
    c.provider, c.model, c.api_key = "OpenRouter", "x/y:free", "k"
    engine = TranslationEngine(c)
    engine.batch_size = 4
    engine.num_workers = 1
    downs, finished, errors = [], [], []
    engine.server_down.connect(downs.append)
    engine.finished.connect(lambda: finished.append(1))
    engine.error.connect(lambda eid, m: errors.append(eid))
    entries = _entries(12)
    try:
        engine.translate_batch(entries)
        done = _pump_until(qapp, lambda: bool(finished), 10)
    finally:
        engine.cancel()
        _pump_until(qapp, lambda: not engine.is_running, 10)
    assert done
    assert len(calls) == 1
    assert len(downs) == 1 and "daily" in downs[0].lower()
    assert errors == []
    assert all(e.status == "untranslated" for e in entries)


# ── 5. the pause dialog names the real cause ─────────────────────────

def test_pause_dialog_text_matches_cause():
    from translator.widgets import main_window as mw
    title, text = mw.server_down_message(
        kind="fatal", reason="Daily free limit reached for OpenRouter",
        provider="OpenRouter", is_cloud=True, can_resume=True)
    assert "Ollama" not in text and "Ollama" not in title
    assert "Daily free limit reached for OpenRouter" in text
    title, text = mw.server_down_message(
        kind="server_down", reason="Cannot reach OpenRouter API",
        provider="OpenRouter", is_cloud=True, can_resume=True)
    assert "Ollama" not in text
    title, text = mw.server_down_message(
        kind="server_down", reason="refused", provider="Ollama (Local)",
        is_cloud=False, can_resume=False)
    assert "Ollama" in text


# ── 2. no "stop it now?" nag after Stop was pressed ──────────────────

def test_no_reask_after_stop(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from translator.widgets.main_window import MainWindow
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(1) or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    engine = SimpleNamespace(is_running=True, is_stopping=True, _cancelled=True)
    msgs = []
    fake = SimpleNamespace(
        _busy=False, engine=engine,
        statusbar=SimpleNamespace(showMessage=lambda *a, **k: msgs.append(a)),
        _stop_translation=lambda: None,
        _wait_for_engine_stop=lambda t: False,
    )
    assert MainWindow._ensure_idle_for_project_change(fake) is False
    assert asked == [], "asked to stop again after Stop was pressed"
    # Once the workers have exited, the change goes through without asking.
    fake._wait_for_engine_stop = lambda t: True
    assert MainWindow._ensure_idle_for_project_change(fake) is True
    assert asked == []
