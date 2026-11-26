"""AI review pass: a strong model reads each scene JP/EN side by side.

What fixed ~1,100 lines of "My Happiness 2" after every automatic check had
passed: send each scene with the Japanese, the current English, a character
sheet and an error checklist; get back only the lines that need a fix, each
with a reason. Every proposal is validated before the user sees it, and
nothing is applied without the user ticking it.

Pure functions (no Qt). The request goes through the AI client's normal
chat transport, so any provider the user configured works; a strong model
is recommended (a small one misses subtle meaning flips).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

from . import CONTROL_CODE_RE, JAPANESE_RE
from .narration import scene_key
from .speakers import speaker_of

REVIEW_FIELDS = ("dialog", "dialogue", "scroll_text", "choice")
MAX_CHUNK_LINES = 60
MAX_BOX_LINES = 4

REVIEW_SYSTEM = """\
You are reviewing an English translation of a Japanese adult game, scene by scene. Each line shows its key, speaker, the Japanese (JP) and the current English (EN). Find lines whose English is WRONG and give a corrected English line.

Check every line for:
- Meaning: dropped or added negation (ない, じゃない, わけじゃない, ません), reversed or changed meaning, wrong numbers or comparisons.
- Who: wrong person or pronoun (he/she/you/I), the listener not called "you", narration that lost its first person "I" (see the narrator note), subject and object swapped, a body part given to the wrong person.
- Missing or invented text: a clause left out, narration added that the Japanese doesn't have.
- Broken English: ungrammatical, garbled or unnatural lines; sound effects left in romaji ("Zuppuh", "Pan!") instead of English sound words.
- Names: use the character sheet spelling.

Rules for a fix:
- Only report lines that are wrong. Natural, faithful lines stay as they are even if you would phrase them differently.
- Keep every control code exactly as written (\\C[2], \\N[1], \\n<name>, \\., \\|, <WordWrap> ...), the same number of line breaks, and the same quote marks at the start and end.
- Write the corrected English only: no notes, no Japanese.

