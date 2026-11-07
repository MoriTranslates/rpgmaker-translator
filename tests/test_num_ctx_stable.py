"""Ollama restarts its model runner whenever options.num_ctx changes, so
per-request context sizes made it reload the model before every batch.
num_ctx sent to Ollama must come from a few fixed sizes and never shrink
for the same model within a session.

No network: requests.post is replaced with a fake that records payloads.
"""

import json
import re
from types import SimpleNamespace

import pytest

from translator import ai_client
from translator.ai_client import AIClient


@pytest.fixture
def sent(monkeypatch):
    payloads = []

    def post(url, json=None, timeout=None, **kw):
        payloads.append(json)
        msgs = json["messages"]
        if json.get("format") == "json":
            keys = re.findall(r'^\s*"([^"]+)":', msgs[-1]["content"], re.MULTILINE)
            content = _json.dumps({k: "Hello there" for k in keys})
        else:
            content = "Hello there"
        data = {"message": {"content": content}, "done": True, "done_reason": "stop"}
        return SimpleNamespace(status_code=200, text="", json=lambda: data)

    monkeypatch.setattr(ai_client.requests, "post", post)
    return payloads


_json = json


def _ctx(payload):
    return payload["options"]["num_ctx"]


def _batch(n, size):
    return [(f"Line{i + 1}", "あ" * size, "", "dialog") for i in range(n)]


def test_bucket_rounds_up_to_fixed_sizes_and_caps():
    b = ai_client._ctx_bucket
    assert b(100) == 4096
    assert b(4096) == 4096
    assert b(4097) == 8192          # just over a boundary: next bucket
    assert b(10354) == b(10558) == 12288
    assert b(16384) == 16384
    assert b(50000) == 16384        # never above the cap


def test_similar_batches_send_identical_num_ctx(sent):
    c = AIClient()
    c.translate_batch(_batch(5, 700))
    c.translate_batch(_batch(5, 760))   # slightly bigger estimate
    c.translate_batch(_batch(5, 650))   # slightly smaller estimate
    ctxs = [_ctx(p) for p in sent]
    assert len(set(ctxs)) == 1, ctxs
    assert ctxs[0] % 4096 == 0


def test_num_ctx_never_shrinks_for_the_same_model(sent):
    c = AIClient()
    c.translate_batch(_batch(6, 1500))      # big batch
    big = _ctx(sent[-1])
    assert big > 4096
    c.translate("おはよう")                  # small single line
    c.polish("Good morning")
    c.translate_names_batch([("a", "アリス", "name")])
    c.translate_variants("おはよう", count=2)
    assert {_ctx(p) for p in sent} == {big}


def test_num_ctx_never_exceeds_cap(sent):
    c = AIClient()
    c.translate_batch(_batch(6, 20000))
    assert _ctx(sent[-1]) == 16384


def test_num_ctx_is_tracked_per_model(sent):
    c = AIClient()
    c.translate_batch(_batch(6, 1500))
    big = _ctx(sent[-1])
    c.model = "other-model:7b"
    c.translate("おはよう")
    assert _ctx(sent[-1]) == 4096 < big
