"""CSV-based game engine parser (.x and similar delimited text files).

Handles games that store dialogue in CSV files with Japanese and English
columns. Auto-detects column layout by scanning headers and content.

Known games: Reversi of Temptation (.x files in data/ folder)
"""

import csv
import logging
import os
import re
import shutil
from pathlib import Path

from .project_model import TranslationEntry
from . import JAPANESE_RE

log = logging.getLogger(__name__)

# Columns that indicate a dialogue row (command column value)
_DIALOG_COMMANDS = {"s"}


class CSVGameParser:
    """Parser for CSV-based game script files."""

    def __init__(self):
        self.context_size = 3
        self.extensions = {".x"}  # file extensions to scan

    @staticmethod
    def is_csv_game_project(path: str) -> bool:
        """Check if path contains CSV game data files with Japanese text."""
        if not os.path.isdir(path):
            return False

        # Look for data/ folder with .x files containing JP CSV
        data_dir = os.path.join(path, "data")
        if not os.path.isdir(data_dir):
            return False

        for name in os.listdir(data_dir):
            if not name.lower().endswith(".x"):
                continue
            try:
                fpath = os.path.join(data_dir, name)
                with open(fpath, "r", encoding="utf-8-sig") as f:
                    head = f.read(4096)
                # Must be CSV-like with commas and Japanese text
                if "," in head and JAPANESE_RE.search(head):
                    return True
            except Exception:
                continue
        return False

    def load_project(self, project_dir: str, context_size: int | None = None
                     ) -> list[TranslationEntry]:
        """Parse all CSV game files and extract translatable entries."""
        if context_size is not None:
            self.context_size = context_size

        data_dir = os.path.join(project_dir, "data")
        if not os.path.isdir(data_dir):
            log.warning("No data/ folder in %s", project_dir)
            return []

        entries = []
        for name in sorted(os.listdir(data_dir)):
            if not any(name.lower().endswith(ext) for ext in self.extensions):
                continue
            fpath = os.path.join(data_dir, name)
            file_entries = self._parse_file(fpath, name)
            if file_entries:
                entries.extend(file_entries)
                log.info("Parsed %d entries from %s", len(file_entries), name)

        log.info("Total: %d translatable entries from CSV files", len(entries))
        return entries

    def _parse_file(self, fpath: str, filename: str) -> list[TranslationEntry]:
        """Parse a single CSV file into TranslationEntry list."""
        try:
            with open(fpath, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except Exception as e:
            log.warning("Failed to read %s: %s", filename, e)
            return []

        # Must have commas and Japanese text
        if "," not in content or not JAPANESE_RE.search(content):
            return []

        rows = list(csv.reader(content.splitlines()))
        if len(rows) < 2:
            return []

        header = rows[0]
        # Auto-detect JP and EN columns
        jp_col, en_col = self._detect_columns(header, rows[1:])
        if jp_col < 0:
            return []

        entries = []
        recent_context: list[str] = []

        for row_idx, row in enumerate(rows[1:], start=2):
            if jp_col >= len(row):
                continue

            jp_text = row[jp_col].strip()
            if not jp_text or not JAPANESE_RE.search(jp_text):
                continue

            # Get existing EN translation if available
            en_text = ""
            if 0 <= en_col < len(row):
                en_text = row[en_col].strip()

            # Build context
            ctx = "\n".join(recent_context[-self.context_size:])

            # Determine field type from command column (if present)
            field = "dialog"
            cmd_col = self._find_cmd_col(header)
            if cmd_col >= 0 and cmd_col < len(row):
                cmd = row[cmd_col].strip().lower()
                if cmd == "s":
                    field = "dialog"
                elif cmd in ("str_01", "start"):
                    field = "narration"
                else:
                    field = "dialog"

            # Detect speaker from adjacent column
            speaker = ""
            speaker_col = self._find_speaker_col(header, jp_col)
            if 0 <= speaker_col < len(row):
                sp = row[speaker_col].strip()
                if sp in ("s", "d"):
                    speaker = "Speaker" if sp == "s" else "Protagonist"

            entry = TranslationEntry(
                id=f"{filename}/row_{row_idx}/col_{jp_col}",
                file=filename,
                field=field,
                original=jp_text,
                translation=en_text,
                status="translated" if en_text else "untranslated",
                context=ctx,
                namebox=speaker,
            )
            entries.append(entry)

            recent_context.append(jp_text)
            if len(recent_context) > self.context_size:
                recent_context.pop(0)

        return entries

    def save_project(self, project_dir: str, entries: list[TranslationEntry],
                     global_speakers: dict | None = None):
        """Write translations back into CSV files."""
        data_dir = os.path.join(project_dir, "data")
        backup_dir = os.path.join(project_dir, "data_original")

        # Group entries by file
        entries_by_file: dict[str, list[TranslationEntry]] = {}
        for entry in entries:
            if entry.translation:
                entries_by_file.setdefault(entry.file, []).append(entry)

        for filename, file_entries in entries_by_file.items():
            fpath = os.path.join(data_dir, filename)
            if not os.path.isfile(fpath):
                continue

            # Backup on first export
            os.makedirs(backup_dir, exist_ok=True)
            backup = os.path.join(backup_dir, filename)
            if not os.path.isfile(backup):
                shutil.copy2(fpath, backup)
                log.info("Backed up %s to data_original/", filename)

            # Read from backup for idempotent re-export
            source = backup if os.path.isfile(backup) else fpath
            with open(source, "r", encoding="utf-8-sig") as f:
                content = f.read()

            rows = list(csv.reader(content.splitlines()))
            if len(rows) < 2:
                continue

            header = rows[0]
            jp_col, en_col = self._detect_columns(header, rows[1:])
            if jp_col < 0 or en_col < 0:
                continue

            # Build translation lookup: row_idx -> translation
            trans_map = {}
            for entry in file_entries:
                # Parse row index from entry ID
                parts = entry.id.split("/")
                for p in parts:
                    if p.startswith("row_"):
                        try:
                            row_idx = int(p[4:])
                            trans_map[row_idx] = entry.translation
                        except ValueError:
                            pass

            # Apply translations
            applied = 0
            for row_idx, translation in trans_map.items():
                if row_idx < len(rows):
                    # Ensure row has enough columns
                    while len(rows[row_idx - 1]) <= en_col:
                        rows[row_idx - 1].append("")
                    rows[row_idx - 1][en_col] = translation
                    applied += 1

            # Write back
            with open(fpath, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f, lineterminator="\r\n")
                for row in rows:
                    writer.writerow(row)

            log.info("Exported %d translations to %s", applied, filename)

    def restore_originals(self, project_dir: str):
        """Restore original files from backup."""
        backup_dir = os.path.join(project_dir, "data_original")
        data_dir = os.path.join(project_dir, "data")
        if not os.path.isdir(backup_dir):
            log.warning("No data_original/ backup found")
            return
        for name in os.listdir(backup_dir):
            src = os.path.join(backup_dir, name)
            dst = os.path.join(data_dir, name)
            shutil.copy2(src, dst)
            log.info("Restored %s", name)

    def get_game_title(self, project_dir: str) -> str:
        """Try to read game title from package.json or folder name."""
        pkg = os.path.join(project_dir, "package.json")
        if os.path.isfile(pkg):
            import json
            try:
                with open(pkg, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("name", "") or data.get("title", "")
            except Exception:
                pass
        return os.path.basename(project_dir)

    # ── Column detection ──────────────────────────────────────────

    def _detect_columns(self, header: list[str], data_rows: list[list[str]]
                        ) -> tuple[int, int]:
        """Auto-detect which columns contain JP text and EN translation.

        Returns (jp_col, en_col). en_col is -1 if no EN column found.
        """
        # Known header names for JP text
        jp_names = {"hen2", "文章", "セリフ", "text", "jp", "japanese"}
        en_names = {"hen2_en", "en", "english", "translation"}

        jp_col = -1
        en_col = -1

        # Try matching by header name
        header_lower = [h.strip().lower() for h in header]
        for i, h in enumerate(header_lower):
            if h in jp_names:
                jp_col = i
            elif h in en_names:
                en_col = i

        # If no header match, scan data for Japanese content
        if jp_col < 0:
            col_jp_count = [0] * len(header)
            for row in data_rows[:50]:
                for ci, cell in enumerate(row):
                    if JAPANESE_RE.search(cell):
                        col_jp_count[ci] += 1
            # Pick the column with most Japanese content
            if any(c > 0 for c in col_jp_count):
                jp_col = col_jp_count.index(max(col_jp_count))

        # EN column is typically right after JP column
        if jp_col >= 0 and en_col < 0:
            candidate = jp_col + 1
            if candidate < len(header):
                en_col = candidate

        return jp_col, en_col

    def _find_cmd_col(self, header: list[str]) -> int:
        """Find the command column (usually 'com')."""
        for i, h in enumerate(header):
            if h.strip().lower() in ("com", "command", "cmd"):
                return i
        return -1

    def _find_speaker_col(self, header: list[str], jp_col: int) -> int:
        """Find the speaker/type column (usually before JP column)."""
        for i, h in enumerate(header):
            if h.strip().lower() in ("hen1", "セリフ1"):
                return i
        # Default: column before JP text
        return jp_col - 1 if jp_col > 0 else -1