Reply with ONLY a JSON object: {"L3": {"fix": "corrected English", "why": "short reason"}, ...}. Reply {} if every line is fine."""


@dataclass
class Chunk:
    """One review request: consecutive lines, keyed L1..Ln."""
    lines: list = field(default_factory=list)   # [(key, entry)]

    def keys(self) -> dict:
        return {k: e for k, e in self.lines}


@dataclass
class Proposal:
    key: str
    entry_id: str
    original: str        # JP when the request was built
    old: str             # EN when the request was built
    new: str
    why: str
    problem: str = ""    # validation failure ("" = valid)


# ── Building requests ──────────────────────────────────────────────

def reviewable(entry) -> bool:
    return (entry.field in REVIEW_FIELDS and entry.status == "translated"
            and bool((entry.translation or "").strip())
            and bool((entry.original or "").strip()))


def build_chunks(entries, only_ids: set | None = None,
                 max_lines: int = MAX_CHUNK_LINES) -> list[Chunk]:
    """Pack reviewable lines into chunks of whole scenes, in game order.

    Identical Japanese lines are reviewed once (the first copy); a fix is
    later applied to the other copies that still have the same English.
    ``only_ids`` limits the review to those lines (plus nothing else).
    Scenes longer than ``max_lines`` are split.
    """
    seen_jp = set()
    scenes: list[list] = []
    current_scene = None
    for e in entries:
        if not reviewable(e) or (only_ids is not None and e.id not in only_ids):
            continue
        if e.original in seen_jp:
            continue
        seen_jp.add(e.original)
        sc = scene_key(e.id)
        if sc != current_scene:
            scenes.append([])
            current_scene = sc
        scenes[-1].append(e)
    chunks: list[Chunk] = []
    buf: list = []
    for scene in scenes:
        if buf and len(buf) + len(scene) > max_lines:
            chunks.append(_chunk(buf))
            buf = []
        while len(scene) > max_lines:
            chunks.append(_chunk(scene[:max_lines]))
            scene = scene[max_lines:]
        buf.extend(scene)
    if buf:
        chunks.append(_chunk(buf))
    return chunks


def _chunk(entries) -> Chunk:
    return Chunk([(f"L{i + 1}", e) for i, e in enumerate(entries)])


def character_sheet(speaker_genders: dict | None, glossary: dict | None,
                    narrators: list | None) -> str:
    """Names (JP = EN), genders and narrators for the reviewer."""
    glossary = glossary or {}
    lines = []
    for name, g in sorted((speaker_genders or {}).items()):
        en = glossary.get(name, "")
        shown = f"{name} = {en}" if en and en != name else name
        lines.append(f"- {shown}: {g}")
    names_done = set(speaker_genders or {})
    for jp, en in glossary.items():
        if jp in names_done or not isinstance(en, str):
            continue
        if re.fullmatch(r"[A-Z][A-Za-z'\-]+(?: [A-Z][A-Za-z'\-]+)?", en.strip()) \
                and JAPANESE_RE.search(jp):
            lines.append(f"- {jp} = {en}")
    out = "Character sheet:\n" + ("\n".join(lines) if lines else "- (none given)")
    if narrators:
        out += ("\nNarrator: narration lines (no speaker) are the first-person "
                f"voice of {narrators[0]}"
                + (f", or of {narrators[1]} in scenes told by them"
                   if len(narrators) > 1 else "")
                + ': the narrator is "I", never "he/she".')
    return out


def build_messages(chunk: Chunk, sheet: str) -> list[dict]:
    rows = []
    for key, e in chunk.lines:
        sp = speaker_of(e.context or "")
        who = f"[{sp}]" if sp else ("[choice]" if e.field == "choice" else "[narration]")
        rows.append(f"{key} {who}\nJP: {e.original}\nEN: {e.translation}")
    user = sheet + "\n\nLines:\n\n" + "\n\n".join(rows)
    return [{"role": "system", "content": REVIEW_SYSTEM},
            {"role": "user", "content": user}]


# ── Reading the reply ──────────────────────────────────────────────

def _load_json(raw: str):
    raw = (raw or "").strip()
    for candidate in (raw, re.sub(r"^```(?:json)?\s*|\s*```$", "", raw).strip()):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, TypeError):
            pass
    raise ValueError("Review reply is not JSON")


def parse_review(raw: str, chunk: Chunk) -> list[Proposal]:
    """Proposals from a reply; unknown keys are kept with a problem note."""
    data = _load_json(raw)
    if not isinstance(data, dict):
        raise ValueError("Review reply is not a JSON object")
    by_key = chunk.keys()
    out = []
    for key, val in data.items():
        if isinstance(val, str):
            fix, why = val, ""
        elif isinstance(val, dict):
            fix, why = val.get("fix", ""), val.get("why", "")
        else:
            continue
        if not isinstance(fix, str):
            continue
        e = by_key.get(key)
        if e is None:
            out.append(Proposal(key, "", "", "", fix, str(why or ""),
                                problem=f"unknown line key {key}"))
            continue
        out.append(Proposal(key, e.id, e.original, e.translation, fix,
                            str(why or "")))
    return out


# ── Validation (lesson: never apply a fix to the wrong line) ───────

_INVISIBLE = (set(range(0x00, 0x09)) | {0x0B, 0x0C} | set(range(0x0E, 0x20))
              | {0x2028, 0x2029, 0xFEFF, 0x200B, 0xFFFE, 0xFFFF})
_NO_BRACKETS = {0x300C: None, 0x300D: None, 0x300E: None, 0x300F: None}
_LEAD_TAGS_RE = re.compile(r"^(?:\s|<[A-Za-z/][^<>\n]*>)*")


def _codes(text: str) -> list:
    """Control codes in order: a reordered colour code wraps other text."""
    return CONTROL_CODE_RE.findall(text or "")


def _bad_char(c: str) -> bool:
    """Control (except LF) or format/invisible characters."""
    if c == "\n":
        return False
    if 0xD800 <= ord(c) <= 0xDFFF or ord(c) in _INVISIBLE:
        return True
    return unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Cn")


def _odd_chars(new: str, old: str) -> bool:
    """Invisible or unusual characters a fix brings in that the line lacked.

    Private-use characters, the ideographic space and combining marks not
    sitting on a letter (a variation selector, a stray accent) are refused
    unless the current English already has them: an accent on a letter
    (Café written with a combining mark) is fine.
    """
    for i, c in enumerate(new):
        if c in old:
            continue
        cat = unicodedata.category(c)
        if cat == "Co" or c == chr(0x3000):
            return True
        if cat in ("Mn", "Me") and not (i and new[i - 1].isalpha()):
            return True
    return False


def _quoted(text: str) -> tuple[bool, bool]:
    body = _LEAD_TAGS_RE.sub("", text or "").rstrip()
    return body.startswith(('"', chr(0x201C))), body.endswith(('"', chr(0x201D)))


def validate(p: Proposal, entry) -> str:
    """Why this proposal must not be applied, or "" if it may be."""
    if p.problem:
        return p.problem
    if entry is None:
        return "line no longer exists"
    if entry.original != p.original:
        return "the Japanese of this line changed since the review"
    if entry.translation != p.old:
        return "the English was edited since the review"
    if entry.status == "reviewed":
        return "line is marked Reviewed"
    # apply() only changes translated lines of the review fields: say so
    # here instead of dropping the fix without a word.
    if entry.status != "translated":
        return f"line is {entry.status}, not translated"
    if entry.field not in REVIEW_FIELDS:
        return f"'{entry.field}' lines aren't changed by the review"
    new = p.new
    if not new.strip():
        return "empty fix"
    if new == p.old:
        return "no change"
    if any(_bad_char(c) for c in new) or _odd_chars(new, p.old):
        return "fix contains control or invisible characters"
    if JAPANESE_RE.search(CONTROL_CODE_RE.sub("", new).translate(_NO_BRACKETS)):
        return "fix contains Japanese"
    if _codes(new) != _codes(p.old):
        return "control codes differ from the current English"
    if _quoted(new) != _quoted(p.old):
        return "quote marks at the start/end differ"
    old_lead = len(p.old) - len(p.old.lstrip("\n"))
    if len(new) - len(new.lstrip("\n")) != old_lead:
        return "leading blank lines differ"
    if "<wordwrap>" not in p.old.lower():
        limit = max(MAX_BOX_LINES, p.old.rstrip("\n").count("\n") + 1)
        if new.rstrip("\n").count("\n") + 1 > limit:
            return f"more than {limit} lines"
    return ""


def validate_all(proposals: list[Proposal], entries) -> list[Proposal]:
    by_id = {e.id: e for e in entries}
    for p in proposals:
        p.problem = validate(p, by_id.get(p.entry_id))
    return proposals


# ── Applying (state; the game gets it on the next export) ──────────

def apply(proposals: list[Proposal], entries) -> list[str]:
    """Apply valid proposals; returns the ids of every entry changed.

    Each proposal is validated again right before it is applied. The fix
    also goes to duplicate copies (same Japanese, same old English, same
    field and speaker, status translated) so copies of a scene stay
    identical. Reviewed lines are never changed.
    """
    by_id = {e.id: e for e in entries}
    by_original: dict[str, list] = {}
    for e in entries:
        by_original.setdefault(e.original, []).append(e)
    changed = []
    for p in proposals:
        entry = by_id.get(p.entry_id)
        if validate(p, entry):
            continue
        # Copies only: same field and same speaker (a he/she or "you" fix
        # depends on who speaks), never system terms or database names.
        speaker = speaker_of(entry.context or "")
        for e in by_original.get(p.original, []):
            if (e.status == "translated" and e.translation == p.old
                    and e.field == entry.field and e.field in REVIEW_FIELDS
                    and speaker_of(e.context or "") == speaker):
                e.translation = p.new
                changed.append(e.id)
    return changed


# ── Request ────────────────────────────────────────────────────────

def request_review(client, chunk: Chunk, sheet: str) -> list[Proposal]:
    """Send one chunk through the client's chat transport; parse the reply.

    Raises ConnectionError / ValueError like the client does.
    """
    messages = build_messages(chunk, sheet)
    num_predict = max(1024, min(160 * len(chunk.lines), 8192))
    options = client._base_options(
        num_predict=num_predict,
        num_ctx=min(32768, max(8192, sum(len(m["content"]) for m in messages)
                               // 2 + num_predict)))
    data = client._chat(messages=messages, format="json",
                        timeout=180 + 10 * len(chunk.lines), options=options)
    raw = client._strip_thinking(
        (data.get("message", {}) or {}).get("content", "").strip())
    if not raw:
        return []
    return parse_review(raw, chunk)
