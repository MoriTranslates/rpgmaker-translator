"""Dialog for setting the gender of speakers that aren't actors.

Lists every [Speaker: X] in the project with its line count, a suggested
gender with the reasons behind it, and sample lines (Japanese and English)
for the selected speaker, so the user can decide without knowing the
character. Suggestions are never applied unless the user asks.
"""

import html

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout,
)

from .. import narration, speakers
from . import theme

NO_NARRATOR = "(none)"

GENDER_OPTIONS = ["unknown", "female", "male"]


class SpeakerGenderDialog(QDialog):
    """Speaker -> gender table with suggestions and sample lines."""

    def __init__(self, entries, existing: dict | None = None,
                 actor_names: dict | None = None,
                 actor_genders: dict | None = None,
                 glossary: dict | None = None,
                 narrators: list | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Speakers and Narrator")
        self._narrators = list(narrators or [])
        self._narrator_suggestion = narration.suggest_narrators(
            entries, set(speakers.collect_speakers(entries)))
        self.setMinimumSize(820, 560)
        self._glossary = glossary or {}
        self._by_speaker = speakers.lines_by_speaker(entries)
        # Most lines first: the characters that matter most are on top.
        self._speakers = sorted(self._by_speaker,
                                key=lambda s: (-len(self._by_speaker[s]), s))
        current = speakers.prefill_speaker_genders(
            self._speakers, actor_names or {}, actor_genders or {},
            existing or {})
        self._suggestions = {
            sp: speakers.suggest_gender(
                sp, [e.original for e in self._by_speaker[sp]])
            for sp in self._speakers}
        self._build_ui(current)

    # ── UI ──────────────────────────────────────────────────────────
    def _build_ui(self, current: dict):
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Set the gender of each speaker so the translator uses the right "
            "pronouns (he/she) and point of view.\n"
            "Not sure? Click a speaker to read their lines. The suggestion "
            "comes from how they talk (僕/俺 vs あたし, 〜わよ/〜かしら vs "
            "〜ぜ/〜ぞ) and their name; check it before using it.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        splitter = QSplitter(Qt.Orientation.Vertical)

        self.table = QTableWidget(len(self._speakers), 4)
        self.table.setHorizontalHeaderLabels(
            ["Speaker", "Lines", "Suggested", "Gender"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 110)

        self._combos = []
        for row, sp in enumerate(self._speakers):
            en = self._glossary.get(sp, "")
            shown = f"{sp} → {en}" if en and en != sp else sp
            self._set_item(row, 0, shown, tooltip=sp)
            self._set_item(row, 1, str(len(self._by_speaker[sp])))
            g, reasons = self._suggestions[sp]
            text = (f"{g}: " if g else "no suggestion" + (": " if reasons else ""))
            text += "; ".join(reasons)
            self._set_item(row, 2, text, tooltip="\n".join(reasons) or
                           "Not enough clues in this speaker's lines")
            combo = QComboBox()
            combo.addItems(GENDER_OPTIONS)
            combo.setCurrentText(current.get(sp, "unknown"))
            self.table.setCellWidget(row, 3, combo)
            self._combos.append(combo)
        self.table.currentCellChanged.connect(
            lambda row, *_: self._show_samples(row))
        splitter.addWidget(self.table)

        self.samples = QTextBrowser()
        self.samples.setOpenLinks(False)
        splitter.addWidget(self.samples)
        splitter.setSizes([320, 220])
        layout.addWidget(splitter)
        layout.addWidget(self._build_narrator_box())

        btn_row = QHBoxLayout()
        self.use_suggestions_btn = QPushButton("Use Suggestions for Unknown")
        self.use_suggestions_btn.setToolTip(
            "Fill in the suggested gender for speakers still set to unknown. "
            "Speakers you already set are not changed.")
        self.use_suggestions_btn.clicked.connect(self.use_suggestions)
        btn_row.addWidget(self.use_suggestions_btn)
        btn_row.addStretch()
        ok_btn = QPushButton("Apply")
        ok_btn.setDefault(True)
        theme.make_primary(ok_btn)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        if self._speakers:
            self.table.selectRow(0)
            self._show_samples(0)
        else:
            self.samples.setPlainText(
                "No speakers found. Speaker names come from message headers "
                "(name boxes); this game may not use them.")

    def _build_narrator_box(self) -> QGroupBox:
        box = QGroupBox("Narrator (whose \"I\" the narration is)")
        form = QFormLayout(box)
        explain = QLabel(
            "Narration lines (no speaker) are told by this character as "
            "\"I\". Set a second narrator if some scenes are told by someone "
            "else; each scene is matched by how the narrator says \"I\" "
            "(僕, 私...). Leave it on (none) for games with no first-person "
            "narration.")
        explain.setWordWrap(True)
        form.addRow(explain)
        names = [NO_NARRATOR] + self._speakers
        self.narrator_combos = []
        for label, idx in (("Main narrator:", 0), ("Second narrator:", 1)):
            combo = QComboBox()
            combo.addItems(names)
            if idx < len(self._narrators) and self._narrators[idx] in names:
                combo.setCurrentText(self._narrators[idx])
            form.addRow(label, combo)
            self.narrator_combos.append(combo)
        picks, why = self._narrator_suggestion
        row = QHBoxLayout()
        self.narrator_hint_label = QLabel(
            ("Suggested: " + ", then ".join(picks) + f" ({why})")
            if picks else why)
        self.narrator_hint_label.setWordWrap(True)
        row.addWidget(self.narrator_hint_label, 1)
        self.use_narrators_btn = QPushButton("Use Suggested Narrators")
        self.use_narrators_btn.setEnabled(bool(picks))
        self.use_narrators_btn.clicked.connect(self.use_suggested_narrators)
        row.addWidget(self.use_narrators_btn)
        form.addRow(row)
        return box

    def use_suggested_narrators(self):
        picks, _ = self._narrator_suggestion
        for i, combo in enumerate(self.narrator_combos):
            combo.setCurrentText(picks[i] if i < len(picks) else NO_NARRATOR)

    def get_narrators(self) -> list[str]:
        """[main, second] narrators chosen (none left out, no duplicates)."""
        out: list[str] = []
        for combo in self.narrator_combos:
            name = combo.currentText()
            if name != NO_NARRATOR and name not in out:
                out.append(name)
        return out

    def _set_item(self, row, col, text, tooltip=""):
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if tooltip:
            item.setToolTip(tooltip)
        self.table.setItem(row, col, item)

    def _show_samples(self, row: int):
        if not 0 <= row < len(self._speakers):
            return
        sp = self._speakers[row]
        parts = [f"<b>{html.escape(sp)}</b> — sample lines:<br>"]
        for e in speakers.sample_lines(self._by_speaker[sp]):
            jp = html.escape(e.original.strip()).replace("\n", "<br>")
            parts.append(f"<p style='margin:4px 0'>{jp}")
            if e.translation:
                en = html.escape(e.translation.strip()).replace("\n", "<br>")
                parts.append(f"<br><span style='color:gray'>{en}</span>")
            parts.append("</p>")
        self.samples.setHtml("".join(parts))

    # ── Actions / result ────────────────────────────────────────────
    def use_suggestions(self) -> int:
        """Fill suggestions into rows still on unknown. Returns rows changed."""
        changed = 0
        for sp, combo in zip(self._speakers, self._combos):
            g, _ = self._suggestions[sp]
            if g and combo.currentText() == "unknown":
                combo.setCurrentText(g)
                changed += 1
        return changed

    def get_genders(self) -> dict:
        """{speaker: gender} for every listed speaker (unknown included, so
        clearing a speaker back to unknown is saved too)."""
        return {sp: combo.currentText()
                for sp, combo in zip(self._speakers, self._combos)}
