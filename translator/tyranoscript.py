"""TyranoScript (.ks) parser — extracts translatable text from visual novels.

Handles:
- Dialogue lines (plain text between speaker tags and [p]/[l] breaks)
- Speaker names (jname= definitions and #speaker tags)
- Choice buttons ([glink text="..."])
- Dialog prompts ([dialog text="..."])
- Inline tags preserved as placeholders: [r], [rr], [l], [p], [emb ...], [heart], etc.
"""

import os
import re
from pathlib import Path

from . import JAPANESE_RE
from .project_model import TranslationEntry

# Inline tags that appear WITHIN dialogue text — must be preserved
# [r] [rr] [l] [p] [emb exp="..."] [heart] [ruby text="..."] [graph ...]
_INLINE_TAG_RE = re.compile(
    r'\[(?:r|rr|l|p|heart|emb\s[^\]]*|ruby\s[^\]]*|graph\s[^\]]*|font\s[^\]]*|resetfont)\]',
    re.IGNORECASE,
)

# Full-line command tags — skip these lines entirely
_COMMAND_LINE_RE = re.compile(r'^\s*[\[@]')

# Speaker tag: #name or # (clear)
_SPEAKER_RE = re.compile(r'^#(\w*)$')

# jname="..." in character definition tags
_JNAME_RE = re.compile(r'jname="([^"]+)"')

# glink/button with text="..." attribute
_GLINK_TEXT_RE = re.compile(r'\[glink\s[^\]]*text="([^"]+)"[^\]]*\]', re.IGNORECASE)

# dialog with text="..." attribute
_DIALOG_TEXT_RE = re.compile(r'\[dialog\s[^\]]*text="([^"]+)"[^\]]*\]', re.IGNORECASE)

# Script blocks to skip entirely
_ISCRIPT_START = re.compile(r'^\s*\[iscript\]', re.IGNORECASE)
_ISCRIPT_END = re.compile(r'^\s*\[endscript\]', re.IGNORECASE)


