"""Spell checker for translation editors — red underlines + right-click suggestions."""

import re
from spellchecker import SpellChecker

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QAction, QColor, QSyntaxHighlighter, QTextCharFormat, QTextCursor,
)
from PyQt6.QtWidgets import QTextEdit

from translator import CONTROL_CODE_RE, TYRANO_CODE_RE

# Combined regex: skip control codes, placeholders, and non-word tokens
_SKIP_RE = re.compile(
    CONTROL_CODE_RE.pattern
    + r"|" + TYRANO_CODE_RE.pattern
    + r"|«CODE\d+»"        # guillemet placeholders
    + r"|@[a-zA-Z]"        # TakanoScript inline codes like @n
)

# Word boundary regex — English words including contractions
_WORD_RE = re.compile(r"\b[A-Za-z]+(?:'[A-Za-z]+)?\b")

# Common Japanese honorifics and terms our translator preserves
_DEFAULT_KNOWN = {
    "san", "chan", "kun", "sama", "senpai", "sempai", "sensei",
    "dono", "tan", "nee", "nii", "onee", "onii", "okaasan", "otousan",
    "kohai", "kouhai", "aniki", "aneki",
    # Common game/VN terms
    "isekai", "mana", "ero", "ecchi", "hentai", "senpai",
    "baka", "kawaii", "sugoi", "yandere", "tsundere", "kuudere",
    "chibi", "shoujo", "shounen", "seinen", "josei",
    "onsen", "futon", "tatami", "yukata", "kimono",
    "katana", "shuriken", "kunai", "jutsu", "ninjutsu",
    "okaa", "otou", "obaa", "ojii", "imouto", "otouto",
}


class SpellHighlighter(QSyntaxHighlighter):
    """QSyntaxHighlighter that underlines misspelled English words."""

    def __init__(self, document, custom_words: set[str] | None = None):
        super().__init__(document)
        self._checker = SpellChecker()
        self._custom_words: set[str] = set(_DEFAULT_KNOWN)
        if custom_words:
            self._custom_words.update(custom_words)
        self._enabled = True

        self._fmt = QTextCharFormat()
        self._fmt.setUnderlineStyle(
            QTextCharFormat.UnderlineStyle.SpellCheckUnderline
        )
        self._fmt.setUnderlineColor(QColor("red"))

    def highlightBlock(self, text: str):
        if not self._enabled or not text:
            return

        # Build set of character positions covered by control codes
        protected = set()
        for m in _SKIP_RE.finditer(text):
            protected.update(range(m.start(), m.end()))

        # Find English words and check spelling
        for m in _WORD_RE.finditer(text):
            # Skip if any char overlaps a control code
            if protected & set(range(m.start(), m.end())):
                continue

            word = m.group()
            if len(word) <= 1:
                continue

            low = word.lower()
            if low in self._custom_words:
                continue

            if self._checker.unknown([low]):
                self.setFormat(m.start(), len(word), self._fmt)

    def is_misspelled(self, word: str) -> bool:
        low = word.lower()
        if low in self._custom_words:
            return False
        return bool(self._checker.unknown([low]))

    def suggestions(self, word: str) -> list[str]:
        candidates = self._checker.candidates(word.lower())
        if not candidates:
            return []
        return sorted(candidates)[:5]

    def add_word(self, word: str):
        self._custom_words.add(word.lower())
        self.rehighlight()

    def load_glossary(self, glossary: dict[str, str]):
        """Add English glossary values as known words."""
        for en_term in glossary.values():
            for word in _WORD_RE.findall(en_term):
                if len(word) > 1:
                    self._custom_words.add(word.lower())
        self.rehighlight()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self.rehighlight()


def build_spell_menu_actions(
    highlighter: SpellHighlighter,
    editor: QTextEdit,
    menu,
    pos,
):
    """Insert spelling suggestions at the top of a context menu."""
    cursor = editor.cursorForPosition(pos)
    cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    word = cursor.selectedText().strip()

    if not word or not word.isalpha() or len(word) <= 1:
        return

    if not highlighter.is_misspelled(word):
        return

    actions = menu.actions()
    first = actions[0] if actions else None

    # Suggestions
    sug_list = highlighter.suggestions(word)
    if sug_list:
        for suggestion in sug_list:
            action = QAction(suggestion, menu)
            action.setFont(action.font())
            # Bold the suggestion to stand out
            f = action.font()
            f.setBold(True)
            action.setFont(f)
            action.triggered.connect(
                lambda checked, s=suggestion, c=cursor: _replace_word(c, s)
            )
            menu.insertAction(first, action)
    else:
        no_sug = QAction("(no suggestions)", menu)
        no_sug.setEnabled(False)
        menu.insertAction(first, no_sug)

    # Add to dictionary
    add_action = QAction(f'Add "{word}" to Dictionary', menu)
    add_action.triggered.connect(lambda: highlighter.add_word(word))
    menu.insertAction(first, add_action)

    sep = menu.insertSeparator(first)


def _replace_word(cursor: QTextCursor, replacement: str):
    """Replace the selected word under cursor with the suggestion."""
    cursor.insertText(replacement)
