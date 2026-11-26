"""Checks and repairs for model replies (seed of a single output checker).

Small pure functions, no client state. Two kinds:

Fixers (safe, deterministic repairs; ``clean_output`` runs them in order):
- normalise_line_breaks: a bare carriage return (CR or CR LF) becomes "\\n".
- strip_prompt_leaks: whole lines copied from our prompt ("Content type:
  dialogue line", "Speaker: X is FEMALE...") are removed.
- replace_jp_ellipsis: "……" (two or more U+2026) in an English reply becomes "...".
- normalise_fullwidth: full-width punctuation/spaces (！？。、「」) in a
  Latin-script reply become ASCII; ～ and ♡ stay; codes are never touched.
- restore_name_case: glossary names (EN starts with a capital, JP is in the
  source, not a built-in default-glossary word) are written with the
  glossary's capitals; all-caps (shouting) is left alone.
- restore_speaker_label_case: an all-lowercase speaker label (first line that
  is only a name, or a speaker_name entry) gets the glossary / actor-name
  spelling, else title case.
- restore_leading_blank_line: a source that starts with blank line(s) keeps
  them, and the first real line starts with a capital.
- restore_speech_quotes: a reply with no quotes to a line that is exactly one
  「...」 gets wrapped in "..." (takes the RAW original, not the cleaned source;
  not part of ``clean_output``).

Checks (return a reason string or None; ``batch_problems`` runs them):
- length_mismatch: a long, varied source (40+ content chars) answered in 1-2
  words, or a 1-2 char source answered with 10+ words.
- duplicate_answers: the same answer (3+ words) for lines whose sources
  differ (a shifted or duplicated batch line).
- name_code_mismatch: a name/variable code (\\N[1], \\n[1], \\V[3], \\P[1],
  \\N<..>) appears a different number of times in the reply than in the
  original (e.g. the model wrote the hero's current name instead).

Input helper:
- ruby_to_base: a ruby code \\r[base,reading] in reference context becomes
  its base word.

Fixers that need the source expect the cleaned source the model saw
(«CODE» markers, converted brackets), and run before codes are restored, so
the inside of a control code is never changed.
"""

import logging
import re
import unicodedata

from . import CONTROL_CODE_RE, JAPANESE_RE

log = logging.getLogger(__name__)

# ── Input ───────────────────────────────────────────────────────────

_RUBY_RE = re.compile(r"\\[rR]\[([^\],]+),[^\]]*\]")


def ruby_to_base(text: str) -> str:
    """\\r[base,reading] (ruby/furigana code) -> base, for context blocks."""
    return _RUBY_RE.sub(r"\1", text) if text else text


# ── Built-in glossary words ─────────────────────────────────────────

_BUILTIN_GLOSSARY_PAIRS: set[tuple[str, str]] | None = None


def is_builtin_glossary_pair(jp: str, en: str) -> bool:
    """True if (jp, en) is an unchanged entry of the built-in default glossary.

    Those are generic words written in title case ("Breasts", "Girl", "No"),
    not names: they are never force-capitalised.
    """
    global _BUILTIN_GLOSSARY_PAIRS
    if _BUILTIN_GLOSSARY_PAIRS is None:
        from .default_glossary import get_all_defaults
        _BUILTIN_GLOSSARY_PAIRS = set(get_all_defaults().items())
    return (jp, en) in _BUILTIN_GLOSSARY_PAIRS


# ── Fixers ──────────────────────────────────────────────────────────

def normalise_line_breaks(output: str) -> str:
    """CR LF and lone CR -> LF (the game only treats LF as a line break)."""
    return output.replace("\r\n", "\n").replace("\r", "\n")


_JP_ELLIPSIS_RE = re.compile(chr(0x2026) + "{2,}")


def replace_jp_ellipsis(output: str, target_language: str = "English") -> str:
    """Japanese double ellipsis -> "..." in English (and Pig Latin) replies."""
    if target_language not in ("English", "Pig Latin"):
        return output
    return _JP_ELLIPSIS_RE.sub("...", output)


