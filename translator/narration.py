"""Whose first-person point of view a narration line is in.

Japanese narration often leaves out the subject. With no idea who the
narrator is, the model drifts into third person ("He felt..." for the
protagonist's own thoughts). In "My Happiness 2" most scenes are told by
Mamoru (僕) and some by Yukiko (私), so the narrator changes per scene.

The user picks up to two narrators (main first). Each narrator's own
first-person word is learned from how they talk in dialogue (Mamoru says
僕, Yukiko says 私). A narration line containing exactly one narrator's word
is told by that narrator; other lines follow the last such line before them
in the same scene (file + event + page), or the first one after it. Scenes
with no clue use the main narrator.

Pure functions, no Qt.
"""

from __future__ import annotations

import re

from .speakers import speaker_of

# First-person words, longest first so あたし isn't also counted as たし.
FIRST_PERSON = ("わたくし", "あたし", "アタシ", "わたし", "ぼく", "ボク", "おれ",
                "オレ", "わし", "僕", "俺", "私", "儂")
# Narration lines start with none of these (speech / thought brackets).
_SPEECH_OPENERS = (chr(0x300C), chr(0x300E), chr(0xFF08), "(", '"',
                   chr(0x201C))
_KANA_RE = re.compile("[" + chr(0x3041) + "-" + chr(0x3096) + chr(0x30A1) + "-"
                      + chr(0x30FA) + "]")
# 私 in compounds that aren't "I": 私服 (casual clothes), 私立, 私物 ...
_WATASHI_COMPOUND_NEXT = "服物立用的有事語生財邸設鉄"
MIN_WORD_HITS = 3   # a narrator's word needs this many uses in their dialogue


def count_word(text: str, word: str) -> int:
    """Uses of a first-person ``word`` in ``text``, skipping obvious
    non-pronoun hits: kana words glued to more kana before them (こわし,
    あわし) and 私 in compounds (私服)."""
    if not text or not word:
        return 0
    n = 0
    start = 0
    is_kana_word = bool(_KANA_RE.fullmatch(word[0]))
    while True:
        i = text.find(word, start)
        if i < 0:
            return n
        start = i + len(word)
        before = text[i - 1] if i else ""
        after = text[start] if start < len(text) else ""
        if is_kana_word and before and _KANA_RE.fullmatch(before):
            continue
        if word == "私" and after and after in _WATASHI_COMPOUND_NEXT:
            continue
        n += 1


def first_person_word(lines) -> str:
    """The first-person word someone uses most in their lines, or ""."""
    best, best_n = "", 0
    texts = [t for t in lines if isinstance(t, str)]
    for w in FIRST_PERSON:
        n = sum(count_word(t, w) for t in texts)
        # Longer words win ties (あたし over 私 is impossible anyway).
        if n > best_n:
            best, best_n = w, n
    return best if best_n >= MIN_WORD_HITS else ""


def is_narration(entry) -> bool:
    """A message line with no speaker and no speech brackets."""
    if getattr(entry, "field", "") not in ("dialog", "dialogue", "scroll_text"):
        return False
    if getattr(entry, "namebox", "") or getattr(entry, "namebox_end", ""):
        return False
    if speaker_of(getattr(entry, "context", "") or ""):
        return False
    text = (getattr(entry, "original", "") or "").strip()
    return bool(text) and not text.startswith(_SPEECH_OPENERS)


def scene_key(entry_id: str) -> str:
    """Scene of an entry: its id without the last part (file/event/page)."""
    return entry_id.rsplit("/", 1)[0] if "/" in entry_id else ""


def narrator_words(entries, narrators: list[str]) -> dict[str, str]:
    """{narrator: first-person word} learned from each narrator's dialogue.

    A narrator whose word another (earlier) narrator already uses is left
    out, since their scenes can't be told apart.
    """
    lines: dict[str, list[str]] = {n: [] for n in narrators}
    for e in entries:
        sp = speaker_of(getattr(e, "context", "") or "")
        if sp in lines:
            lines[sp].append(e.original or "")
    out: dict[str, str] = {}
    for n in narrators:
        w = first_person_word(lines[n])
        if w and w not in out.values():
            out[n] = w
    return out


