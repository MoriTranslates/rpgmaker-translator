"""Kirikiri / KAG visual novel engine parser (.ks script files).

Supports Kirikiri2/KAG3 games (2000s–present). KAG is the scripting layer
that TyranoScript was later built on, so the syntax is similar but not identical.

File format (.ks):
  - Plain text (cp932 or UTF-8), line-oriented
  - @name chara="Speaker" — sets speaker for following dialogue
  - Plain text lines after @name — dialogue content (may span multiple lines)
  - @e — end of unvoiced text block
  - @ve — end of voiced text block
  - @PV storage="voice_file" — voice cue (before @name)
  - *label — jump target
  - *| — page break (separates dialogue pages)
  - @ or [ prefix — engine commands (skip)
  - ; prefix — comments (skip)
  - 地 (chi) as chara name — narration (narrator voice)

Project structure:
  - data/scenario/*.ks — script files
  - Some games pack everything into .xp3 archives (not yet supported)
"""

import logging
import os
import re
import shutil
from pathlib import Path

from .project_model import TranslationEntry
from . import JAPANESE_RE

log = logging.getLogger(__name__)

# Speaker tag: @name chara="Speaker Name"
_NAME_TAG = re.compile(r'@name\s+chara="([^"]*)"', re.IGNORECASE)

# Voice cue: @PV storage="voice_file"
_VOICE_TAG = re.compile(r'@PV\s+storage="([^"]*)"', re.IGNORECASE)

# End markers
_END_UNVOICED = re.compile(r'^@e\s*$', re.IGNORECASE)
_END_VOICED = re.compile(r'^@ve\s*$', re.IGNORECASE)

# Command line: starts with @ or [
_COMMAND_LINE = re.compile(r'^[@\[]')

# Label line: starts with *
_LABEL_LINE = re.compile(r'^\*')

# Comment line: starts with ;
_COMMENT_LINE = re.compile(r'^;')


