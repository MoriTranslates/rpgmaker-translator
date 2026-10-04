"""Fake LLM backend for tests.

Replaces the HTTP layer used by ``translator.ai_client.AIClient`` so the
*real* client code (placeholder extraction, prompt building, batch JSON
parsing, code restoration, retries) runs end to end without an Ollama
server.

The seam is ``requests.post`` / ``requests.get`` as seen from
``translator.ai_client`` -- ``AIClient._chat()`` POSTs to ``/api/chat`` and
expects ``{"message": {"content": "..."}}`` back.  Batch calls send
``format="json"`` and put a pretty-printed JSON object at the end of the
last user message; the fake answers with a JSON object using the same keys.

Usage (pytest)::

    def test_x(fake_llm):              # fixture from conftest.py
        client = AIClient()
        client.translate("こんにちは")   # -> "Hello"
        fake_llm.calls                 # recorded request payloads

Usage (manual)::

    with patched_llm() as llm:
        ...
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from translator import JAPANESE_RE

PLACEHOLDER_RE = re.compile(r"«CODE\d+»")

# Phrase dictionary covering every Japanese string in the fake MV fixture
# plus the text-engine fixtures.  Longest keys are applied first.
JP_EN: dict[str, str] = {
    # Actors / DB
    "アリス姫": "Princess Alice",
    "アリス": "Alice",
    "見習い魔法使い": "Apprentice Mage",
    "明るい少女": "A cheerful girl",
    "魔法の勉強中": "Studying magic",
    "ボブ": "Bob",
    "勇敢な男の騎士": "A brave male knight",
    "騎士": "Knight",
    "剣士": "Swordsman",
    "謎の人物": "Mysterious Person",
    "勇者と魔王の物語": "Tale of the Hero and the Demon King",
    "勇者": "Hero",
    "ポーション": "Potion",
    "エーテル": "Ether",
    "回復する": " restored",
    "を50": " +50",
    "を20": " +20",
    "ファイアを唱えた": " casts Fire",
    "ファイア": "Fire",
    "攻撃": "Attack",
    "の攻撃": " attacks",
    "敵単体に炎のダメージ": "Fire damage to one enemy",
    "木の剣": "Wooden Sword",
    "ただの": "Just a ",
    "革の盾": "Leather Shield",
    "軽い盾": "A light shield",
    "スライムが現れた": "A slime appeared",
    "スライム×2": "Slime x2",
    "スライム": "Slime",
    "戦闘不能": "Knockout",
    "は倒れた": " falls",
    "を倒した": " was defeated",
    "は立ち上がった": " stands up",
    "には効かなかった": " was unaffected",
    "お金を": "Got ",
    "手に入れた": "",
    "の勝利": " is victorious",
    "戦う": "Fight",
    "逃げる": "Escape",
    "アイテム": "Item",
    "最大ＨＰ": "Max HP",
    "最大ＭＰ": "Max MP",
    "レベル": "Level",
    "ＨＰ": "HP",
    "物理": "Physical",
    "炎": "Fire",
    "魔法": "Magic",
    "剣": "Sword",
    "一般防具": "General Armor",
    "武器": "Weapon",
    "盾": "Shield",
    "所持金": "Gold",
    # Events
    "村長の話": "Chief's Talk",
    "村長": "Village Chief",
    "よく来たな、若者よ": "Welcome, young one",
    "村を守ってくれ": "Please protect the village",
    "はじまりの村": "Starting Village",
    "おはよう": "Good morning",
    "今日は": "Today I have ",
    "ゴールドを持っているよ": " gold with me",
    "一緒に冒険に行こう": "Let's go on an adventure together",
    "村人": "Villager",
    "ようこそ、旅の人": "Welcome, traveler",
    "ようこそ": "Welcome",
    "この村は平和です": "This village is peaceful",
    "はい": "Yes",
    "いいえ": "No",
    "ありがとう": "Thank you",
    "遠い昔": "Long ago",
    "世界は闇に包まれていた": "the world was shrouded in darkness",
    "宝箱": "Treasure Chest",
    "ねえ、聞いて": "Hey, listen",
    "誰もいない": "Nobody is here",
    "静かだ": "It's quiet",
    "冒険の書": "Adventure Log",
    "こんにちは": "Hello",
    # Text-engine fixtures
    "一行目": "Line one",
    "二行目": "line two",
    "テスト": "test",
    "先生": "Teacher",
    "おはようございます": "Good morning, sir",
    "どこへ行く": "Where to go",
    "学校": "School",
    "家に帰る": "Go home",
    "主人公": "Hero",
    "明日も会おう": "See you tomorrow",
    "了解": "Understood",
}

_JP_PUNCT = [
    ("……", "..."), ("…", "..."), ("。", ". "), ("、", ", "),
    ("！", "!"), ("？", "?"), ("「", '"'), ("」", '"'), ("『", '"'),
    ("』", '"'), ("（", "("), ("）", ")"), ("　", " "), ("×", "x"),
]

_SORTED_KEYS = sorted(JP_EN, key=len, reverse=True)


def fake_translate_line(line: str) -> str:
    """Deterministic JP->EN for one line.  «CODEn» placeholders survive."""
    out = line
    for jp in _SORTED_KEYS:
        if jp in out:
            out = out.replace(jp, JP_EN[jp])
    for jp, en in _JP_PUNCT:
        out = out.replace(jp, en)
    leftover = bool(JAPANESE_RE.search(out))
    if leftover:
        out = JAPANESE_RE.sub("", out)
    # Tidy whitespace without touching placeholders
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.!?])", r"\1", out)
    out = out.strip()
    if leftover:
        out = ("[EN] " + out).strip()
    return out


def fake_translate(text: str) -> str:
    """Translate a (possibly multi-line) string line by line."""
    return "\n".join(fake_translate_line(ln) for ln in text.split("\n"))


def fake_polish(text: str) -> str:
    """Deterministic 'polish': capitalise the first letter of each line."""
    lines = []
    for ln in text.split("\n"):
        s = ln.strip()
        if s and s[0].islower():
            s = s[0].upper() + s[1:]
        lines.append(s)
    return "\n".join(lines)


# ── Fake HTTP response / backend ─────────────────────────────────────

class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload, ensure_ascii=False)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@dataclass
class FakeLLM:
    """Stand-in for Ollama's /api/chat.

    Attributes:
        calls: every request payload received (dicts, in order).
        translate_fn / polish_fn: line translators (override to simulate
            misbehaving models, e.g. dropping placeholders).
        batch_mode: "ok" (valid JSON), "garbage" (non-JSON text) or
            "fenced" (```json fenced```).
        model_names: what GET /api/tags reports.
    """
    translate_fn: Callable[[str], str] = fake_translate
    polish_fn: Callable[[str], str] = fake_polish
    batch_mode: str = "ok"
    model_names: list = field(default_factory=lambda: ["qwen3.5:9b"])
    calls: list = field(default_factory=list)

    # -- request classification -------------------------------------

    @staticmethod
    def _last_user(messages: list) -> str:
        for m in reversed(messages):
            if m.get("role") == "user":
                return m.get("content", "")
        return ""

    @staticmethod
    def _extract_json_payload(content: str) -> dict:
        idx = content.rfind("\n\n{")
        if idx < 0:
            raise ValueError("no JSON payload in batch request")
        return json.loads(content[idx + 2:])

    def respond(self, payload: dict) -> str:
        messages = payload.get("messages", [])
        user = self._last_user(messages)
        polish = "Polish this:\n" in user or "Polish the following JSON" in user

        if payload.get("format") == "json":
            data = self._extract_json_payload(user)
            fn = self.polish_fn if polish else self.translate_fn
            result = {k: fn(v) for k, v in data.items()}
            body = json.dumps(result, ensure_ascii=False)
            if self.batch_mode == "garbage":
                return "Sorry, I cannot produce JSON today."
            if self.batch_mode == "fenced":
                return f"```json\n{body}\n```"
            return body

        for marker, fn in (("Fix this translation:\n", self.translate_fn),
                           ("Polish this:\n", self.polish_fn),
                           ("Translate this:\n", self.translate_fn)):
            idx = user.rfind(marker)
            if idx >= 0:
                return fn(user[idx + len(marker):])
        return self.translate_fn(user)

    # -- requests.* replacements --------------------------------------

    def post(self, url, json=None, timeout=None, **kwargs):  # noqa: A002
        payload = json or {}
        self.calls.append(payload)
        if url.endswith("/api/chat"):
            if not payload.get("messages"):
                return FakeResponse({"message": {"content": ""}})
            return FakeResponse({"message": {"role": "assistant",
                                             "content": self.respond(payload)},
                                 "done": True})
        if url.endswith("/api/generate"):
            return FakeResponse({"done": True})
        return FakeResponse({"error": "not found"}, status_code=404)

    def get(self, url, timeout=None, **kwargs):
        if url.endswith("/api/tags"):
            return FakeResponse({"models": [{"name": n}
                                            for n in self.model_names]})
        if url.endswith("/api/ps"):
            return FakeResponse({"models": []})
        return FakeResponse({"error": "not found"}, status_code=404)

    # -- helpers for assertions ----------------------------------------

    def chat_calls(self) -> list:
        return [c for c in self.calls if "messages" in c]

    def user_messages(self) -> list[str]:
        return [self._last_user(c["messages"]) for c in self.chat_calls()]


def install(monkeypatch, **kwargs) -> FakeLLM:
    """Patch translator.ai_client's HTTP layer with a FakeLLM (pytest)."""
    from translator import ai_client
    llm = FakeLLM(**kwargs)
    monkeypatch.setattr(ai_client.requests, "post", llm.post)
    monkeypatch.setattr(ai_client.requests, "get", llm.get)
    return llm


@contextlib.contextmanager
def patched_llm(**kwargs):
    """Context-manager variant of :func:`install` for non-pytest use."""
    from translator import ai_client
    llm = FakeLLM(**kwargs)
    orig_post, orig_get = ai_client.requests.post, ai_client.requests.get
    ai_client.requests.post = llm.post
    ai_client.requests.get = llm.get
    try:
        yield llm
    finally:
        ai_client.requests.post = orig_post
        ai_client.requests.get = orig_get
