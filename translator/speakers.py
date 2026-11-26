"""Speaker genders that don't depend on Actors.json.

Many games (most VN-style RPG Maker games) name speakers only in the message
header, so ``[Speaker: X]`` in an entry's context never matches an actor and
the model gets no gender hint. In "My Happiness 2" none of the four main
characters were actors; the only actor had costume names (``守人 私服``).

Pure functions, no Qt:
- collect_speakers: every distinct ``[Speaker: X]`` with its line count.
- base_name: ``守人 私服`` -> ``守人`` (a name followed by a costume/mood tag).
- prefill_speaker_genders: suggested genders from actors (exact or base name).
- lookup_speaker_gender: gender for a speaker from the project map, matching
  the name as written, its glossary English/Japanese, or its base name.
- clean_speaker_genders: a loaded map with bad keys/values dropped.
"""

from __future__ import annotations

import re

GENDERS = ("female", "male", "unknown")

SPEAKER_RE = re.compile(r"\[Speaker:\s*(.+?)\]")
# A costume/mood suffix after the name: ASCII or full-width space.
_SUFFIX_SPLIT_RE = re.compile("[ " + chr(0x3000) + "]+")


def speaker_of(context: str) -> str:
    """The ``[Speaker: X]`` name in a context string, or ""."""
    if not context:
        return ""
    m = SPEAKER_RE.search(context)
    return m.group(1).strip() if m else ""


def collect_speakers(entries) -> dict[str, int]:
    """{speaker: number of entries} for every ``[Speaker: X]`` in contexts."""
    counts: dict[str, int] = {}
    for e in entries:
        name = speaker_of(getattr(e, "context", "") or "")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def base_name(speaker: str) -> str:
    """The name part of a speaker tag with a costume/mood suffix, else ""."""
    parts = _SUFFIX_SPLIT_RE.split(speaker.strip(), maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0]
    return ""


def clean_speaker_genders(raw) -> dict[str, str]:
    """Keep only string keys with a known gender (a hand-edited file may differ)."""
    if not isinstance(raw, dict):
        return {}
    return {k.strip(): v for k, v in raw.items()
            if isinstance(k, str) and k.strip() and v in GENDERS}


def _aliases(name: str, glossary: dict | None) -> list[str]:
    """``name``, its glossary English (if JP) and Japanese (if EN), lowercased."""
    out = [name.lower()]
    if glossary:
        en = glossary.get(name)
        if isinstance(en, str) and en.strip():
            out.append(en.strip().lower())
        low = name.lower()
        for jp, en in glossary.items():
            if isinstance(en, str) and en.strip().lower() == low:
                out.append(jp.lower())
    return out


def lookup_speaker_gender(speaker: str, speaker_genders: dict | None,
                          glossary: dict | None = None) -> str:
    """"female"/"male" for ``speaker`` from the project map, else "".

    Tries the speaker as written, then its base name (``守人 私服`` -> ``守人``);
    each through the glossary in both directions. "unknown" counts as no
    answer, so a later, more general match can still apply.
    """
    if not speaker or not speaker_genders:
        return ""
    by_lower: dict[str, str] = {}
    for k, v in speaker_genders.items():
        if isinstance(k, str) and v in ("female", "male"):
            for alias in _aliases(k, glossary):
                by_lower.setdefault(alias, v)
    for name in (speaker, base_name(speaker)):
        if not name:
            continue
        for alias in _aliases(name, glossary):
            if alias in by_lower:
                return by_lower[alias]
    return ""


# ── Suggesting a gender (shown to the user, never applied silently) ──
#
# Japanese marks gender mostly through first-person pronouns and sentence
# endings. 私 is used by everyone and counts for nothing.
_MALE_PRONOUNS = ("僕", "ぼく", "俺", "おれ", "オレ", "わし", "儂", "拙者")
_FEMALE_PRONOUNS = ("あたし", "アタシ", "あたい", "わたくし")
# Sentence endings, matched before closing punctuation or the end of a line.
_END = "(?=[。！？!?…♡♪～〜」』\\s]|$)"
_FEMALE_ENDINGS_RE = re.compile("(?:わよ|わね|かしら|のよ|だわ|ですわ)" + _END)
_MALE_ENDINGS_RE = re.compile("(?:だぜ|んだぜ|ぜ|だぞ|ぞ|じゃねえ|じゃねぇ)" + _END)
# Name endings (kanji given names; surnames and hiragana names say nothing).
_FEMALE_NAME_ENDS = ("子", "美", "奈", "菜", "香", "花", "恵", "江", "代", "乃",
                     "華", "絵", "里", "璃", "愛", "姫")
_MALE_NAME_ENDS = ("郎", "太", "介", "助", "男", "夫", "雄", "彦", "輔", "也",
                   "吉", "蔵", "治", "司", "朗", "兵衛", "衛門")
# Role words anywhere in the speaker tag.
_FEMALE_ROLES = ("母", "ママ", "姉", "妹", "娘", "少女", "女", "婆", "嫁", "妻",
                 "奥さん", "おばさん", "お嬢", "姫", "メイド", "シスター")
_MALE_ROLES = ("父", "パパ", "兄", "弟", "息子", "少年", "男", "爺", "じじい",
               "おじさん", "おっさん", "親父", "旦那", "夫", "王子", "神父")