class TyranoScriptParser:
    """Parser for TyranoScript (.ks) visual novel games."""

    def __init__(self):
        self.context_size = 3

    # ── Public API ─────────────────────────────────────────────

    def load_project(self, project_dir: str) -> list[TranslationEntry]:
        """Load all .ks files from a TyranoScript project.

        Args:
            project_dir: Path to the game root (containing data/scenario/).

        Returns:
            List of TranslationEntry objects.
        """
        scenario_dir = self._find_scenario_dir(project_dir)
        if not scenario_dir:
            return []

        entries = []
        ks_files = sorted(Path(scenario_dir).rglob("*.ks"))

        # First pass: collect character name definitions
        self._char_names = {}  # name_id -> jname
        for ks_path in ks_files:
            self._scan_char_names(ks_path)

        # Second pass: extract translatable text
        for ks_path in ks_files:
            rel_path = str(ks_path.relative_to(Path(scenario_dir)))
            rel_path = rel_path.replace("\\", "/")
            file_entries = self._parse_ks_file(ks_path, rel_path)
            entries.extend(file_entries)

        return entries

    def save_project(self, project_dir: str, entries: list[TranslationEntry]):
        """Write translations back into .ks files.

        Reads from data_original/ backup (creating it on first export),
        then writes translated files to data/scenario/.
        """
        scenario_dir = self._find_scenario_dir(project_dir)
        if not scenario_dir:
            return

        # Create backup on first export
        original_dir = os.path.join(
            os.path.dirname(scenario_dir), "scenario_original")
        if not os.path.isdir(original_dir):
            import shutil
            shutil.copytree(scenario_dir, original_dir)

        # Build lookup: file/line_num -> translation
        lookup: dict[str, dict[int, str]] = {}
        for entry in entries:
            if not entry.translation or entry.status not in ("translated", "reviewed"):
                continue
            file_key = entry.file
            # Parse line number from entry ID
            parts = entry.id.rsplit("/", 1)
            if len(parts) == 2 and parts[1].startswith("line_"):
                try:
                    line_num = int(parts[1][5:])
                except ValueError:
                    continue
                lookup.setdefault(file_key, {})[line_num] = entry.translation
            elif entry.field == "jname":
                # jname entries: replace in the tag
                lookup.setdefault(file_key, {})[entry.id] = entry.translation
            elif entry.field == "choice":
                lookup.setdefault(file_key, {})[entry.id] = entry.translation

        # Process each file
        for file_key, translations in lookup.items():
            # Read from backup
            src = os.path.join(original_dir, file_key)
            dst = os.path.join(scenario_dir, file_key)
            if not os.path.isfile(src):
                continue

            with open(src, "r", encoding="utf-8") as f:
                lines = f.readlines()

            new_lines = []
            for i, line in enumerate(lines):
                line_num = i + 1  # 1-indexed

                if line_num in translations:
                    # Dialogue line — replace text content, preserve structure
                    new_lines.append(
                        self._apply_dialogue_translation(
                            line, translations[line_num]) + "\n")
                else:
                    # Check for jname/choice entries by ID
                    replaced = False
                    for entry_id, trans in translations.items():
                        if isinstance(entry_id, str):
                            if entry_id.startswith(file_key + "/jname/"):
                                old_name = entry_id.split("/jname/", 1)[1]
                                if f'jname="{old_name}"' in line:
                                    line = line.replace(
                                        f'jname="{old_name}"',
                                        f'jname="{trans}"')
                                    replaced = True
                            elif entry_id.startswith(file_key + "/choice/"):
                                # Extract original text from ID
                                old_text = entry_id.split("/choice/", 1)[1]
                                if f'text="{old_text}"' in line:
                                    line = line.replace(
                                        f'text="{old_text}"',
                                        f'text="{trans}"')
                                    replaced = True
                    new_lines.append(line if not replaced else line)

            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

    # ── Detection ──────────────────────────────────────────────

    @staticmethod
    def is_tyranoscript_project(path: str) -> bool:
        """Check if a folder looks like a TyranoScript game."""
        scenario = os.path.join(path, "data", "scenario")
        if os.path.isdir(scenario):
            return any(f.endswith(".ks") for f in os.listdir(scenario))
        # Some games use extracted/ subfolder
        extracted = os.path.join(path, "extracted", "data", "scenario")
        if os.path.isdir(extracted):
            return any(f.endswith(".ks") for f in os.listdir(extracted))
        return False

    @staticmethod
    def find_nwjs_exe(path: str) -> str | None:
        """Find an NW.js executable with appended ZIP data in the folder.

        TyranoScript games are typically packaged as NW.js apps where the
        game data is appended to the exe as a ZIP archive.  Returns the
        exe path if found, None otherwise.
        """
        import zipfile
        for f in os.listdir(path):
            if not f.lower().endswith(".exe"):
                continue
            exe_path = os.path.join(path, f)
            try:
                if zipfile.is_zipfile(exe_path):
                    with zipfile.ZipFile(exe_path, "r") as zf:
                        names = zf.namelist()
                        # Check for TyranoScript signature: data/scenario/ in the zip
                        if any(n.startswith("data/scenario/") and n.endswith(".ks")
                               for n in names):
                            return exe_path
            except (OSError, zipfile.BadZipFile):
                continue
        return None

    @staticmethod
    def extract_nwjs(exe_path: str, dest_dir: str,
                     progress_cb=None) -> int:
        """Extract game data from an NW.js executable.

        Args:
            exe_path: Path to the NW.js .exe with appended ZIP.
            dest_dir: Destination folder (typically <game>/extracted/).
            progress_cb: Optional callback(current, total) for progress.

        Returns:
            Number of files extracted.
        """
        import zipfile
        os.makedirs(dest_dir, exist_ok=True)
        with zipfile.ZipFile(exe_path, "r") as zf:
            members = zf.namelist()
            total = len(members)
            for i, member in enumerate(members):
                zf.extract(member, dest_dir)
                if progress_cb and i % 50 == 0:
                    progress_cb(i, total)
            if progress_cb:
                progress_cb(total, total)
        return total

    # ── Internal ───────────────────────────────────────────────

    def _find_scenario_dir(self, project_dir: str) -> str | None:
        """Find the data/scenario/ directory."""
        candidates = [
            os.path.join(project_dir, "data", "scenario"),
            os.path.join(project_dir, "extracted", "data", "scenario"),
        ]
        for c in candidates:
            if os.path.isdir(c):
                return c
        return None

    def _scan_char_names(self, ks_path: Path):
        """Scan a .ks file for character jname definitions."""
        try:
            text = ks_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        # Find [chara_new ... name=X ... jname="Y"] and similar
        for line in text.splitlines():
            jname_match = _JNAME_RE.search(line)
            if jname_match:
                # Extract name= attribute
                name_match = re.search(r'\bname=(\w+)', line)
                if name_match:
                    self._char_names[name_match.group(1)] = jname_match.group(1)

    def _parse_ks_file(self, ks_path: Path, rel_path: str) -> list[TranslationEntry]:
        """Parse a single .ks file and extract translatable entries."""
        try:
            lines = ks_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return []

        entries = []
        recent_context = []
        current_speaker = ""
        current_speaker_id = ""
        in_script = False
        seen_jnames = set()
        seen_choices = set()

        for i, line in enumerate(lines):
            line_num = i + 1
            stripped = line.strip()

            # Skip empty lines
            if not stripped:
                continue

            # Skip script blocks
            if _ISCRIPT_START.match(stripped):
                in_script = True
                continue
            if _ISCRIPT_END.match(stripped):
                in_script = False
                continue
            if in_script:
                continue

            # Skip comments
            if stripped.startswith(";"):
                continue

            # Speaker tag
            speaker_match = _SPEAKER_RE.match(stripped)
            if speaker_match:
                current_speaker_id = speaker_match.group(1)
                current_speaker = self._char_names.get(
                    current_speaker_id, current_speaker_id)
                continue

            # jname definitions — extract for translation
            jname_match = _JNAME_RE.search(stripped)
            if jname_match:
                jname = jname_match.group(1)
                if JAPANESE_RE.search(jname):
                    entry_id = f"{rel_path}/jname/{jname}"
                    if entry_id not in seen_jnames:
                        seen_jnames.add(entry_id)
                        entries.append(TranslationEntry(
                            id=entry_id,
                            file=rel_path,
                            field="jname",
                            original=jname,
                            status="untranslated",
                        ))
                # Don't return — line may also be a command, fall through

            # Choice buttons: [glink ... text="日本語" ...]
            glink_match = _GLINK_TEXT_RE.search(stripped)
            if glink_match:
                text = glink_match.group(1)
                if JAPANESE_RE.search(text):
                    entry_id = f"{rel_path}/choice/{text}"
                    if entry_id not in seen_choices:
                        seen_choices.add(entry_id)
                        context = "\n---\n".join(recent_context[-self.context_size:])
                        entries.append(TranslationEntry(
                            id=entry_id,
                            file=rel_path,
                            field="choice",
                            original=text,
                            status="untranslated",
                            context=f"[Speaker: {current_speaker}]\n{context}" if current_speaker else context,
                        ))
                continue

            # Dialog prompts: [dialog ... text="日本語" ...]
            dialog_match = _DIALOG_TEXT_RE.search(stripped)
            if dialog_match:
                text = dialog_match.group(1)
                if JAPANESE_RE.search(text):
                    entry_id = f"{rel_path}/choice/{text}"
                    if entry_id not in seen_choices:
                        seen_choices.add(entry_id)
                        entries.append(TranslationEntry(
                            id=entry_id,
                            file=rel_path,
                            field="choice",
                            original=text,
                            status="untranslated",
                        ))
                continue

            # Skip full-line commands (but NOT dialogue that contains inline tags)
            if _COMMAND_LINE_RE.match(stripped):
                continue

            # If we get here, it's a text/dialogue line
            if not JAPANESE_RE.search(stripped):
                continue

            # Build context
            speaker_hint = ""
            if current_speaker:
                speaker_hint = f"[Speaker: {current_speaker}]"

            context_parts = []
            if speaker_hint:
                context_parts.append(speaker_hint)
            if recent_context:
                context_parts.append(
                    "\n---\n".join(recent_context[-self.context_size:]))
            context = "\n".join(context_parts)

            entry = TranslationEntry(
                id=f"{rel_path}/line_{line_num}",
                file=rel_path,
                field="dialog",
                original=stripped,
                status="untranslated",
                context=context,
            )
            entries.append(entry)

            # Add to recent context for next entries
            display = stripped
            if current_speaker:
                display = f"{current_speaker}: {stripped}"
            recent_context.append(display)
            if len(recent_context) > self.context_size + 2:
                recent_context.pop(0)

        return entries

    def _apply_dialogue_translation(self, original_line: str, translation: str) -> str:
        """Replace the text content of a dialogue line with its translation.

        Preserves leading whitespace and any trailing inline tags that
        the translation might have dropped.
        """
        # Preserve original indentation
        indent = ""
        for ch in original_line:
            if ch in " \t":
                indent += ch
            else:
                break

        return indent + translation

    def get_game_title(self, project_dir: str) -> str:
        """Try to extract game title from package.json or index.html."""
        for sub in ["", "extracted"]:
            base = os.path.join(project_dir, sub) if sub else project_dir
            pkg = os.path.join(base, "package.json")
            if os.path.isfile(pkg):
                try:
                    import json
                    with open(pkg, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    title = data.get("window", {}).get("title", "")
                    if title:
                        return title
                    title = data.get("name", "")
                    if title:
                        return title
                except Exception:
                    pass
        return ""

    # ── Word Wrap ─────────────────────────────────────────────

    @staticmethod
    def detect_line_budget(ks_contents: list[str]) -> int:
        """Derive English character budget from original JP line lengths.

        Scans all .ks file contents, splits dialogue on [r]/[p]/newlines,
        strips inline tags, and uses the 95th percentile of JP line lengths
        as the baseline (avoids outliers like HTML comments).  Since JP
        characters are full-width (~2x English), the budget is:

            english_budget = p95_jp_chars * 1.6

        Args:
            ks_contents: List of .ks file text contents (strings).

        Returns:
            English character budget per line (int).  Falls back to 55 if
            no dialogue is found (reasonable default for 800px window).
        """
        # Any [tag ...] or <!-- comment --> — strip for character counting
        tag_re = re.compile(r'\[[^\]]*\]|<!--.*?-->')
        # Split points: [r], [rr], [p], [l] and actual newlines
        split_re = re.compile(r'\[(?:r|rr|p|l)\]', re.IGNORECASE)

        lengths = []
        in_script = False

        for text in ks_contents:
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                # Skip iscript blocks
                if _ISCRIPT_START.match(stripped):
                    in_script = True
                    continue
                if _ISCRIPT_END.match(stripped):
                    in_script = False
                    continue
                if in_script:
                    continue
                # Skip comments (both ; and HTML)
                if stripped.startswith(";") or stripped.startswith("<!--"):
                    continue
                if _SPEAKER_RE.match(stripped):
                    continue
                if _COMMAND_LINE_RE.match(stripped):
                    continue
                # Must contain Japanese to be dialogue
                if not JAPANESE_RE.search(stripped):
                    continue

                # Split on line break tags
                segments = split_re.split(stripped)
                for seg in segments:
                    clean = tag_re.sub("", seg).strip()
                    if clean and JAPANESE_RE.search(clean):
                        lengths.append(len(clean))

        if not lengths:
            return 55  # safe default

        # Use 95th percentile to avoid outliers
        lengths.sort()
        p95_idx = int(len(lengths) * 0.95)
        p95 = lengths[min(p95_idx, len(lengths) - 1)]

        return int(p95 * 1.6)

    @staticmethod
    def wordwrap_translation(text: str, budget: int) -> str:
        """Insert [r] line break tags into translated text at word boundaries.

        Args:
            text: English translated text (may already contain [r]/[p] tags).
            budget: Maximum characters per line before wrapping.

        Returns:
            Text with [r] tags inserted at word boundaries.
        """
        if not text or budget <= 0:
            return text

        # Tag pattern — preserve but don't count toward width
        tag_re = re.compile(r'\[[^\]]*\]')

        # If text already has [r] tags from the LLM, strip them first
        # (we'll re-wrap properly)
        has_p = "[p]" in text.lower()
        # Split on [p] to preserve paragraph boundaries
        paragraphs = re.split(r'\[p\]', text, flags=re.IGNORECASE)

        wrapped_parts = []
        for para_idx, para in enumerate(paragraphs):
            # Remove existing [r] tags — we'll re-insert them
            para = re.sub(r'\[r\]', ' ', para, flags=re.IGNORECASE)
            para = re.sub(r'\[rr\]', ' ', para, flags=re.IGNORECASE)
            # Collapse multiple spaces
            para = re.sub(r'  +', ' ', para).strip()

            if not para:
                wrapped_parts.append(para)
                continue

            # Split into words and tags
            tokens = tag_re.split(para)
            tags = tag_re.findall(para)

            # Rebuild with word wrapping
            result_lines = []
            current_line = ""
            current_width = 0

            # Interleave text chunks and tags
            all_pieces = []
            for i, chunk in enumerate(tokens):
                all_pieces.append(("text", chunk))
                if i < len(tags):
                    all_pieces.append(("tag", tags[i]))

            for piece_type, piece in all_pieces:
                if piece_type == "tag":
                    current_line += piece
                    continue

                words = piece.split(" ")
                for wi, word in enumerate(words):
                    if not word:
                        continue
                    word_len = len(word)
                    # Check if adding this word exceeds budget
                    needed = word_len + (1 if current_width > 0 else 0)
                    if current_width + needed > budget and current_width > 0:
                        result_lines.append(current_line.rstrip())
                        current_line = word
                        current_width = word_len
                    else:
                        if current_width > 0:
                            current_line += " "
                            current_width += 1
                        current_line += word
                        current_width += word_len

            if current_line.strip():
                result_lines.append(current_line.rstrip())

            wrapped_parts.append("[r]".join(result_lines))

        # Re-join with [p] tags
        if has_p and len(paragraphs) > 1:
            return "[p]".join(wrapped_parts)
        return wrapped_parts[0] if wrapped_parts else text
