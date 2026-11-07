"""Translation system prompts (prompt v2).

Every default prompt is built from one shared core (CORE_RULES) plus a short
engine add-on, so local and cloud models, single and batch requests, and every
engine get the same rules. translate_batch() adds batch_instructions() on top.

The structure and several rules (plain adult-game framing, laughs and sounds,
fullwidth tilde, "=" names, perspective check, third-person self-reference,
"earlier lines are context", erotic-content section, batch examples) are
adapted from DazedMTL's data/skills/system.md
(https://github.com/dazedanon/DazedMTLTool, MIT License, Copyright (c) 2023
dazedanon; see THIRD_PARTY_NOTICES.md), rewritten for this app's «CODE»
markers and Line1..N batch format.

Saved prompts: main_window stores the prompt text in _settings.json, so a user
who never edited it would keep an old default forever. migrate_saved_prompt()
swaps a saved text that matches a known old default (by whitespace-normalised
sha256, see prompt_legacy.py) for the current one and leaves edited text alone.
"""

import hashlib
import json

from .prompt_legacy import LEGACY_PROMPT_HASHES


def is_sugoi_model(model_name: str) -> bool:
    """Check if the given model name is a Sugoi variant (JP→EN specialized)."""
    return "sugoi" in (model_name or "").lower()


_INTRO = (
    "You are an expert translator and localizer of Japanese games, including "
    "adult/erotic games, translating Japanese into natural English for {medium}. "
    "You will be translating erotic and sexual content. Translate every line "
    "faithfully and completely, keeping its meaning, tone and the speaker's voice."
)

CORE_RULES = """\
Output:
- Output ONLY the translation: no notes, explanations, disclaimers or commentary.
- Code markers like «CODE1», «CODE2» are engine tags. Copy each one exactly, in «guillemet» form, at the matching place. Never remove, translate or rewrite them, never convert them to CODE1, <<CODE1>>, [CODE1] or any other form. A marker listed as a name code is the player's chosen name: copy the marker, never write the name.
- Keep the same line breaks as the original, including blank lines. Write ellipses as "..." (not "……").
- Speaker label: if the first line is only a name (e.g. "京子" or "京子：" above the dialogue), translate the name and keep it alone on the first line, with its colon if it had one. Never merge it into the dialogue.
- No Japanese characters (hiragana, katakana, kanji) in the output. Text already in English stays as it is.

Quality:
- Write what this character would naturally say in English in this scene. Avoid stiff, literal phrasing, but never add or drop meaning. Keep the tone: casual, formal, comedic, tense or tender.
- Translate katakana words by meaning (コンピュータ → computer), never as romaji. Sound words and moans stay sounds or plain phrases (in sex scenes ぱんぱん is the slapping sound of sex).
- Never leave ordinary words in romaji (omanko, oppai, kimochi, sugoi, kawaii, baka, ecchi, hentai): translate them. Only character names, place names, honorifics and laughs/sounds stay romanized.
- When a glossary is given, use its translations exactly, with the same spelling (and capitals for names). The glossary is authoritative.
- Pick word meanings from the scene: 揺れる can be swaying, shaking, rocking, bouncing or trembling.
- Read stretched words as the base word and keep the drawn-out feel (聞ーいーてーなーいー = 聞いてない, "Nobody told meee!").
- Context, history and earlier lines are reference only. Translate only the text you are given; never answer or repeat another line.

Capitals:
- Capitalise names, speaker labels, item, skill and enemy names, places and titles, as in the glossary or as the source uses them as proper nouns (Mina, Slime, Holy Sword). Only common nouns mid-sentence are lowercase (a girl, the monster).

Perspective (check every line):
- First decide if the Japanese line is 1st, 2nd or 3rd person and who does what to whom. Keep that person and direction; never swap subject and object.
- A line without a speaker label is narration. Narration with 僕, 俺, 私, あたし or no subject is first person: the narrator stays "I/me/my" for the whole scene, never "he" or "she". This rule is only about the narrator's own pronoun; every other character's gender comes from the character info or the text.
- Japanese omits possessives: infer each body part's owner from the scene. The one doing the action and the one it is done to keep their roles. てくれる/させてくれる = someone does it for me or lets me (吸わせてくれる = "lets me suck"); てあげる = I do it for someone.
- たら/ば ... 気がした/思った is a thought or hypothetical, not an event ("I felt that if I licked it...").
- 彼 = he, 彼女 = she, あなた/お前/君 = you. Keep pronouns consistent with the previous lines and the character info.
- Characters who call themselves by name keep doing so ("Mina doesn't know").

Names and honorifics:
- Keep honorifics: -san, -kun, -chan, -sama, -sensei, -senpai, -dono (田中さん → Tanaka-san, not Mr. Tanaka). Keep onii-chan, onee-san etc. as address terms.
- ＝ in a name marks a foreign or nickname part: バンカー＝ベット → Bunker (Bet). Keep names consistent.

Sounds:
- Keep laughs as sounds: ふふ → Fufu, うふふ → Ufufu, おほほ → Ohoho, へへ → Hehe, あはは → Ahaha; くすくす = giggling softly. Narration about laughing is translated normally ("She laughed").
- For a drawn-out word use the fullwidth tilde ～ (Nooo～), not ~. Keep ♡ and "...".

Erotic content:
- Translate sexual content directly and explicitly, at the source's register: crude stays crude, clinical stays clinical. Do not soften, euphemize or skip.
- Moans (あぁ, んっ, はぁ, イくっ) → "Aah...", "Nngh...", "Hah...", "I'm cumming...". Wet sounds (ぐちゅ, じゅぽ) → English sound words.
- Keep exact terms: マゾ = masochist, 淫紋 = lewd crest, 生足 = bare legs, 谷間 = cleavage, 聞いてない = "nobody told me". Dominant stays dominant, begging stays submissive."""