# Full-width punctuation left in a Latin-script reply. ～ (wave dash) and ♡
# are kept on purpose: English game text uses them too.
_FULLWIDTH_MAP = {
    0x3000: " ",            # ideographic space
    0x3002: ".",            # 。
    0x3001: ",",            # 、
    0xFF61: ".",            # ｡ (half-width katakana full stop)
    0xFF64: ",",            # ､
    0x300C: '"', 0x300D: '"', 0x300E: '"', 0x300F: '"',   # 「」『』
    0x3010: "[", 0x3011: "]",                             # 【】
}
# Full-width ASCII forms ！..｝ map to ASCII; ～ (U+FF5E) is not included.
for _cp in range(0xFF01, 0xFF5E):
    _FULLWIDTH_MAP[_cp] = chr(_cp - 0xFEE0)
_FULLWIDTH_RE = re.compile("[" + "".join(re.escape(chr(c)) for c in _FULLWIDTH_MAP) + "]")
_CJK_TARGETS = ("Chinese", "Japanese", "Korean")
_NO_BRACKETS = str.maketrans({c: None for c in (0x300C, 0x300D, 0x300E, 0x300F)})


def normalise_fullwidth(output: str, target_language: str = "English") -> str:
    """Full-width punctuation and spaces in a Latin-script reply -> ASCII.

    Game 2 had about 265 English lines with leftover ！？。、 or full-width
    spaces. CJK target languages are left alone.
    """
    if not output or any(t in target_language for t in _CJK_TARGETS):
        return output
    if not _FULLWIDTH_RE.search(output):
        return output
    # Japanese left in the text means it isn't a Latin-script reply: a
    # picture-choice file name (C_はい。|350|200) or an untranslated line.
    # Changing it could break a file reference, so leave it whole.
    # (JAPANESE_RE also counts 「」『』, which are exactly what we convert.)
    if JAPANESE_RE.search(_PROTECTED_RE.sub("", output).translate(_NO_BRACKETS)):
        return output
    out = []
    for piece, is_code in _split_codes(output):
        out.append(piece if is_code else _convert_fullwidth(piece))
    return "".join(out)


def _convert_fullwidth(text: str) -> str:
    """Convert one code-free piece; a space is added only after a converted
    mark glued to a letter ("Yes、that" -> "Yes, that"), never elsewhere."""
    out = []
    n = len(text)
    for i, ch in enumerate(text):
        rep = _FULLWIDTH_MAP.get(ord(ch))
        if rep is None:
            out.append(ch)
            continue
        if rep == " " and out and out[-1] == " ":
            continue          # "Yes!　" next to a real space: one space only
        out.append(rep)
        nxt = text[i + 1] if i + 1 < n else ""
        if rep in ",.!?;:" and nxt.isascii() and nxt.isalpha():
            out.append(" ")
    return "".join(out)


_PROTECTED_RE = re.compile(
    r"«CODE\d+»|(?:" + CONTROL_CODE_RE.pattern + ")")


def _split_codes(text: str) -> list[tuple[str, bool]]:
    """[(piece, is_code)]: «CODEn» placeholders and real control codes are
    kept whole so nothing inside a code is ever changed."""
    parts, pos = [], 0
    for m in _PROTECTED_RE.finditer(text):
        if m.start() > pos:
            parts.append((text[pos:m.start()], False))
        if m.end() > m.start():
            parts.append((m.group(0), True))
        pos = m.end()
    if pos < len(text):
        parts.append((text[pos:], False))
    return parts


# Lines of our own prompt that a model sometimes copies into its reply
# ("Content type: dialogue line" leaked into game 2). Whole lines only.
_PROMPT_LEAK_LINE_RE = re.compile(
    r"^[ \t]*(?:"
    r"Content type:.*"
    r"|Context \(surrounding dialogue.*"
    r"|Speaker: .{1,60} is (?:FEMALE|MALE)\..*"
    r"|Narration: this line is .*"
    r"|Translation History.*"
    r"|Translate this:\s*"
    r"|(?:JP|EN):\s*$"
    r")[ \t]*$",
    re.MULTILINE)