class KirikiriParser:
    """Parser for Kirikiri/KAG .ks script files."""

    def __init__(self):
        self.context_size = 3

    def load_project(self, project_dir: str, context_size: int = 3) -> list[TranslationEntry]:
        """Parse all .ks files from a Kirikiri project."""
        self.context_size = context_size
        scenario_dir = self._find_scenario_dir(project_dir)
        if not scenario_dir:
            log.warning("No scenario directory found in %s", project_dir)
            return []

        entries = []
        ks_files = sorted(Path(scenario_dir).rglob("*.ks"))

        for ks_path in ks_files:
            rel_path = str(ks_path.relative_to(Path(scenario_dir)))
            rel_path = rel_path.replace("\\", "/")
            file_entries = self._parse_ks_file(ks_path, rel_path)
            entries.extend(file_entries)

        log.info("Parsed %d entries from %d .ks files", len(entries), len(ks_files))
        return entries

    def save_project(self, project_dir: str, entries: list[TranslationEntry]):
        """Export translations back into .ks files."""
        scenario_dir = self._find_scenario_dir(project_dir)
        if not scenario_dir:
            log.error("No scenario directory found for export")
            return

        backup_dir = os.path.join(os.path.dirname(scenario_dir), "scenario_original")

        # Create backup on first export
        if not os.path.exists(backup_dir):
            shutil.copytree(scenario_dir, backup_dir)
            log.info("Backed up scenario/ to scenario_original/")

        # Group entries by file
        by_file: dict[str, list[TranslationEntry]] = {}
        for entry in entries:
            if entry.translation and entry.status in ("translated", "reviewed"):
                by_file.setdefault(entry.file, []).append(entry)

        export_count = 0
        for rel_path, file_entries in by_file.items():
            # Always read from backup for idempotent re-export
            backup_path = os.path.join(backup_dir, rel_path)
            live_path = os.path.join(scenario_dir, rel_path)
            source = backup_path if os.path.exists(backup_path) else live_path

            if not os.path.exists(source):
                log.warning("Source file not found: %s", source)
                continue

            content = self._read_file(source)
            lines = content.split("\n")

            # Build translation map: line_number -> entry
            trans_map = {}
            for entry in file_entries:
                # ID format: "rel_path/dialogue/LINE_START"
                parts = entry.id.rsplit("/", 2)
                if len(parts) >= 3:
                    try:
                        line_num = int(parts[-1])
                        trans_map[line_num] = entry
                    except ValueError:
                        continue

            translated_lines = self._apply_translations(lines, trans_map)
            translated_content = "\n".join(translated_lines)

            # Detect encoding from source
            encoding = self._detect_encoding(source)
            os.makedirs(os.path.dirname(live_path), exist_ok=True)
            with open(live_path, "w", encoding=encoding, errors="replace") as f:
                f.write(translated_content)

            export_count += len(file_entries)

        log.info("Exported %d translations to scenario/", export_count)

    def restore_originals(self, project_dir: str):
        """Restore original scenario files from backup."""
        scenario_dir = self._find_scenario_dir(project_dir)
        if not scenario_dir:
            return
        backup_dir = os.path.join(os.path.dirname(scenario_dir), "scenario_original")
        if not os.path.isdir(backup_dir):
            log.warning("No scenario_original/ backup found")
            return
        shutil.rmtree(scenario_dir)
        shutil.copytree(backup_dir, scenario_dir)
        log.info("Restored scenario/ from backup")

    def get_game_title(self, project_dir: str) -> str:
        """Try to extract game title from startup.tjs or folder name."""
        # Check startup.tjs for title
        for data_dir in [
            os.path.join(project_dir, "data"),
            project_dir,
        ]:
            startup = os.path.join(data_dir, "startup.tjs")
            if os.path.exists(startup):
                try:
                    text = self._read_file(startup)
                    # Look for ;System.title = "..." or System.title = "..."
                    m = re.search(r'System\.title\s*=\s*"([^"]+)"', text)
                    if m:
                        return m.group(1)
                except Exception:
                    pass
        return os.path.basename(project_dir)

    @staticmethod
    def is_kirikiri_project(path: str) -> bool:
        """Check if path is a Kirikiri/KAG project.

        Looks for data/scenario/*.ks with @name chara= tags (KAG style),
        or a startup.tjs file (Kirikiri engine marker).
        """
        if not os.path.isdir(path):
            return False

        # Check for startup.tjs (definitive Kirikiri marker)
        if os.path.exists(os.path.join(path, "data", "startup.tjs")):
            return True

        # Check for data/scenario/*.ks with @name chara= (KAG, not TyranoScript)
        scenario_dir = os.path.join(path, "data", "scenario")
        if os.path.isdir(scenario_dir):
            for name in os.listdir(scenario_dir):
                if name.lower().endswith(".ks"):
                    try:
                        ks_path = os.path.join(scenario_dir, name)
                        with open(ks_path, "rb") as f:
                            head = f.read(4096)
                        text = head.decode("cp932", errors="replace")
                        if "@name " in text.lower() and "chara=" in text.lower():
                            return True
                    except Exception:
                        continue

        return False

    # ── Private helpers ─────────────────────────────────────

    def _find_scenario_dir(self, project_dir: str) -> str | None:
        """Find the data/scenario/ directory."""
        candidates = [
            os.path.join(project_dir, "data", "scenario"),
            os.path.join(project_dir, "scenario"),
        ]
        for d in candidates:
            if os.path.isdir(d):
                return d
        return None

    def _detect_encoding(self, path: str) -> str:
        """Detect if a file is UTF-8 or cp932."""
        with open(path, "rb") as f:
            raw = f.read()
        # BOM check
        if raw[:3] == b"\xef\xbb\xbf":
            return "utf-8-sig"
        try:
            raw.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            return "cp932"

    def _read_file(self, path: str) -> str:
        """Read a text file, auto-detecting encoding."""
        encoding = self._detect_encoding(path)
        with open(path, "r", encoding=encoding, errors="replace") as f:
            return f.read()

    def _parse_ks_file(self, ks_path: Path, rel_path: str) -> list[TranslationEntry]:
        """Parse a single .ks file into TranslationEntry list."""
        content = self._read_file(str(ks_path))
        lines = content.split("\n")
        entries = []
        recent_context: list[str] = []
        current_label = ""

        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            stripped = line.strip()

            # Track labels for context
            if stripped.startswith("*") and not stripped.startswith("*|"):
                current_label = stripped[1:]
                i += 1
                continue

            # Look for @name chara="..." to start a dialogue block
            name_m = _NAME_TAG.search(stripped)
            if name_m:
                speaker = name_m.group(1)
                text_start = i + 1
                text_lines = []

                # Collect text lines until @e, @ve, or next command
                j = text_start
                while j < len(lines):
                    tline = lines[j].rstrip()
                    tstripped = tline.strip()

                    if not tstripped:
                        # Blank line might be intentional spacing
                        j += 1
                        continue

                    if (_END_UNVOICED.match(tstripped) or
                            _END_VOICED.match(tstripped)):
                        j += 1  # consume the @e/@ve
                        break

                    if (_COMMAND_LINE.match(tstripped) or
                            _LABEL_LINE.match(tstripped) or
                            _COMMENT_LINE.match(tstripped)):
                        break  # hit a command, don't consume it

                    text_lines.append(tline)
                    j += 1

                if text_lines:
                    # Join multi-line text
                    full_text = "\n".join(text_lines)

                    # Determine field type
                    if speaker == "地":
                        field = "narration"
                        display_speaker = ""
                    else:
                        field = "dialogue"
                        display_speaker = speaker

                    # Only include entries with translatable text
                    if JAPANESE_RE.search(full_text) or self._has_translatable_text(full_text):
                        entry_id = f"{rel_path}/{field}/{text_start}"

                        # Build context
                        ctx_parts = []
                        if current_label:
                            ctx_parts.append(f"[Label: {current_label}]")
                        ctx_parts.extend(recent_context[-self.context_size:])

                        entry = TranslationEntry(
                            id=entry_id,
                            file=rel_path,
                            field=field,
                            original=full_text,
                            context="\n".join(ctx_parts),
                            namebox=display_speaker,
                        )
                        entries.append(entry)

                        # Update context
                        ctx_line = (f"[{display_speaker}] {full_text}"
                                    if display_speaker else full_text)
                        recent_context.append(ctx_line)
                        if len(recent_context) > self.context_size:
                            recent_context.pop(0)

                i = j
                continue

            i += 1

        return entries

    def _has_translatable_text(self, text: str) -> bool:
        """Check if text has content worth translating (non-empty, non-command)."""
        # Already translated text (English) is still valid content
        stripped = text.strip()
        if not stripped:
            return False
        # Skip if it's only whitespace, numbers, or punctuation
        if re.match(r'^[\s\d\W]*$', stripped):
            return False
        return True

    def _apply_translations(self, lines: list[str], trans_map: dict[int, TranslationEntry]) -> list[str]:
        """Apply translations to a list of source lines."""
        result = list(lines)
        # Process in reverse order so line number shifts don't affect earlier entries
        for line_num in sorted(trans_map.keys(), reverse=True):
            entry = trans_map[line_num]
            if not entry.translation:
                continue

            # Find the extent of the original text block
            original_lines = entry.original.split("\n")
            num_original = len(original_lines)
            translation_lines = entry.translation.split("\n")

            # Replace the original text lines with translation
            # line_num is 0-indexed (the first text line after @name)
            start = line_num
            end = start + num_original

            # Pad or trim translation to match original line count
            # (preserves @e/@ve alignment)
            while len(translation_lines) < num_original:
                translation_lines[-1] += " "  # pad last line
                if len(translation_lines) < num_original:
                    translation_lines.append("")
            if len(translation_lines) > num_original:
                # Join excess into the last line
                last = " ".join(translation_lines[num_original - 1:])
                translation_lines = translation_lines[:num_original - 1] + [last]

            result[start:end] = translation_lines

        return result