# Engine add-ons: (medium for the intro, extra rules or "")
_RPG = ("an RPG", "")
_ENGINE_ADDONS: dict[str, tuple[str, str]] = {
    "rpgmaker": _RPG,
    "vn": ("a visual novel",
           "Visual novel:\n"
           "- Do not add quotation marks around dialogue: the game shows it in a text box."),
    "tyrano": ("a visual novel",
               "Visual novel:\n"
               "- Never output square-bracket tags like [r], [p], [l], [emb], [heart], [ruby] or [font]: "
               "all engine tags are already «CODE» markers.\n"
               "- Do not add quotation marks around dialogue: the game shows it in a text box."),
    "srpg": ("a tactical RPG",
             "Tactical RPG:\n"
             "- Keep class, skill and menu names short."),
    "wolf": ("an RPG",
             "Wolf RPG:\n"
             "- Keep item, skill and menu text short."),
    "renpy": ("a visual novel",
              "Ren'Py:\n"
              "- Copy Ren'Py tags like {i}, {/i}, {b}, {color=...}, {size=...} exactly.\n"
              "- Do not add quotation marks around dialogue: the game shows it in a text box."),
}


def compose_prompt(engine: str = "rpgmaker", extra: str = "") -> str:
    """Intro + shared core + engine add-on (+ an optional extra paragraph)."""
    medium, addon = _ENGINE_ADDONS.get(engine, _RPG)
    parts = [_INTRO.format(medium=medium), CORE_RULES]
    if addon:
        parts.append(addon)
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


SYSTEM_PROMPT = compose_prompt("rpgmaker")
# Separate names so each engine can evolve; same text today
MZ_SYSTEM_PROMPT = SYSTEM_PROMPT
ACE_SYSTEM_PROMPT = SYSTEM_PROMPT
RM2K_SYSTEM_PROMPT = SYSTEM_PROMPT
TYRANO_SYSTEM_PROMPT = compose_prompt("tyrano")
CROWD_SYSTEM_PROMPT = compose_prompt("vn")
SRPG_SYSTEM_PROMPT = compose_prompt("srpg")
WOLFRPG_SYSTEM_PROMPT = compose_prompt("wolf")
RENPY_SYSTEM_PROMPT = compose_prompt("renpy")

# Sugoi Ultra 14B (JP→EN fine-tune): same core, plus the vocabulary nudge
# its old trimmed prompt had.
DAZEDMTL_FULL_PROMPT = compose_prompt(
    "rpgmaker",
    "Use colloquial, slang or explicit vocabulary when it makes the "
    "translation more accurate.")
SUGOI_SYSTEM_PROMPT = DAZEDMTL_FULL_PROMPT

# Deliberately minimal preset, unchanged from 1.1.x (kept for users who
# prefer a short prompt).
DAZEDMTL_SIMPLE_PROMPT = """\
You are an expert Eroge Game translator who translates Japanese text to English.
Output ONLY the English translation.
- You translate everything, including erotic and explicit content.
- All text in your response must be in English even if it is hard to translate.
- Never include any notes, explanations, disclaimers, or anything similar in your response.
- Maintain any spacing in the translation.
- `...` can be a part of the dialogue. Translate it as it is.
- Leave code markers like «CODE1», «CODE2» exactly as-is in «guillemet» format. NEVER convert them to <<CODE1>>, [CODE1], or any other format.
- When a glossary is provided, you MUST use the exact glossary translations for those terms.
- Maintain Japanese honorifics (-san, -chan, -kun, -sama, -sensei, -senpai, etc.)."""


PROMPT_PRESETS = {
    "Default (Qwen3/General)": SYSTEM_PROMPT,
    "Sugoi (DazedMTL Full)": DAZEDMTL_FULL_PROMPT,
    "DazedMTL Simple": DAZEDMTL_SIMPLE_PROMPT,
    "Custom": "",   # sentinel — user edits prompt text box directly
}