def strip_prompt_leaks(output: str, source: str = "") -> str:
    """Remove whole lines copied from our prompt scaffolding.

    A line is only removed when the source doesn't contain the same text, so
    a game that really says "Content type:" keeps it. Blank lines left behind
    at the start or end are trimmed; inner line structure is kept.
    """
    if not output or not _PROMPT_LEAK_LINE_RE.search(output):
        return output
    kept = []
    for line in output.split("\n"):
        if (_PROMPT_LEAK_LINE_RE.fullmatch(line)
                and line.strip() not in (source or "")):
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    lead = 0
    while lead < len(kept) and not kept[lead].strip():
        lead += 1
    # Keep blank lines the reply started with only if the source did too
    if lead and not (source or "").startswith("\n"):
        kept = kept[lead:]
    return "\n".join(kept)


def restore_name_case(output: str, source: str, glossary: dict | None) -> str:
    """Write glossary names with the glossary's capitals."""
    if not output or not glossary:
        return output
    for jp, en in glossary.items():
        if (not isinstance(en, str) or not isinstance(jp, str) or len(en) < 2
                or not en[0].isupper() or en.isupper() or not jp
                or jp not in source or len(en.split()) > 3
                or is_builtin_glossary_pair(jp, en)):
            continue
        pat = re.compile(r"(?<![\w«])" + re.escape(en) + r"(?![\w»])", re.IGNORECASE)
        output = pat.sub(
            lambda m, en=en: (m.group(0) if m.group(0) == en
                              or m.group(0).isupper() else en),
            output)
    return output


# Characters that never appear in a speaker name (sentence punctuation, codes)
_NOT_A_NAME_RE = re.compile(r"[。、！？!?…,.「」『』\"«»\s]")
# Output speaker-label line: optional [ ], the name, optional ] and colon
_LABEL_LINE_RE = re.compile(r"(\s*\[?)(.*?)(\]?\s*[:：]?\s*)")


def speaker_label_name(source: str, field: str = "", glossary: dict | None = None,
                       actor_names: dict | None = None) -> str | None:
    """The JP name if the source's first line is a speaker label, else None.

    A label is a speaker_name entry, or a short first line (no sentence
    punctuation, with Japanese) followed by more text that has a colon, is
    in [ ], is a known glossary/actor name, or is followed by a quoted line.
    """
    if field == "speaker_name":
        name = source.strip()
        if name and "\n" not in name and len(name) <= 20 and "«" not in name:
            return name
        return None
    lines = source.split("\n")
    if len(lines) < 2:
        return None
    first = lines[0].strip()
    if not first or len(first) > 16:
        return None
    name = first.rstrip(":：")
    colon = name != first
    bracketed = len(name) > 2 and name[0] == "[" and name[-1] == "]"
    if bracketed:
        name = name[1:-1].strip()
    if not name or _NOT_A_NAME_RE.search(name) or not JAPANESE_RE.search(name):
        return None
    known = name in (glossary or {}) or name in set((actor_names or {}).values())
    rest = lines[1].lstrip()
    if colon or bracketed or known or rest[:1] in ('"', "(", "「", "『", "（"):
        return name
    return None


def restore_speaker_label_case(output: str, source: str, field: str = "",
                               glossary: dict | None = None,
                               actor_names: dict | None = None,
                               actor_names_en: dict | None = None) -> str:
    """Capitalise an all-lowercase speaker label on the reply's first line."""
    name = speaker_label_name(source, field, glossary, actor_names)
    if name is None or not output:
        return output
    lines = output.split("\n")
    m = _LABEL_LINE_RE.fullmatch(lines[0])
    if not m:
        return output
    core = m.group(2)
    if (not any(ch.isalpha() for ch in core) or core != core.lower()
            or len(core.split()) > 4):
        return output
    candidates = [(glossary or {}).get(name)]
    for actor_id, jp in (actor_names or {}).items():
        if jp == name:
            candidates.append((actor_names_en or {}).get(actor_id))
    proper = None
    for en in candidates:
        if isinstance(en, str) and en and core.startswith(en.lower()):
            proper = en + core[len(en):]  # "mina-chan" + "Mina" -> "Mina-chan"
            break
    if proper is None:
        if len(core.split()) > 3:
            return output
        proper = " ".join(w[:1].upper() + w[1:] for w in core.split(" "))
    lines[0] = m.group(1) + proper + m.group(3)
    return "\n".join(lines)