# The same in English speaker tags (contexts are renamed to English once
# names are translated). Whole words only: "Female" must not hit "male".
_FEMALE_ROLES_EN_RE = re.compile(
    r"\b(?:female|woman|women|girl|lady|mother|mom|mum|sister|daughter|wife|"
    r"aunt|grandma|grandmother|maid|nun|princess|queen|madam|miss|bride|"
    r"housewife|midwife|schoolgirl|mrs)\b", re.IGNORECASE)
_MALE_ROLES_EN_RE = re.compile(
    r"\b(?:male|man|men|boy|guy|gentleman|father|dad|brother|son|husband|"
    r"uncle|grandpa|grandfather|old man|monk|prince|king|lord|sir|mr|"
    r"schoolboy|butler|priest)\b", re.IGNORECASE)

# How many more cue hits one side needs before anything is suggested.
_MIN_LEAD = 2


def _count(texts, needles) -> int:
    return sum(t.count(n) for t in texts for n in needles)


def suggest_gender(speaker: str, lines) -> tuple[str, list[str]]:
    """("female"/"male"/"", reasons) for a speaker, from their Japanese lines.

    ``lines`` are the speaker's original texts. The result is a suggestion
    for the user to confirm: an empty gender means the cues are too weak or
    point both ways. Reasons are short, human-readable ("uses 僕 ×12").
    """
    texts = [t for t in lines if isinstance(t, str)]
    score = {"female": 0, "male": 0}
    reasons = {"female": [], "male": []}

    def add(g, n, why, weight=1):
        if n:
            score[g] += n * weight
            reasons[g].append(why)

    # Role words in the tag are strong (母, 少年...). 男/女 inside 少女/男性 are
    # covered because the longer word is listed too; both sides can't both hit
    # unless the tag is odd, and then the lead check says nothing.
    name = base_name(speaker) or speaker
    for role in _FEMALE_ROLES:
        if role in name:
            add("female", 1, f"name contains {role}", weight=3)
            break
    for role in _MALE_ROLES:
        if role in name:
            add("male", 1, f"name contains {role}", weight=3)
            break
    m_en = _FEMALE_ROLES_EN_RE.search(speaker)
    if m_en:
        add("female", 1, f'name contains "{m_en.group(0)}"', weight=3)
    m_en = _MALE_ROLES_EN_RE.search(speaker)
    if m_en:
        add("male", 1, f'name contains "{m_en.group(0)}"', weight=3)
    if not score["female"] and not score["male"]:
        for end in _FEMALE_NAME_ENDS:
            if name.endswith(end) and len(name) >= 2:
                add("female", 1, f"name ends in {end} (often female)", weight=2)
                break
        for end in _MALE_NAME_ENDS:
            if name.endswith(end) and len(name) >= 2:
                add("male", 1, f"name ends in {end} (often male)", weight=2)
                break

    for p in _MALE_PRONOUNS:
        n = _count(texts, (p,))
        add("male", n, f"says {p} ×{n}")
    for p in _FEMALE_PRONOUNS:
        n = _count(texts, (p,))
        add("female", n, f"says {p} ×{n}")
    n = sum(len(_FEMALE_ENDINGS_RE.findall(t)) for t in texts)
    add("female", n, f"feminine endings (わよ/かしら/のよ) ×{n}")
    n = sum(len(_MALE_ENDINGS_RE.findall(t)) for t in texts)
    add("male", n, f"masculine endings (ぜ/ぞ) ×{n}")

    f, m = score["female"], score["male"]
    if f - m >= _MIN_LEAD and f >= 2 * m:
        return "female", reasons["female"]
    if m - f >= _MIN_LEAD and m >= 2 * f:
        return "male", reasons["male"]
    both = reasons["female"] + reasons["male"]
    return "", (["mixed cues: " + "; ".join(both)] if both else [])


def lines_by_speaker(entries) -> dict[str, list]:
    """{speaker: [entries]} in game order, for entries with a [Speaker: X]."""
    out: dict[str, list] = {}
    for e in entries:
        sp = speaker_of(getattr(e, "context", "") or "")
        if sp:
            out.setdefault(sp, []).append(e)
    return out


def sample_lines(entries, limit: int = 8) -> list:
    """Up to ``limit`` of a speaker's entries worth reading: not tiny moans,
    no duplicates, spread across the game rather than the first scene only."""
    seen = set()
    good = []
    for e in entries:
        text = (e.original or "").strip()
        if len(text) < 6 or text in seen:
            continue
        seen.add(text)
        good.append(e)
    if len(good) <= limit:
        return good
    step = len(good) / limit
    return [good[int(i * step)] for i in range(limit)]


def prefill_speaker_genders(speakers, actor_names: dict, actor_genders: dict,
                            existing: dict | None = None) -> dict[str, str]:
    """Suggested {speaker: gender}: existing choices first, then actors.

    A speaker gets an actor's gender when its name, or its base name, is the
    actor's name (``守人 私服`` -> actor ``守人``). Everything else is "unknown".
    """
    existing = existing or {}
    by_name: dict[str, str] = {}
    for aid, name in (actor_names or {}).items():
        g = (actor_genders or {}).get(aid, "")
        if isinstance(name, str) and name.strip() and g in ("female", "male"):
            by_name.setdefault(name.strip().lower(), g)
    out: dict[str, str] = {}
    for sp in speakers:
        if existing.get(sp) in GENDERS:
            out[sp] = existing[sp]
            continue
        g = by_name.get(sp.lower()) or by_name.get(base_name(sp).lower(), "")
        out[sp] = g or "unknown"
    return out
