"""RPG Maker Translator — shared constants."""

import re

# Regex matching RPG Maker control codes that the LLM should never touch.
# Order matters — longer patterns first to avoid partial matches.
CONTROL_CODE_RE = re.compile(
    r'\\[A-Za-z]+\[\d*\]'      # \V[1], \N[2], \C[3], \FS[24], etc.
    r'|\\[{}$.|!><^]'           # \{, \}, \$, \., \|, \!, \>, \<, \^
    r'|<[^>]+>'                 # HTML-like tags: <br>, <WordWrap>, <B>, etc.
    r'|%\d+'                    # %1, %2, etc. — RPG Maker format specifiers
)

# Regex matching TyranoScript inline tags that appear within dialogue.
# [r] [rr] [l] [p] [heart] [emb exp="..."] [ruby text="..."] [graph ...]
# [font ...] [resetfont] — must be extracted as placeholders before LLM.
TYRANO_CODE_RE = re.compile(
    r'\[(?:r|rr|l|p|heart'
    r'|emb\s[^\]]*'
    r'|ruby\s[^\]]*'
    r'|graph\s[^\]]*'
    r'|font\s[^\]]*'
    r'|resetfont)\]',
    re.IGNORECASE,
)

# Japanese characters — hiragana, katakana, CJK kanji.
JAPANESE_RE = re.compile(
    r'[\u3040-\u309F'   # Hiragana
    r'\u30A0-\u30FF'    # Katakana
    r'\u4E00-\u9FFF'    # CJK Unified Ideographs (kanji)
    r'\u3400-\u4DBF'    # CJK Extension A
    r'\uFF65-\uFF9F]'   # Halfwidth Katakana
)