_LOWER_START_RE = re.compile(r"""^(\n*["'(\[]?)([a-z])""")


def restore_leading_blank_line(output: str, source: str) -> str:
    """Keep the source's leading blank line(s); capitalise the first word."""
    lead = len(source) - len(source.lstrip("\n"))
    if not lead or not output.strip() or output.startswith("\n"):
        return output
    return "\n" * lead + _LOWER_START_RE.sub(
        lambda m: m.group(1) + m.group(2).upper(), output, count=1)


# VN engines display dialogue in speech boxes: their speech quotes are
# dropped, not converted, and never restored.
VN_ENGINES = frozenset({"tyranoscript", "kirikiri", "crowd", "renpy"})

_JP_SPEECH_OPEN = chr(0x300C)    # 「
_JP_SPEECH_CLOSE = chr(0x300D)   # 」
_LEADING_TAGS_RE = re.compile(r"(?:<[A-Za-z/][^<>\n]*>\s*)+")
# Any of these in the reply means the model kept (some) quoting: leave it.
# Curly double quotes, JP brackets, guillemets (French etc.) and the German
# low quote. Not U+2019: it is also the curly apostrophe in "don't".
_REPLY_QUOTE_CHARS = ('"', chr(0x201C), chr(0x201D), chr(0x201E),
                      chr(0xAB), chr(0xBB), _JP_SPEECH_OPEN,
                      _JP_SPEECH_CLOSE, chr(0x300E), chr(0x300F))


def restore_speech_quotes(original: str, output: str) -> str:
    """Wrap the reply in "..." when the Japanese is one 「...」 around the line.

    Models (Sugoi 14B in particular) often drop the quotes: in one game only
    26 of 4,418 「」 lines kept them. ``original`` is the RAW Japanese (before
    brackets are converted, so 「A」と「B」 and 「A『B』C」 can be told apart).

    Fails closed: the original must start with 「 and end with 」 with no
    other 「 or 」 (an inner 『』 is fine); anything before or after (a control
    code, a name glued on) means no change. A reply that has any quote mark
    of its own is left alone, and so is one wrapped in single quotes.
    """
    if not output or not original:
        return output
    src = original.strip()
    if (len(src) < 2 or not src.startswith(_JP_SPEECH_OPEN)
            or not src.endswith(_JP_SPEECH_CLOSE)
            or src.count(_JP_SPEECH_OPEN) != 1
            or src.count(_JP_SPEECH_CLOSE) != 1):
        return output
    if any(q in output for q in _REPLY_QUOTE_CHARS):
        return output
    stripped = output.strip()
    start = output.index(stripped[0]) if stripped else 0
    # Leading <Tag>s (<WordWrap>, <B>) stay outside the quotes.
    tags = _LEADING_TAGS_RE.match(stripped)
    if tags:
        start += tags.end()
    body = output[start:].rstrip()
    if not body or (len(body) > 1 and body[0] == "'" and body[-1] == "'"):
        return output
    end = start + len(body)
    return output[:start] + '"' + body + '"' + output[end:]


def clean_output(source: str, output: str, *, glossary: dict | None = None,
                 actor_names: dict | None = None,
                 actor_names_en: dict | None = None, field: str = "",
                 target_language: str = "English") -> str:
    """Run every fixer, in order. ``source`` is the cleaned source text."""
    if not output:
        return output
    output = normalise_line_breaks(output)
    output = strip_prompt_leaks(output, source)
    output = replace_jp_ellipsis(output, target_language)
    output = normalise_fullwidth(output, target_language)
    output = restore_name_case(output, source, glossary)
    output = restore_speaker_label_case(output, source, field, glossary,
                                        actor_names, actor_names_en)
    return restore_leading_blank_line(output, source)


