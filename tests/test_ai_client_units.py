"""Unit tests for AIClient helpers and translation_engine pure functions.

No network / no fake HTTP layer: low-level pieces are called directly, and
where a request is needed ``AIClient._chat`` is monkeypatched.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
import requests  # noqa: E402

from translator.ai_client import (  # noqa: E402
    AIClient, RateLimited, ServerUnreachable,
)
from translator.translation_engine import (  # noqa: E402
    BatchTranslationWorker, _distribute_events, _group_by_event,
    _is_server_down_error,
)


@pytest.fixture
def client():
    return AIClient()


# ── _parse_batch_response ─────────────────────────────────────────

KEYS = ["Line1", "Line2"]


def test_parse_clean_json():
    raw = '{"Line1": "Hello", "Line2": "World"}'
    assert AIClient._parse_batch_response(raw, KEYS) == {"Line1": "Hello", "Line2": "World"}


def test_parse_fenced_json():
    raw = '```json\n{"Line1": "Hello", "Line2": "World"}\n```'
    assert AIClient._parse_batch_response(raw, KEYS) == {"Line1": "Hello", "Line2": "World"}


def test_parse_with_preamble():
    raw = 'Sure! Here is the translation:\n{"Line1": "Hello", "Line2": "World"}\nDone.'
    assert AIClient._parse_batch_response(raw, KEYS) == {"Line1": "Hello", "Line2": "World"}


def test_parse_list_value_joined_with_newlines():
    raw = json.dumps({"Line1": ["First line", "Second line"], "Line2": "Ok"})
    out = AIClient._parse_batch_response(raw, KEYS)
    assert out["Line1"] == "First line\nSecond line"
    assert out["Line2"] == "Ok"


def test_parse_rejects_non_string_values():
    raw = json.dumps({"Line1": {"text": "x"}, "Line2": "Ok", "Line3": "extra"})
    out = AIClient._parse_batch_response(raw, KEYS)
    assert out == {"Line2": "Ok"}


def test_parse_garbage_raises_value_error():
    with pytest.raises(ValueError):
        AIClient._parse_batch_response("not json at all", KEYS)
    with pytest.raises(ValueError):
        AIClient._parse_batch_response('{"Other": "x"}', KEYS)


# ── placeholder extraction / restoration ──────────────────────────

def test_extract_restore_round_trip_many_codes(client):
    text = "".join(f"\\C[{i}]語{i}" for i in range(12)) + "\\N[1]"
    clean, mapping = client._extract_codes(text)
    assert len(mapping) == 13
    assert "\\C[" not in clean and "\\N[" not in clean
    assert "«CODE10»" in clean and "«CODE13»" in clean
    # «CODE1» must not clobber «CODE10»..«CODE13»
    assert client._restore_codes(clean, mapping) == text


@pytest.mark.parametrize("mangled", [
    "<<CODE1>>Hi <<CODE2>>",
    "[CODE1]Hi [CODE2]",
    "« CODE 1 »Hi «CODE2>>",
])
def test_restore_normalizes_mangled_placeholders(client, mangled):
    _, mapping = client._extract_codes("\\C[2]やあ\\C[0]")
    assert client._restore_codes(mangled, mapping) == "\\C[2]Hi \\C[0]"


# ── event grouping / distribution ─────────────────────────────────

def _entry(eid, field):
    return SimpleNamespace(id=eid, field=field, original="x", translation="",
                           status="untranslated", context="")


def test_group_and_distribute_invariants():
    entries = []
    for m in range(5):
        for d in range(m + 1):
            entries.append(_entry(f"Map00{m}.json/Ev1(EV001)/p0/dialog_{d}", "dialog"))
    entries += [_entry(f"Items.json/{i}/name", "name") for i in range(7)]
    entries.insert(3, _entry("CommonEvents.json/CE2(x)/choice_0", "choice"))

    buckets, flat = _group_by_event(entries)
    # every entry exactly once
    flat_ids = [e.id for b in buckets for e in b] + [e.id for e in flat]
    assert sorted(flat_ids) == sorted(e.id for e in entries)
    assert len(flat) == 7
    # each bucket is a single event, original order kept
    for b in buckets:
        assert len({e.id.rsplit("/", 1)[0] for e in b}) == 1
        idx = [entries.index(e) for e in b]
        assert idx == sorted(idx)

    for n in (1, 2, 3, 8):
        assignments = _distribute_events(buckets, flat, n)
        assert len(assignments) == n
        seen = [e.id for worker in assignments for group in worker for e in group]
        assert sorted(seen) == sorted(e.id for e in entries)
        # an event bucket is never split across workers
        for b in buckets:
            owners = [w for w, worker in enumerate(assignments)
                      if any(g is b for g in worker)]
            assert len(owners) == 1


# ── server-down classification ────────────────────────────────────

@pytest.mark.parametrize("exc,expected", [
    (ServerUnreachable("down"), True),
    (requests.exceptions.ConnectionError("refused"), True),
    (requests.exceptions.ReadTimeout("slow"), True),
    (ConnectionRefusedError("refused"), True),
    (ConnectionError("Model 'x' not found"), False),
    (ConnectionError("Ollama error 500: boom"), False),
    (RateLimited("429"), False),
    (ValueError("Empty response for batch translation"), False),
    (ValueError("read timeout in message text only"), False),
])
def test_is_server_down_error(exc, expected):
    assert _is_server_down_error(exc) is expected


def test_new_exceptions_are_connection_errors():
    assert issubclass(ServerUnreachable, ConnectionError)
    assert issubclass(RateLimited, ConnectionError)


# ── note / quote / think stripping ────────────────────────────────

def test_strip_notes_keeps_separator_line_present_in_source():
    out = AIClient._strip_notes("I...\n——\nNo way!", "私…\n——\nまさか！")
    assert out == "I...\n——\nNo way!"


def test_strip_notes_keeps_jp_bar_separator_source():
    out = AIClient._strip_notes("I...\n——\nNo way!", "私…\n――\nまさか！")
    assert out == "I...\n——\nNo way!"


def test_strip_notes_removes_trailing_tl_note():
    out = AIClient._strip_notes("Let's go.\nTL Note: literally 'let us depart'.",
                                "行こう。")
    assert out == "Let's go."
    out = AIClient._strip_notes("Let's go.\n(Translator's note: pun)", "行こう。")
    assert out == "Let's go."


def test_strip_notes_keeps_bare_note_dialogue():
    text = "Hey.\nNote: the door is locked."
    assert AIClient._strip_notes(text, "おい。\nメモ：扉は施錠されている。") == text


def test_strip_notes_separator_without_source_still_stripped():
    assert AIClient._strip_notes("Hello.\n---\nThis means hi.", "こんにちは。") == "Hello."


def test_quote_strip_keeps_internal_quotes(client):
    s = '"Yes," she said, "go."'
    assert client._postprocess_result(s, {}, "「はい」と彼女は言った") == s


def test_quote_strip_removes_single_wrapping_pair(client):
    assert client._postprocess_result('"Good morning."', {}, "おはよう。") == "Good morning."


def test_quote_strip_keeps_when_source_quoted(client):
    assert client._postprocess_result('"Good morning."', {}, '"おはよう。"') == '"Good morning."'


def test_strip_thinking_unclosed():
    assert AIClient._strip_thinking("<think>reasoning that never ends") == ""
    assert AIClient._strip_thinking("Hello<think>trailing") == "Hello"
    assert AIClient._strip_thinking("<think>a</think>\nHello") == "Hello"


# ── speaker / code hints ──────────────────────────────────────────

def test_speaker_hint_matches_english_name(client):
    client.actor_names = {1: "さくら"}
    client.actor_names_en = {1: "Sakura"}
    client.actor_genders = {1: "female"}
    hint = client._build_speaker_hint("[Speaker: Sakura]\nprev line")
    assert "Sakura is FEMALE" in hint
    assert "FEMALE" in client._build_speaker_hint("[Speaker: さくら]")


def test_batch_code_hints_labelled_per_line(client, monkeypatch):
    client.actor_names = {1: "ユウ", 2: "さくら"}
    client.actor_names_en = {1: "Yuu", 2: "Sakura"}
    client.actor_genders = {1: "male", 2: "female"}
    sent = {}

    def fake_chat(*, messages, **kwargs):
        sent["messages"] = messages
        sent["options"] = kwargs.get("options", {})
        sent["json_schema"] = kwargs.get("json_schema")
        return {"message": {"content": json.dumps({
            "Line1": "«CODE1» smiled.", "Line2": "«CODE1» waved."})}}

    monkeypatch.setattr(client, "_chat", fake_chat)
    out = client.translate_batch([
        ("Line1", "\\N[1]は笑った。", "", "dialog"),
        ("Line2", "\\N[2]は手を振った。", "", "dialog"),
    ])
    assert out == {"Line1": "\\N[1] smiled.", "Line2": "\\N[2] waved."}
    user = sent["messages"][-1]["content"]
    assert "Line1 «CODE1» = name of Yuu (he/him)" in user
    assert "Line2 «CODE1» = name of Sakura (she/her)" in user
    # local batch now sets explicit budgets
    assert sent["options"]["num_predict"] >= 2048
    assert 4096 <= sent["options"]["num_ctx"] <= 16384


def test_batch_leftover_placeholder_retries_single(client, monkeypatch):
    calls = []

    def fake_chat(*, messages, **kwargs):
        calls.append(kwargs.get("format"))
        if kwargs.get("format") == "json":
            # Model invented a «CODE2» that has no mapping
            return {"message": {"content": json.dumps(
                {"Line1": "Hi «CODE2»", "Line2": "Bye"})}}
        return {"message": {"content": "Hi"}}

    monkeypatch.setattr(client, "_chat", fake_chat)
    out = client.translate_batch([
        ("Line1", "\\C[2]やあ", "", "dialog"),
        ("Line2", "じゃあね", "", "dialog"),
    ])
    assert out["Line2"] == "Bye"
    assert out["Line1"] == "Hi"          # came from the single-entry retry
    assert calls == ["json", None]


def test_batch_retry_failure_leaves_key_out(client, monkeypatch):
    def fake_chat(*, messages, **kwargs):
        if kwargs.get("format") == "json":
            return {"message": {"content": json.dumps(
                {"Line1": "まだ日本語", "Line2": "Bye"})}}
        raise ServerUnreachable("down")

    monkeypatch.setattr(client, "_chat", fake_chat)
    out = client.translate_batch([
        ("Line1", "まだ日本語です", "", "dialog"),
        ("Line2", "じゃあね", "", "dialog"),
    ])
    assert out == {"Line2": "Bye"}


def test_translate_empty_output_is_value_error(client, monkeypatch):
    monkeypatch.setattr(client, "_chat",
                        lambda **kw: {"message": {"content": "<think>x</think>"}})
    with pytest.raises(ValueError):
        client.translate("こんにちは")


def test_translate_keeps_server_unreachable_type(client, monkeypatch):
    def boom(**kw):
        raise ServerUnreachable("down")
    monkeypatch.setattr(client, "_chat", boom)
    with pytest.raises(ServerUnreachable):
        client.translate("こんにちは")


def test_polish_raises_on_error_and_keeps_original_on_empty(client, monkeypatch):
    def boom(**kw):
        raise ServerUnreachable("down")
    monkeypatch.setattr(client, "_chat", boom)
    with pytest.raises(ConnectionError):
        client.polish("Hello there.")
    monkeypatch.setattr(client, "_chat", lambda **kw: {"message": {"content": ""}})
    assert client.polish("Hello there.") == "Hello there."


def test_polish_uses_polish_model_without_mutating_client(client, monkeypatch):
    client.model = "main-model"
    client.polish_model = "polish-model"
    seen = []

    def fake_chat(*, messages, model=None, **kw):
        seen.append(model)
        assert client.model == "main-model"
        if kw.get("format") == "json":
            return {"message": {"content": '{"Line1": "Polished."}'}}
        return {"message": {"content": "Polished."}}

    monkeypatch.setattr(client, "_chat", fake_chat)
    assert client.polish("polish me") == "Polished."
    client.polish_batch([("Line1", "polish me")])
    assert seen == ["polish-model", "polish-model"]


def test_history_pairs_use_placeholders(client, monkeypatch):
    sent = {}

    def fake_chat(*, messages, **kw):
        sent["messages"] = messages
        return {"message": {"content": "Okay."}}

    monkeypatch.setattr(client, "_chat", fake_chat)
    client.translate("うん", history=[("\\C[2]やあ", "\\C[2]Hi")])
    hist = [m["content"] for m in sent["messages"][1:-1]]
    assert all("\\C[" not in h for h in hist)
    assert "«CODE1»" in hist[0] and "«CODE1»" in hist[1]


def test_ollama_payload_drops_json_schema(client, monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"message": {"content": "ok"}}

    def fake_post(url, json=None, timeout=None, **kw):  # noqa: A002
        captured.update(json)
        return _Resp()

    monkeypatch.setattr(requests, "post", fake_post)
    client._chat(messages=[], json_schema={"x": 1}, model="other")
    assert "json_schema" not in captured
    assert captured["model"] == "other"


def test_chat_connection_failure_is_server_unreachable(client, monkeypatch):
    def fake_post(*a, **kw):
        raise requests.exceptions.ConnectionError("refused")
    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(ServerUnreachable):
        client._chat(messages=[])


# ── batch worker: parse failure skips identical retry ─────────────

class _FakeBatchClient:
    def __init__(self):
        import threading
        self.cancel_event = threading.Event()
        self.batch_calls = 0
        self.single_calls = 0

    def translate_batch(self, payload, history=None):
        self.batch_calls += 1
        raise ValueError("Could not parse JSON")

    def translate(self, text, context="", field="", history=None):
        self.single_calls += 1
        return "EN:" + text


def test_batch_worker_value_error_goes_straight_to_fallback():
    fake = _FakeBatchClient()
    entries = [_entry(f"Map001.json/Ev1(EV001)/p0/dialog_{i}", "dialog") for i in range(4)]
    worker = BatchTranslationWorker(fake, entries=None, events=[entries],
                                    batch_size=4, max_history=0)
    done, finished = [], []
    worker.entry_done.connect(lambda eid, tl: done.append(eid))
    worker.finished.connect(lambda: finished.append(True))
    worker.run()
    assert fake.batch_calls == 1          # no identical deterministic retry
    assert fake.single_calls == 4
    assert len(done) == 4
    assert finished == [True]


def test_batch_worker_unexpected_error_still_finishes():
    class _Broken(_FakeBatchClient):
        def translate_batch(self, payload, history=None):
            raise KeyError("bug")

    worker = BatchTranslationWorker(_Broken(), entries=None,
                                    events=[[_entry("Map001.json/Ev1/p0/dialog_0", "dialog")]],
                                    batch_size=4, max_history=0)
    errors, finished = [], []
    worker.error.connect(lambda eid, msg: errors.append(eid))
    worker.finished.connect(lambda: finished.append(True))
    worker.run()
    assert errors == ["Map001.json/Ev1/p0/dialog_0"]
    assert finished == [True]
