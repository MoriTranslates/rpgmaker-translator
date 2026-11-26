"""Suspect finder: cheap checks that point a reviewer at likely-wrong lines.

"My Happiness 2" passed every automatic check (no Japanese left, codes
intact, names consistent) while about 1 line in 5 was wrong. These checks
look for the shapes those errors had. They only flag; nothing is changed.

Each check returns a reason string or "". find_suspects() runs them all over
translated (and reviewed, if asked) entries.

Pure functions, no Qt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import JAPANESE_RE
from .narration import FIRST_PERSON, count_word, is_narration
from .output_checks import normalise_fullwidth, romanised_sfx
from .speakers import lookup_speaker_gender, speaker_of

# ── Kinds (key, label shown to the user) ───────────────────────────
KINDS: dict[str, str] = {
    "pov": "Narration lost its \"I\" (third person instead of first)",
    "negation": "Japanese is negative, English isn't",
    "he_no_male": "\"he/him\" but the Japanese names no man",
    "body": "Speaker talks about the wrong body (\"my pussy\" from a man)",
    "name_missing": "A glossary name in the Japanese is missing in English",
    "inconsistent": "Same Japanese, different English elsewhere",
    "romaji_sfx": "Romanised sound effects (\"Zuppuh\")",
    "fullwidth": "Full-width punctuation or Japanese left in English",
    "too_long": "More than 4 lines (won't fit the message box)",
}

_EN_I_RE = re.compile(r"\b(?:I|me|my|mine|myself|we|us|our|ours|I'm|I've|I'll|I'd)\b")
_EN_HE_RE = re.compile(r"\b(?:he|him|his|himself|he's|he'd|he'll)\b", re.IGNORECASE)
_EN_NEG_RE = re.compile(
    r"\b(?:not|no|never|nothing|nobody|none|nowhere|neither|nor|without|"
    r"cannot|can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't|weren't|"
    r"haven't|hasn't|hadn't|shouldn't|wouldn't|couldn't|mustn't|ain't|"
    r"hardly|barely|instead|rather|fail|failed|unable|refuse|refused|"
    r"stop|stopped)\b|n't\b", re.IGNORECASE)

# Japanese words that name or point at a man.
_JP_MALE = ("彼", "男", "父", "兄", "弟", "息子", "夫", "旦那", "主人", "店長",
            "おじさん", "おっさん", "親父", "少年", "爺", "彼氏", "坊", "君",
            "くん", "オヤジ", "王子", "神父", "パパ")
# "彼" alone is male, but 彼女 is female: count 彼 only when not 彼女.
_KARE_RE = re.compile("彼(?!女)")

# Sentence-final negatives (before 。！？…」 or the end), minus forms that
# aren't negations in English ("have to", adjectives ending in ない).
_JP_NEG_RE = re.compile(
    "(?:ない|なかった|ません|ませんでした|じゃない|ではない|わけじゃない|"
    "ないで|なくて|ないの|なかったの)"
    "(?=[。！？!?…♡♪～〜」』、\\s]|$)")
_JP_NEG_NOT_NEGATIVE = (
    "少ない", "危ない", "情けない", "だらしない", "もったいない", "勿体ない",
    "つまらない", "仕方ない", "仕方がない", "たまらない", "堪らない",
    "間違いない", "違いない", "しょうがない", "申し訳ない", "幼い",
    "汚い", "切ない", "はしたない", "くだらない", "とんでもない", "さりげない",
    "ないと", "なきゃ", "なくちゃ", "なければ", "ないか", "ないかな",
    "ないかしら", "じゃないか", "ではないか", "じゃない？", "じゃないの？",
    "かもしれない", "かもしれません", "知れない", "変わりない", "変わらない",
    "すいません", "すみません", "すんません", "ないの？", "ないの?",
    "かまわない", "構わない",
)
# しか ... ない is "only", not a negation in English ("all I could do")
_SHIKA_RE = re.compile("しか[^。！？]{0,12}$")
_FEMALE_BODY_SELF_RE = re.compile(
    r"\bmy (?:pussy|womb|vagina|clit|cunt|breasts|tits|boobs|nipples|"
    r"pussy's|walls|uterus)\b|\binside of me\b", re.IGNORECASE)
_MALE_BODY_SELF_RE = re.compile(
    r"\bmy (?:cock|dick|penis|balls|shaft|member|cock's)\b", re.IGNORECASE)

MAX_BOX_LINES = 4


@dataclass
class Suspect:
    id: str
    kind: str
    reason: str


@dataclass
class SuspectReport:
    by_kind: dict[str, list[Suspect]] = field(default_factory=dict)

    def add(self, s: Suspect):
        self.by_kind.setdefault(s.kind, []).append(s)

    def ids(self, kinds=None) -> set[str]:
        out = set()
        for k, items in self.by_kind.items():
            if kinds is None or k in kinds:
                out.update(s.id for s in items)
        return out

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.by_kind.items()}


def _strip_codes(text: str) -> str:
    from . import CONTROL_CODE_RE
    return CONTROL_CODE_RE.sub("", text or "")


# ── Single-line checks ─────────────────────────────────────────────

def check_pov(entry, narrator_words: tuple[str, ...] = FIRST_PERSON) -> str:
    """Narration whose Japanese uses a first-person word, English has no I."""
    if not is_narration(entry):
        return ""
    jp = entry.original or ""
    words = [w for w in narrator_words if count_word(jp, w)]
    if not words:
        return ""
    if _EN_I_RE.search(_strip_codes(entry.translation)):
        return ""
    return f"Japanese says {words[0]}, English has no I/me/my"


def check_negation(entry) -> str:
    jp = _strip_codes(entry.original)
    for m in _JP_NEG_RE.finditer(jp):
        window = jp[max(0, m.start() - 6):m.end() + 3]
        if any(x in window for x in _JP_NEG_NOT_NEGATIVE):
            continue
        if _SHIKA_RE.search(jp[max(0, m.start() - 14):m.start()]):
            continue
        if _EN_NEG_RE.search(_strip_codes(entry.translation)):
            return ""
        return f"Japanese ends in a negative ({m.group(0)}), English has no not/no/never"
    return ""


def _mentions_a_man(jp: str, names) -> bool:
    if _KARE_RE.search(jp):
        return True
    if any(w in jp for w in _JP_MALE if w != "彼"):
        return True
    return any(n and n in jp for n in names)


def check_pronoun(entry, male_names=()) -> tuple[str, str]:
    """("he_no_male", reason) for he/him with no man in the Japanese.

    "he" about someone named in the previous line can be right, so this is
    a pointer for review, not a verdict.
    """
    en = _strip_codes(entry.translation)
    jp = _strip_codes(entry.original)
    if not JAPANESE_RE.search(jp):
        return "", ""
    if _EN_HE_RE.search(en) and not _mentions_a_man(jp, male_names):
        return "he_no_male", "English says he/him; no man is named in the Japanese"
    # (The mirror check for she/her was tried on game 2 and flagged no more
    # real errors than random lines, so it isn't run.)
    return "", ""


def check_body(entry, gender: str) -> str:
    en = _strip_codes(entry.translation)
    if gender == "male" and _FEMALE_BODY_SELF_RE.search(en):
        return "A male speaker says \"" + _FEMALE_BODY_SELF_RE.search(en).group(0) + "\""
    if gender == "female" and _MALE_BODY_SELF_RE.search(en):
        return "A female speaker says \"" + _MALE_BODY_SELF_RE.search(en).group(0) + "\""
    return ""


def check_name_missing(entry, names: dict[str, str]) -> str:
    """A glossary name in the Japanese whose English (first word) is absent."""
    jp = _strip_codes(entry.original)
    en = _strip_codes(entry.translation).lower()
    for jp_name, en_name in names.items():
        if jp_name not in jp:
            continue
        first = en_name.split()[0].split("-")[0].lower()
        if first and first not in en:
            # The listener's name often becomes "you", and a name repeated
            # from the line before becomes he/she: not errors
            if _NAME_REPLACED_RE.search(en):
                continue
            return f"{jp_name} ({en_name}) is not in the English"
    return ""


def check_fullwidth(entry, target_language: str = "English") -> str:
    tr = entry.translation or ""
    if JAPANESE_RE.search(_strip_codes(tr).translate(
            {0x300C: None, 0x300D: None, 0x300E: None, 0x300F: None})):
        return "Japanese characters left in the English"
    if normalise_fullwidth(tr, target_language) != tr:
        return "Full-width punctuation left in the English"
    return ""


def check_too_long(entry) -> str:
    if getattr(entry, "field", "") not in ("dialog", "dialogue"):
        return ""
    tr = (entry.translation or "").rstrip("\n")
    if "<WordWrap>" in tr or "<wordwrap>" in tr.lower():
        return ""       # wrapped at run time; explicit lines don't count
    n = tr.count("\n") + 1
    if n > MAX_BOX_LINES:
        return f"{n} lines; the message box shows {MAX_BOX_LINES}"
    return ""


# ── Whole-project run ──────────────────────────────────────────────

_NAME_REPLACED_RE = re.compile(
    r"\b(?:you|your|yours|he|him|his|she|her|hers|they|them|their)\b")
_NAME_LIKE_RE = re.compile(r"^[A-Z][A-Za-z'\-]+(?: [A-Z][A-Za-z'\-]+)?$")


def _glossary_names(glossary: dict) -> dict[str, str]:
    """Glossary entries that look like character names (capitalised, short)."""
    out = {}
    for jp, en in (glossary or {}).items():
        if (isinstance(en, str) and _NAME_LIKE_RE.match(en.strip())
                and len(jp) >= 2 and JAPANESE_RE.search(jp)):
            out[jp] = en.strip()
    return out


def find_suspects(entries, *, glossary: dict | None = None,
                  speaker_genders: dict | None = None,
                  include_reviewed: bool = False,
                  target_language: str = "English",
                  kinds=None) -> SuspectReport:
    """Run every check. ``kinds`` limits which checks run (None = all).

    Only translated entries are checked (and reviewed ones when asked).
    """
    glossary = glossary or {}
    speaker_genders = speaker_genders or {}
    want = set(kinds) if kinds else set(KINDS)
    statuses = ("translated", "reviewed") if include_reviewed else ("translated",)
    names = _glossary_names(glossary)
    # Male characters' names (JP and EN) count as "a man is named"
    male_names = []
    for name, g in speaker_genders.items():
        if g == "male":
            male_names.append(name)
            if glossary.get(name):
                male_names.append(glossary[name])
            male_names.extend(jp for jp, en in glossary.items() if en == name)
    english = "Chinese" not in target_language and "Japanese" not in target_language \
        and "Korean" not in target_language
    report = SuspectReport()
    by_original: dict[str, dict[str, list[str]]] = {}
    for e in entries:
        if e.status not in statuses or not e.translation:
            continue
        if e.field in ("dialog", "dialogue", "scroll_text", "choice"):
            by_original.setdefault(e.original, {}).setdefault(
                e.translation.strip(), []).append(e.id)
        speech_or_narr = e.field in ("dialog", "dialogue", "scroll_text")
        checks = []
        if speech_or_narr and target_language == "English":
            if "pov" in want:
                checks.append(("pov", check_pov(e)))
            if "negation" in want:
                checks.append(("negation", check_negation(e)))
            if "he_no_male" in want:
                kind, why = check_pronoun(e, male_names)
                if kind in want:
                    checks.append((kind, why))
            if "body" in want:
                sp = speaker_of(e.context or "")
                g = lookup_speaker_gender(sp, speaker_genders, glossary) if sp else ""
                checks.append(("body", check_body(e, g)))
            if "name_missing" in want and names:
                checks.append(("name_missing", check_name_missing(e, names)))
            if "romaji_sfx" in want:
                found = romanised_sfx(e.translation, glossary)
                checks.append(("romaji_sfx", ("Looks romanised: " + ", ".join(found))
                               if found else ""))
            if "too_long" in want:
                checks.append(("too_long", check_too_long(e)))
        if "fullwidth" in want and english:
            checks.append(("fullwidth", check_fullwidth(e, target_language)))
        for kind, why in checks:
            if why:
                report.add(Suspect(e.id, kind, why))
    if "inconsistent" in want:
        for original, variants in by_original.items():
            if len(variants) < 2 or len(original.strip()) < 4:
                continue
            for tr, ids in variants.items():
                for i in ids:
                    report.add(Suspect(
                        i, "inconsistent",
                        f"{len(variants)} different translations of this line"))
    return report
