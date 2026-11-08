"""No-credit replies from OpenAI and Anthropic stop the run once.

OpenAI reports used-up prepaid credit as HTTP 429 with code/type
``insufficient_quota``; Anthropic as HTTP 400 "credit balance is too low".
Neither can succeed on retry, so both must become OutOfCredits instead of a
rate-limit back-off. Gemini's daily free cap (same "exceeded your current
quota" wording) must stay DailyQuotaExceeded, and a per-minute 429 must still
back off. No network: ``openai.OpenAI`` is replaced with a fake.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from types import SimpleNamespace  # noqa: E402

from http_lib import httpx  # noqa: E402
import openai  # noqa: E402
import pytest  # noqa: E402

from translator import ai_client  # noqa: E402
from translator.ai_client import AIClient  # noqa: E402

QUOTA_MSG = ("You exceeded your current quota, please check your plan and "
             "billing details. For more information on this error, read the "
             "docs: https://platform.openai.com/docs/guides/error-codes/api-errors.")
ANTHROPIC_MSG = ("Your credit balance is too low to access the Anthropic API. "
                 "Please go to Plans & Billing to upgrade or purchase credits.")


def _error(cls, status, message, body):
    req = httpx.Request("POST", "https://example.invalid/chat/completions")
    resp = httpx.Response(status, request=req, json={"error": body})
    return cls(f"Error code: {status} - {message}", response=resp, body=body)


def _client(monkeypatch, provider, exc):
    class _C:
        def __init__(self, **kw):
            def create(**p):
                raise exc
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))

        def with_options(self, **kw):
            return self
    monkeypatch.setattr(openai, "OpenAI", _C)
    c = AIClient()
    c.provider, c.model, c.api_key = provider, "m", "k"
    return c


def _call(c):
    return c._chat_openai(messages=[{"role": "user", "content": "hi"}])


def test_openai_insufficient_quota_429_is_out_of_credits(monkeypatch):
    # The SDK hands the inner "error" object over as the body.
    exc = _error(openai.RateLimitError, 429, QUOTA_MSG, {
        "message": QUOTA_MSG, "type": "insufficient_quota",
        "param": None, "code": "insufficient_quota"})
    c = _client(monkeypatch, "OpenAI", exc)
    with pytest.raises(ai_client.OutOfCredits):
        _call(c)


def test_openai_insufficient_quota_wrapped_body_is_out_of_credits(monkeypatch):
    # Same reply with the outer {"error": {...}} wrapper kept in the body.
    exc = _error(openai.RateLimitError, 429, QUOTA_MSG, {"error": {
        "message": QUOTA_MSG, "type": "insufficient_quota",
        "param": None, "code": "insufficient_quota"}})
    c = _client(monkeypatch, "OpenAI", exc)
    with pytest.raises(ai_client.OutOfCredits):
        _call(c)


def test_anthropic_low_credit_400_is_out_of_credits(monkeypatch):
    exc = _error(openai.BadRequestError, 400, ANTHROPIC_MSG, {
        "type": "invalid_request_error", "message": ANTHROPIC_MSG})
    c = _client(monkeypatch, "Anthropic", exc)
    with pytest.raises(ai_client.OutOfCredits):
        _call(c)


def test_openai_plain_rate_limit_still_backs_off(monkeypatch):
    msg = ("Rate limit reached for gpt-4o-mini in organization org-x on "
           "requests per min (RPM): Limit 3, Used 3, Requested 1.")
    exc = _error(openai.RateLimitError, 429, msg, {
        "message": msg, "type": "requests", "param": None,
        "code": "rate_limit_exceeded"})
    c = _client(monkeypatch, "OpenAI", exc)
    with pytest.raises(ai_client.RateLimited) as ei:
        _call(c)
    assert not isinstance(ei.value, ai_client.FatalProviderError)


def test_gemini_daily_quota_stays_daily(monkeypatch):
    body = [{"error": {
        "code": 429, "message": "You exceeded your current quota, please "
        "check your plan and billing details.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                     "violations": [{"quotaId": "GenerateRequestsPerDayPerProject"
                                                "PerModel-FreeTier"}]}]}}]
    req = httpx.Request("POST", "https://example.invalid/chat/completions")
    resp = httpx.Response(429, request=req, json=body)
    exc = openai.RateLimitError("Error code: 429 - quota", response=resp, body=body)
    c = _client(monkeypatch, "Google Gemini", exc)
    with pytest.raises(ai_client.DailyQuotaExceeded):
        _call(c)


def test_gemini_per_minute_quota_still_backs_off(monkeypatch):
    body = [{"error": {
        "code": 429, "message": "You exceeded your current quota, please "
        "check your plan and billing details.",
        "status": "RESOURCE_EXHAUSTED",
        "details": [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                     "violations": [{"quotaId": "GenerateRequestsPerMinute"
                                                "PerProjectPerModel-FreeTier"}]}]}}]
    req = httpx.Request("POST", "https://example.invalid/chat/completions")
    resp = httpx.Response(429, request=req, json=body)
    exc = openai.RateLimitError("Error code: 429 - quota", response=resp, body=body)
    c = _client(monkeypatch, "Google Gemini", exc)
    with pytest.raises(ai_client.RateLimited) as ei:
        _call(c)
    assert not isinstance(ei.value, ai_client.FatalProviderError)


def test_other_400_is_not_out_of_credits(monkeypatch):
    msg = "max_tokens: 99999 > 8192, which is the maximum allowed"
    exc = _error(openai.BadRequestError, 400, msg, {
        "type": "invalid_request_error", "message": msg})
    c = _client(monkeypatch, "Anthropic", exc)
    with pytest.raises(ConnectionError) as ei:
        _call(c)
    assert not isinstance(ei.value, ai_client.OutOfCredits)