# ── Checks ──────────────────────────────────────────────────────────

# Romanised sound effects ("Zuppuh", "DOPPY", "Juph"): katakana SFX the
# model spelled out in romaji instead of translating. A word counts when it
# splits into romaji syllables (with SFX endings like -h, -pp, -ph), starts
# with a hard SFX consonant, and isn't an English word or a common moan.
_ROMAJI_SYLLABLE = (
    r"(?:(?:kk|gg|ss|zz|tt|dd|pp|bb|tch|cch|chch)?"
    r"(?:ky|gy|sh|ch|ts|ny|hy|my|ry|by|py|j|[kgsztdnhbpmyrwf])?[aiueo]+"
    r"|n(?![aiueoy]))")
_ROMAJI_SFX_RE = re.compile(
    r"(?:" + _ROMAJI_SYLLABLE + r")+(?:h+|ppy|py|pp|ph+|p|tt|t|ts|zz|tsu)?",
    re.IGNORECASE)
_SFX_START_RE = re.compile(r"^(?:z|j|g|d|b|p|ts|ch|gy|by|py)", re.IGNORECASE)
_WORD_TOKEN_RE = re.compile(r"[A-Za-z]+")
# Romaji-shaped words that are fine in English game text.
_SFX_OK = frozenset("""
    pan pun pin pen pon bun bin ban ban bon den din dan don gun gin gone
    dope dune june jun jin jane zen zone bone pine dine done gene tune
    chin chop chap chip chun pa pi po pu be bi bo bu de di do du ga ge
    go gu ja ji jo ju za ze zo zu chu chuu chuchu
""".split())


# Sounds that are normal in English game text: laughs (gahaha), grunts
# (guh, puh), kisses (chu, chuu, chup).
_SFX_OK_RE = re.compile(
    r"^(?:g[aeiou](?:h[aeiou])+h*|[gp]u+h+|chu+p?|(?:chu)+)$")


def romanised_sfx(text: str, glossary: dict | None = None) -> list[str]:
    """Words in an English reply that look like romanised katakana SFX.

    Flags, never fixes: the reviewer decides. English words (any word the
    word-splitter knows), glossary English (names like Tsukimoto) and
    common laughs/grunts/kisses are not flagged.
    """
    names = set()
    for en in (glossary or {}).values():
        if isinstance(en, str):
            names.update(w.lower() for w in _WORD_TOKEN_RE.findall(en))
    found = []
    for tok in _WORD_TOKEN_RE.findall(text or ""):
        low = tok.lower()
        if (len(low) < 3 or low in _SFX_OK or low in names
                or _SFX_OK_RE.match(low)
                or not _SFX_START_RE.match(low)
                or not _ROMAJI_SFX_RE.fullmatch(low)):
            continue
        if _is_english_word(low):
            continue
        found.append(tok)
    return found


_ENGLISH_WORDS: frozenset | None = None


def _is_english_word(word: str) -> bool:
    """True if ``word`` is in wordninja's English word list."""
    global _ENGLISH_WORDS
    if _ENGLISH_WORDS is None:
        try:
            import wordninja
            # ~126k words; garbled SFX ("zupp", "juph", "doppy") aren't in it.
            _ENGLISH_WORDS = frozenset(wordninja.DEFAULT_LANGUAGE_MODEL._wordcost)
        except Exception:
            # Without the list "but", "been" and "damage" look like romaji:
            # flagging them would bury real problems, so flag nothing.
            log.warning("English word list unavailable; SFX check disabled",
                        exc_info=True)
            _ENGLISH_WORDS = frozenset()
    return not _ENGLISH_WORDS or word in _ENGLISH_WORDS