def _hits(text: str, words: dict[str, str]) -> dict[str, int]:
    return {n: count_word(text, w) for n, w in words.items()}


def assign_narrators(entries, narrators: list[str],
                     words: dict[str, str] | None = None) -> dict[str, str]:
    """{entry id: narrator} for every narration entry.

    ``narrators[0]`` is the main narrator (scenes with no clue). ``words``
    defaults to narrator_words(entries, narrators).
    """
    narrators = [n for n in narrators if n]
    if not narrators:
        return {}
    main = narrators[0]
    if words is None:
        words = narrator_words(entries, narrators)
    # Narration lines per scene, in game order, with their own clue (if any).
    scenes: dict[str, list[tuple[str, str]]] = {}
    for e in entries:
        if not is_narration(e):
            continue
        own = [n for n, c in _hits(e.original, words).items() if c]
        scenes.setdefault(scene_key(e.id), []).append(
            (e.id, own[0] if len(own) == 1 else ""))
    out: dict[str, str] = {}
    for lines in scenes.values():
        # A line without a clue follows the last clue before it (a scene can
        # switch narrator part-way); lines before the first clue take the
        # first clue; a scene with no clue at all is the main narrator's.
        first = next((n for _, n in lines if n), main)
        current = first
        for eid, own in lines:
            if own:
                current = own
            out[eid] = current
    return out


def suggest_narrators(entries, speaker_names) -> tuple[list[str], str]:
    """(suggested narrators, explanation) from narration pronoun counts.

    Each first-person word used in narration is matched to the speaker who
    uses it most in dialogue. The main narrator is the most frequent; a
    second one is suggested when its word is at least 10% of the hits.
    """
    totals: dict[str, int] = {}
    for e in entries:
        if is_narration(e):
            for w in FIRST_PERSON:
                c = count_word(e.original, w)
                if c:
                    totals[w] = totals.get(w, 0) + c
    if not totals:
        return [], "Narration has no first-person words (僕/私/俺...)."
    by_speaker: dict[str, list[str]] = {}
    for e in entries:
        sp = speaker_of(getattr(e, "context", "") or "")
        if sp in speaker_names:
            by_speaker.setdefault(sp, []).append(e.original or "")
    owner = {}
    for sp, lines in by_speaker.items():
        w = first_person_word(lines)
        if w:
            n = sum(count_word(t, w) for t in lines)
            if w not in owner or n > owner[w][1]:
                owner[w] = (sp, n)
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    grand = sum(totals.values())
    picks, why = [], []
    for w, c in ranked:
        if w not in owner:
            continue
        sp = owner[w][0]
        if sp in picks:
            continue
        if picks and c < grand * 0.1:
            break
        picks.append(sp)
        why.append(f"{w} ×{c} in narration ({sp} says {w})")
        if len(picks) == 2:
            break
    if not picks:
        return [], ("Narration uses " + ", ".join(f"{w} ×{c}" for w, c in ranked)
                    + ", but no speaker uses those words.")
    return picks, "; ".join(why)


def narration_pov(entries, narrators: list[str]) -> dict[tuple[str, str], str]:
    """{(original, context): narrator} for the AI client's lookup."""
    by_id = assign_narrators(entries, narrators)
    return {(e.original, e.context or ""): by_id[e.id]
            for e in entries if e.id in by_id}


MAX_NARRATORS = 2


def clean_narrators(raw) -> list[str]:
    """A loaded narrator list: up to two distinct non-empty names."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for n in raw:
        if isinstance(n, str) and n.strip() and n.strip() not in out:
            out.append(n.strip())
    return out[:MAX_NARRATORS]
