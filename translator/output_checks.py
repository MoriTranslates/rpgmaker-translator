"""Checks and repairs for model replies (seed of a single output checker).

Small pure functions, no client state. Two kinds:

Fixers (safe, deterministic repairs; ``clean_output`` runs them in order):
- normalise_line_breaks: a bare carriage return (CR or CR LF) becomes "\\n".
- replace_jp_ellipsis: "……" (two or more U+2026) in an English reply becomes "...".
- restore_name_case: glossary names (EN starts with a capital, JP is in the
  source, not a built-in default-glossary word) are written with the
  glossary's capitals; all-caps (shouting) is left alone.
- restore_speaker_label_case: an all-lowercase speaker label (first line that
  is only a name, or a speaker_name entry) gets the glossary / actor-name
  spelling, else title case.
- restore_leading_blank_line: a source that starts with blank line(s) keeps
  them, and the first real line starts with a capital.

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

import re
import unicodedata

from . import JAPANESE_RE

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


def clean_output(source: str, output: str, *, glossary: dict | None = None,
                 actor_names: dict | None = None,
                 actor_names_en: dict | None = None, field: str = "",
                 target_language: str = "English") -> str:
    """Run every fixer, in order. ``source`` is the cleaned source text."""
    if not output:
        return output
    output = normalise_line_breaks(output)
    output = replace_jp_ellipsis(output, target_language)
    output = restore_name_case(output, source, glossary)
    output = restore_speaker_label_case(output, source, field, glossary,
                                        actor_names, actor_names_en)
    return restore_leading_blank_line(output, source)


# ── Checks ──────────────────────────────────────────────────────────

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