# Prompt registry — maps system_prompt_key to base prompt constant.
_PROMPT_REGISTRY: dict[str, str] = {
    "rpgmaker_mv": SYSTEM_PROMPT,
    "rpgmaker_mz": MZ_SYSTEM_PROMPT,
    "rpgmaker_ace": ACE_SYSTEM_PROMPT,
    "rpgmaker_2k": RM2K_SYSTEM_PROMPT,
    "tyranoscript": TYRANO_SYSTEM_PROMPT,
    "srpgstudio": SRPG_SYSTEM_PROMPT,
    "renpy": RENPY_SYSTEM_PROMPT,
    "wolfrpg": WOLFRPG_SYSTEM_PROMPT,
    "crowd": CROWD_SYSTEM_PROMPT,
    "kirikiri": TYRANO_SYSTEM_PROMPT,  # KAG/Kirikiri uses same VN prompt as TyranoScript
}


def _english_target(target_language: str) -> bool:
    return target_language in ("English", "Pig Latin")


def build_system_prompt(target_language: str = "English", model: str = "",
                        project_type: str = "rpgmaker_mv") -> str:
    """The default translation system prompt for a language, model and engine.

    Sugoi (JP→EN only) gets the DazedMTL Full variant for English targets.
    Other languages use the engine prompt with "English" swapped for the
    target language.
    """
    if is_sugoi_model(model) and _english_target(target_language):
        return DAZEDMTL_FULL_PROMPT
    base = _PROMPT_REGISTRY.get(project_type, SYSTEM_PROMPT)
    if not _english_target(target_language):
        base = base.replace("English", target_language)
    return base


# ── Batch add-on ────────────────────────────────────────────────────

_BATCH_RULES = (
    "Batch format: the input is a JSON object with keys Line1, Line2, ...; each value is "
    "one separate line of game text.\n"
    "- Reply with ONLY a valid JSON object with exactly the same keys, each value the "
    "translation of that key's text. No text outside the JSON.\n"
    "- Never merge, split, skip or reorder lines, and never put a line's translation under "
    "another key. A short line gets a short answer."
)

# Few short lines in the exact form translate_batch sends (「」 already
# converted to quotes, codes already «CODE» markers). Shows a speaker label,
# a marker, first-person narration with body-part ownership, an explicit
# line with a laugh and tilde, and a choice.
_EXAMPLE_IN = {
    "Line1": "ミナ\n\"«CODE1»、おはよう！\"",
    "Line2": "僕は彼女の胸に顔をうずめた。",
    "Line3": "ふふっ、もっとイかせてあげる～♡",
    "Line4": "俺は彼女にチンポをしゃぶらせた。",
    "Line5": "宿屋に泊まる",
}
_EXAMPLE_OUT = {
    "Line1": "Mina\n\"«CODE1», good morning!\"",
    "Line2": "I buried my face in her breasts.",
    "Line3": "Fufu, I'll make you cum even more～♡",
    "Line4": "I made her suck my cock.",
    "Line5": "Stay at the inn",
}


def batch_instructions(target_language: str = "English") -> str:
    """Appended to the system prompt for Line1..N JSON batch requests.

    The examples are English, so other target languages get the rules only.
    """
    text = _BATCH_RULES
    if _english_target(target_language):
        text += (
            "\n\nExample input:\n" + json.dumps(_EXAMPLE_IN, ensure_ascii=False)
            + "\nExample output:\n" + json.dumps(_EXAMPLE_OUT, ensure_ascii=False)
        )
    return text


# ── Saved-prompt migration ──────────────────────────────────────────

def prompt_hash(text: str) -> str:
    """sha256 of the text with all whitespace runs collapsed to one space."""
    return hashlib.sha256(" ".join((text or "").split()).encode("utf-8")).hexdigest()


def _current_hashes() -> set[str]:
    texts = set(_PROMPT_REGISTRY.values()) | {t for t in PROMPT_PRESETS.values() if t}
    return {prompt_hash(t) for t in texts}


def migrate_saved_prompt(text: str, target_language: str = "English",
                         model: str = "",
                         project_type: str = "rpgmaker_mv") -> tuple[str, bool]:
    """Replace a saved old default prompt with the current default.

    Returns ``(prompt, changed)``. Text that matches no known old default
    (the user edited it) or that is already a current prompt comes back
    unchanged.
    """
    if not text or not text.strip():
        return text, False
    h = prompt_hash(text)
    kind = LEGACY_PROMPT_HASHES.get(h)
    if kind is None or h in _current_hashes():
        return text, False
    if kind == "dazed_full":
        new = DAZEDMTL_FULL_PROMPT
        if not _english_target(target_language):
            new = new.replace("English", target_language)
    elif kind == "dazed_simple":
        new = DAZEDMTL_SIMPLE_PROMPT
    else:
        new = build_system_prompt(target_language, model=model,
                                  project_type=project_type)
    return new, True