_SHIFT_MIN_DUP_WORDS = 3       # identical answers shorter than this are fine ("Yeah.")
_SHIFT_LONG_SRC = 40           # a source this long (content chars)...
_SHIFT_LONG_SRC_DISTINCT = 15  # ...with this many different chars (not a moan)...
_SHIFT_LONG_MAX_WORDS = 2      # ...answered in this many words or fewer is bad
_SHIFT_SHORT_SRC = 2           # a source this short...
_SHIFT_SHORT_MIN_WORDS = 10    # ...answered with this many words or more is bad
_PLACEHOLDER_TOKEN_RE = re.compile(r"«CODE\d+»")


def _src_content(text: str) -> str:
    """Source letters only: no «CODE» markers, spaces, punctuation or symbols."""
    text = _PLACEHOLDER_TOKEN_RE.sub("", text or "")
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in "PSZC")


def _answer_words(text: str) -> list[str]:
    text = _PLACEHOLDER_TOKEN_RE.sub(" ", text or "").lower()
    return re.sub(r"[\W_]+", " ", text).split()


def length_mismatch(source: str, output: str) -> str | None:
    """Reason if the answer's length is absurd for its source, else None."""
    src = _src_content(source)
    words = len(_answer_words(output))
    if (len(src) >= _SHIFT_LONG_SRC and len(set(src)) >= _SHIFT_LONG_SRC_DISTINCT
            and words <= _SHIFT_LONG_MAX_WORDS):
        return f"{len(src)}-char line answered with {words} word(s)"
    if 0 < len(src) <= _SHIFT_SHORT_SRC and words >= _SHIFT_SHORT_MIN_WORDS:
        return f"{len(src)}-char line answered with {words} words"
    return None


# Codes the game replaces with text (player/actor names, variables): a reply
# that writes the current name instead of the code breaks when the player
# renames the hero. Formatting codes (colours, icons, pauses) are not
# checked here: a dropped one is put back by the existing restore step.
_TEXT_CODE_RE = re.compile(r"\\[NnVvPp]\[\d+\]|\\[Nn]<[^>\n]*>")


def name_code_mismatch(original: str, output: str) -> str | None:
    """Reason if a name/variable code (\\N[1], \\n[1], \\V[3], \\P[1], \\N<..>)
    appears a different number of times in the output than in the original.

    Both texts are raw game text (codes restored).
    """
    want: dict[str, int] = {}
    for code in _TEXT_CODE_RE.findall(original or ""):
        want[code] = want.get(code, 0) + 1
    for code, n in want.items():
        got = (output or "").count(code)
        if got != n:
            return f"name/variable code {code} appears {got} time(s), expected {n}"
    return None


def duplicate_answers(sources: dict[str, str], outputs: dict[str, str]) -> dict[str, str]:
    """{key: reason} for keys that share a 3+ word answer with a different source."""
    by_answer: dict[str, list[str]] = {}
    for key, answer in outputs.items():
        words = _answer_words(answer)
        if len(words) >= _SHIFT_MIN_DUP_WORDS:
            by_answer.setdefault(" ".join(words), []).append(key)
    bad: dict[str, str] = {}
    for keys in by_answer.values():
        if len(keys) > 1 and len({_src_content(sources.get(k, "")) for k in keys}) > 1:
            for k in keys:
                bad[k] = "same answer as " + ", ".join(o for o in keys if o != k)
    return bad


def problems(source: str, output: str) -> list[str]:
    """Reasons a single reply looks wrong (empty list = fine)."""
    reason = length_mismatch(source, output)
    return [reason] if reason else []


def batch_problems(sources: dict[str, str], outputs: dict[str, str]) -> dict[str, list[str]]:
    """{key: reasons} for batch answers that look shifted or misplaced."""
    found: dict[str, list[str]] = {}
    for key, output in outputs.items():
        reasons = problems(sources.get(key, ""), output)
        if reasons:
            found[key] = reasons
    for key, reason in duplicate_answers(sources, outputs).items():
        found.setdefault(key, []).append(reason)
    return found
