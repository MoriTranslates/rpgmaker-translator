"""Ren'Py .rpy script parser — load, extract, export."""

import logging
import os
import re

from translator import game_io
from translator.project_model import TranslationEntry

log = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────

# Character definition: define alias = Character("Name", ...)
_CHAR_DEF_RE = re.compile(
    r'^define\s+(\w+)\s*=\s*Character\(\s*"((?:[^"\\\n]|\\.)*)"', re.MULTILINE)

# What may follow the string of a say statement and must be written back
# unchanged: `with dissolve`, `nointeract`, `id line_01` (any order).
_SAY_TAIL = r'((?:\s+(?:nointeract|id\s+[\w.]+|with\s+[^"#\s][^"#]*?))*)'

# Dialogue: alias "text"
_DIALOGUE_RE = re.compile(
    r'^(\s+)(\w+)\s+"((?:[^"\\]|\\.)*)"' + _SAY_TAIL + r'\s*$')

# Narration: "text" (indented, no alias, not a choice, not a define)
_NARRATION_RE = re.compile(
    r'^(\s+)"((?:[^"\\]|\\.)*)"' + _SAY_TAIL + r'\s*$')

# Menu choice: "Choice text":  (or "Choice text" if condition:)
_CHOICE_RE = re.compile(
    r'^(\s+)"((?:[^"\\]|\\.)+)"(\s+if\s+.+)?:\s*$')

# Label definition: label name:
_LABEL_RE = re.compile(r'^label\s+(\w+)\s*:')

# Lines to skip (non-translatable Ren'Py commands).
# Every keyword must end at a word boundary (\s, \s*:, \b ...) — a bare
# prefix like "pass" would also swallow dialogue from a character alias
# such as `passerby "..."`.
_SKIP_RE = re.compile(
    r'^\s*(?:'
    r'default\s|init\s|python\s*:|image\s|transform\s|'
    r'scene\s|show\s|hide\s|with\s|play\s|stop\s|queue\s|'
    r'pause\s|jump\s|call\s|return\b|pass\b|'
    r'\$|if\s|elif\s|else\s*:|for\s|while\s|'
    r'#|label\s|menu\s*:|screen\s|style\s|'
    r'window\s|nvl\s|voice\s|camera\s|'
    r'font\s|color\s|hover_color\s|outlines\s|xalign\s|yalign\s|'
    r'padding\s|margin\s*\(|size\s|text_align\s|layout\s|spacing\s|'
    r'xsize\s|ysize\s|xpos\s|ypos\s|xmaximum\s|ymaximum\s|'
    r'background\s|foreground\s|bar\s|vbar\s|at\s|use\s|'
    r'add\s|hbox\s*:|vbox\s*:|grid\s|frame\s*:|'
    r'viewport\s|textbutton\s|imagebutton\s|input\s|'
    r'key\s|on\s|tag\s|zorder\s|modal\s|'
    r'action\s|sensitive\s|insensitive\s|'
    r'text\s|timer\s|has\s|'
    r'selected_color\s|idle_color\s|hover_outlines\s|'
    r'ground\s|unscrollable\s|mousewheel\s|draggable\s|'
    r'child_size\s|scrollbars\s|side_xalign\s'
    r')',
    re.IGNORECASE)

# Standard Ren'Py boilerplate files that don't contain game dialogue
_SKIP_FILES = {"gui.rpy", "screens.rpy", "options.rpy"}

# Style/config value patterns that look like narration but aren't
_STYLE_VALUE_RE = re.compile(
    r'^[#0-9A-Fa-f]{3,8}$|'           # color codes: #fff, FF0000
    r'^[\w\-]+\.\w{2,4}$|'            # file names: font.ttf, bg.png
    r'^\d+(\.\d+)?$|'                  # bare numbers: 25, 0.5
    r'^(?:True|False|None)$',          # Python literals
    re.IGNORECASE)

# Ren'Py inline tags to extract as placeholders: {i}, {/i}, {b}, {/b},
# {color=...}, {/color}, {size=...}, {/size}, etc.
RENPY_TAG_RE = re.compile(
    r'\{/?(?:i|b|u|s|plain|color|size|font|alpha|cps|nw|fast|w|p|vspace|image|space|art)'
    r'(?:=[^}]*)?\}')

# Ren'Py [variable] interpolation, e.g. [player] or [mc.name!c].  A
# doubled "[[" is an escaped literal bracket, not an interpolation.
RENPY_INTERP_RE = re.compile(r'(?<!\[)\[(?!\[)[^\[\]\n]+\]')

# Everything _escape_rpy must leave alone in a translation
_RENPY_TOKEN_RE = re.compile(r'\[\[|\{\{|\[[^\[\]\n]*\]|\{[^{}\n]*\}|\[|\{')

# Game title in options.rpy
_TITLE_RE = re.compile(
    r'define\s+config\.name\s*=\s*_?\(\s*"([^"]*)"\s*\)')


# Folders under game/ that never hold the game's own script: Ren'Py's
# translation files (tl/), its bytecode cache and save folders.
_SKIP_DIRS = {"tl", "cache", "saves"}


def _rpy_files(game_dir: str) -> list[str]:
    """Every .rpy under *game_dir*, as sorted "/"-separated relative paths.

    Real games keep their script in subfolders (game/scripts/ch1.rpy ...),
    so the whole tree is scanned -- except tl/ (Ren'Py's own translation
    files) and cache/saves.
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(game_dir):
        rel_dir = os.path.relpath(dirpath, game_dir)
        if rel_dir == ".":
            dirnames[:] = [d for d in dirnames if d.lower() not in _SKIP_DIRS]
        dirnames.sort()
        for fn in filenames:
            if fn.endswith(".rpy"):
                rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def _read_rpy(fpath: str, lines: bool = False):
    """Read a .rpy file (UTF-8, BOM-tolerant, lossy fallback)."""
    try:
        with open(fpath, "r", encoding="utf-8-sig") as f:
            return f.readlines() if lines else f.read()
    except UnicodeDecodeError:
        with open(fpath, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines() if lines else f.read()


def _classify_line(line: str):
    """Classify one script line (without newline) for load AND export.

    Returns (kind, match).  kind is one of "label", "define", "choice",
    "dialog", "narration" or None.  Only choice/dialog/narration consume a
    dialogue index — load and export must number entries identically.
    """
    m = _LABEL_RE.match(line)
    if m:
        return "label", m
    stripped = line.strip()
    if not stripped or _SKIP_RE.match(stripped):
        return None, None
    if stripped.startswith("define "):
        return "define", _CHAR_DEF_RE.match(stripped)
    m = _CHOICE_RE.match(line)
    if m:
        return ("choice", m) if m.group(2).strip() else (None, None)
    m = _DIALOGUE_RE.match(line)
    if m:
        return ("dialog", m) if m.group(3).strip() else (None, None)
    m = _NARRATION_RE.match(line)
    if m:
        text = m.group(2).strip()
        if text and not _STYLE_VALUE_RE.match(text):
            return "narration", m
    return None, None


def _escape_backslashes(text: str, original: str = "") -> str:
    """Double every backslash that isn't a Ren'Py escape.

    Entry text keeps Ren'Py's escapes as written in the script (``\\"``,
    ``\\n``, ``\\\\``), so those stay.  ``\\ ``, ``\\%`` and ``\\'`` are
    escapes too, but an LLM writes "a \\ b" meaning a backslash: they are
    kept only when the original uses the same escape.  Anything else (a
    lone backslash, one before a letter, at the end) is a literal
    backslash and becomes ``\\\\`` so it shows in the game.
    """
    original = original or ""

    def _fix(m):
        seq = m.group(0)
        if seq in ("\\\\", '\\"', "\\n"):
            return seq
        if len(seq) == 2 and seq[1] in " %'" and seq in original:
            return seq
        return "\\\\" + seq[1:]
    return re.sub(r'\\(.|$)', _fix, text, flags=re.S)


def _escape_rpy(text: str, original: str = "") -> str:
    """Escape a translation for a Ren'Py string literal.

    * Bare double quotes are escaped (an already-escaped \\" stays as is;
      backslashes are never doubled).
    * Real newlines become the \\n escape so the literal stays on one line.
    * ``[`` and ``{`` start an interpolation / text tag in Ren'Py; an
      unknown one ("[sighs]", "{laughs}") raises an error in-game when the
      line is shown.  They are doubled (``[[`` / ``{{``) unless the token is
      a known text tag, already escaped, or was present in *original*
      (e.g. ``[player]`` or ``{w=0.5}`` carried over from the source).
    """
    text = text.replace("\r\n", "\n").replace("\n", "\\n")
    text = _escape_backslashes(text, original)
    # Escape bare quotes by walking escape pairs: after the step above every
    # backslash starts a pair, so a quote that isn't the second half of one
    # would end the literal.  (A lookbehind got `\\"` wrong: there the
    # backslash is escaped, not the quote.)
    text = re.sub(r'\\.|"', lambda m: '\\"' if m.group(0) == '"' else m.group(0),
                  text, flags=re.S)
    keep = set(RENPY_TAG_RE.findall(original or ""))
    keep.update(RENPY_INTERP_RE.findall(original or ""))
    keep.update(m.group(0) for m in re.finditer(r'\{[^{}\n]*\}', original or ""))

    def _fix(m):
        tok = m.group(0)
        if tok in ("[[", "{{") or tok in keep or RENPY_TAG_RE.fullmatch(tok):
            return tok
        return tok[0] * 2 + tok[1:]
    return _RENPY_TOKEN_RE.sub(_fix, text)


# ── Parser ────────────────────────────────────────────────────────────

class RenPyParser:
    """Parser for Ren'Py .rpy script files."""

    # ── Detection ─────────────────────────────────────────────────────

    @staticmethod
    def is_renpy_project(path: str) -> bool:
        """Return True if path looks like a Ren'Py project."""
        game_dir = os.path.join(path, "game")
        if not os.path.isdir(game_dir):
            return False
        # Must have at least one .rpy file
        has_rpy = bool(_rpy_files(game_dir))
        # Should also have renpy/ folder or a .py launcher
        has_renpy = (os.path.isdir(os.path.join(path, "renpy")) or
                     any(f.endswith(".py") for f in os.listdir(path)))
        return has_rpy and has_renpy

    # ── Load project ──────────────────────────────────────────────────

    def load_originals(self, project_dir: str) -> list[TranslationEntry]:
        """Parse the game's ORIGINAL text, for merging a game update.

        Each .rpy is read from game_original/ when it has a backup copy
        (the live file holds English after an export), else live.
        """
        return self.load_project(project_dir, prefer_backup=True)

    def load_project(self, project_dir: str,
                     context_size: int = 3,
                     prefer_backup: bool = False) -> list[TranslationEntry]:
        """Extract all translatable strings from a Ren'Py project.

        With ``prefer_backup`` each .rpy is read from ``game_original/``
        when a backup copy exists (the live file holds exported English
        after the first export) — used by the saved-state ID migration.
        """
        game_dir = os.path.join(project_dir, "game")
        backup_dir = os.path.join(project_dir, "game_original")
        entries: list[TranslationEntry] = []

        def src_path(fname):
            if prefer_backup:
                bak = os.path.join(backup_dir, fname)
                if os.path.isfile(bak):
                    return bak
            return os.path.join(game_dir, fname)

        self.load_warnings: list[str] = []
        rpy_files = _rpy_files(game_dir)

        # Parse character definitions first (for speaker context)
        self._char_names = {}
        for fname in rpy_files:
            self._parse_char_defs(src_path(fname))

        # Extract character names from names.rpy (or wherever defines are)
        for fname in rpy_files:
            fpath = src_path(fname)
            entries.extend(self._extract_char_name_entries(fpath, fname))

        # Extract translatable strings from each .rpy file
        for fname in rpy_files:
            if os.path.basename(fname) in _SKIP_FILES:
                continue
            fpath = src_path(fname)
            if not os.path.isfile(fpath):
                continue
            try:
                file_entries = self._extract_file(
                    fpath, fname, context_size)
                entries.extend(file_entries)
            except Exception as e:
                log.error("Failed to parse %s: %s", fname, e)
                self.load_warnings.append(f"{fname}: {e}")

        log.info("Loaded %d entries from Ren'Py project", len(entries))
        return entries

    def _parse_char_defs(self, fpath: str):
        """Extract character alias → name mappings from a file."""
        content = _read_rpy(fpath)
        for match in _CHAR_DEF_RE.finditer(content):
            alias, name = match.group(1), match.group(2)
            if name:  # skip empty names (narrator variants)
                self._char_names[alias] = name

    def _extract_char_name_entries(self, fpath: str,
                                   fname: str) -> list[TranslationEntry]:
        """Extract character name definitions as translatable entries."""
        entries = []
        content = _read_rpy(fpath)
        for match in _CHAR_DEF_RE.finditer(content):
            alias, name = match.group(1), match.group(2)
            if name and not name.startswith("{"):  # skip styled names
                entry_id = f"{fname}/define/{alias}"
                entries.append(TranslationEntry(
                    id=entry_id,
                    file=fname,
                    field="name",
                    original=name,
                    translation="",
                    status="untranslated",
                    context="[Character Definition]",
                ))
        return entries

    def _extract_file(self, fpath: str, fname: str,
                      context_size: int) -> list[TranslationEntry]:
        """Extract translatable strings from a single .rpy file."""
        entries = []
        recent_context: list[str] = []
        current_label = "start"
        dialogue_index = 0

        lines = _read_rpy(fpath, lines=True)

        for raw_line in lines:
            line = raw_line.rstrip("\n\r")
            kind, m = _classify_line(line)

            # Track labels for entry IDs
            if kind == "label":
                current_label = m.group(1)
                dialogue_index = 0
                continue
            # Character definitions are extracted separately
            if kind not in ("choice", "dialog", "narration"):
                continue

            ctx_parts = recent_context[-context_size:]
            if kind == "choice":
                text = m.group(2)
                entry_id = f"{fname}/{current_label}/choice_{dialogue_index}"
                field = "choice"
            elif kind == "dialog":
                alias = m.group(2)
                text = m.group(3)
                speaker = self._char_names.get(alias, alias)
                entry_id = f"{fname}/{current_label}/dialog_{dialogue_index}"
                field = "dialog"
                if speaker:
                    ctx_parts.insert(0, f"[Speaker: {speaker}]")
            else:
                text = m.group(2)
                entry_id = f"{fname}/{current_label}/dialog_{dialogue_index}"
                field = "dialog"

            entries.append(TranslationEntry(
                id=entry_id,
                file=fname,
                field=field,
                original=text,
                translation="",
                status="untranslated",
                context="\n".join(ctx_parts),
            ))
            if kind == "dialog":
                recent_context.append(f"{speaker}: {text[:60]}")
            elif kind == "narration":
                recent_context.append(text[:60])
            dialogue_index += 1

        return entries

    # ── Actors ────────────────────────────────────────────────────────

    def load_actors_raw(self, project_dir: str) -> list[dict]:
        """Load character definitions for gender dialog."""
        game_dir = os.path.join(project_dir, "game")
        self._char_names = {}
        for fname in _rpy_files(game_dir):
            self._parse_char_defs(os.path.join(game_dir, fname))

        actors = []
        for i, (alias, name) in enumerate(self._char_names.items(), 1):
            actors.append({
                "id": i,
                "name": name,
                "nickname": alias,
                "profile": "",
            })
        return actors

    # ── Game title ────────────────────────────────────────────────────

    def get_game_title(self, project_dir: str) -> str:
        """Read game title from options.rpy."""
        options = os.path.join(project_dir, "game", "options.rpy")
        if not os.path.isfile(options):
            return ""
        try:
            with open(options, "r", encoding="utf-8-sig") as f:
                content = f.read()
            match = _TITLE_RE.search(content)
            return match.group(1) if match else ""
        except Exception:
            return ""

    # ── Export ─────────────────────────────────────────────────────────

    def save_project(self, project_dir: str,
                     entries: list[TranslationEntry]):
        """Write translations back into .rpy files.

        Strategy: create backup of game/ as game_original/, then
        modify .rpy files in-place with translations.
        """
        game_dir = os.path.join(project_dir, "game")
        backup_dir = os.path.join(project_dir, "game_original")

        # Create backup on first export (built under a temp name and
        # renamed when complete, so a partial copy is never trusted)
        manifest = game_io.ExportManifest(backup_dir + ".manifest.json")
        rpy_names = _rpy_files(game_dir)
        if game_io.backup_tree_once(game_dir, backup_dir, only=rpy_names):
            manifest.save()
        # Scripts the backup has no copy of yet (it was made by a version
        # that only read game/*.rpy, or before a game update added files):
        # back them up now, before they are ever written.
        for fname in rpy_names:
            bak = os.path.join(backup_dir, fname)
            if not os.path.exists(bak):
                os.makedirs(os.path.dirname(bak), exist_ok=True)
                game_io.backup_file_once(os.path.join(game_dir, fname), bak)
        self.last_export_notes: list[str] = []
        refreshed = game_io.refresh_changed_originals(
            [(f, os.path.join(game_dir, f), os.path.join(backup_dir, f))
             for f in rpy_names], manifest)
        if refreshed:
            self.last_export_notes.append(game_io.refreshed_note(refreshed, merges_on_reopen=True))

        # Build translation map
        trans_map = {}
        for e in entries:
            if e.translation and e.status in ("translated", "reviewed"):
                trans_map[e.id] = e

        if not trans_map:
            log.warning("No translations to export")

        # Process each .rpy file
        exported = 0
        problems: list[str] = []
        changed_lines: dict[str, int] = {}
        for fname in rpy_names:
            # Read from backup (idempotent re-export)
            source = os.path.join(backup_dir, fname)
            if not os.path.isfile(source):
                source = os.path.join(game_dir, fname)
            target = os.path.join(game_dir, fname)
            try:
                count = self._export_file(source, target, fname, trans_map)
                if self._changed_lines:
                    changed_lines[fname] = self._changed_lines
                if self._unsafe_lines:
                    problems.append(
                        f"{fname}: {self._unsafe_lines} translation(s) not "
                        f"written -- they would break the script line "
                        f"(unbalanced quote or backslash)")
                manifest.record(fname, target)
            except OSError as exc:
                problems.append(f"{fname} ({exc})")
                continue
            exported += count

        manifest.finish()
        log.info("Exported %d translations to .rpy files", exported)
        game_io.raise_export_problems(problems, changed_lines)

    def _export_file(self, source_path: str, target_path: str,
                     fname: str, trans_map: dict) -> int:
        """Apply translations to a single .rpy file. Returns count.

        Entries are addressed by label + line index, so each line must still
        hold the entry's original text; otherwise (game update) the game's
        new line is kept and counted in ``self._changed_lines``.
        """
        lines = _read_rpy(source_path, lines=True)
        self._changed_lines = 0
        self._unsafe_lines = 0
        visited: set[str] = set()

        def still_there(entry, text):
            visited.add(entry.id)
            if text == entry.original:
                return True
            if entry.translation != entry.original:
                self._changed_lines += 1
            return False

        def safe(new_line, kind, escaped):
            """The rewritten line must parse as the same kind of line with
            exactly the escaped text, or it would break the script."""
            k2, m2 = _classify_line(new_line.rstrip("\n"))
            if k2 != kind or m2 is None:
                return False
            got = m2.group(3) if kind == "dialog" else m2.group(2)
            return got == escaped

        current_label = "start"
        dialogue_index = 0
        changed = False
        output_lines = list(lines)

        for line_idx, raw_line in enumerate(lines):
            line = raw_line.rstrip("\n\r")
            kind, m = _classify_line(line)

            if kind == "label":
                current_label = m.group(1)
                dialogue_index = 0
                continue

            if kind == "define":
                if m is None:
                    continue
                entry = trans_map.get(f"{fname}/define/{m.group(1)}")
                if entry and still_there(entry, m.group(2)):
                    escaped = _escape_rpy(entry.translation, entry.original)
                    new_line = raw_line.replace(
                        f'"{m.group(2)}"', f'"{escaped}"', 1)
                    if safe(new_line.strip(), "define", escaped):
                        output_lines[line_idx] = new_line
                        changed = True
                    elif entry.translation != entry.original:
                        self._unsafe_lines += 1
                continue

            if kind is None:
                continue

            prefix = "choice" if kind == "choice" else "dialog"
            entry = trans_map.get(
                f"{fname}/{current_label}/{prefix}_{dialogue_index}")
            dialogue_index += 1
            if not entry:
                continue
            text = m.group(3) if kind == "dialog" else m.group(2)
            if not still_there(entry, text):
                continue

            escaped = _escape_rpy(entry.translation, entry.original)
            indent = m.group(1)
            if kind == "choice":
                condition = m.group(3) or ""
                new_line = f'{indent}"{escaped}"{condition}:\n'
            elif kind == "dialog":
                new_line = f'{indent}{m.group(2)} "{escaped}"{m.group(4) or ""}\n'
            else:
                new_line = f'{indent}"{escaped}"{m.group(3) or ""}\n'
            if not safe(new_line, kind, escaped):
                if entry.translation != entry.original:
                    self._unsafe_lines += 1
                continue
            output_lines[line_idx] = new_line
            changed = True

        # Entries whose label / line no longer exists were never placed
        for eid, entry in trans_map.items():
            if (eid.startswith(f"{fname}/") and eid not in visited
                    and entry.translation != entry.original):
                self._changed_lines += 1

        if changed:
            game_io.atomic_write_text(target_path, "".join(output_lines))
        elif (os.path.normcase(os.path.abspath(source_path)) !=
              os.path.normcase(os.path.abspath(target_path))):
            # No translations (or all reverted) — restore pristine backup so
            # stale English from a previous export doesn't linger.
            game_io.atomic_copy(source_path, target_path)

        return sum(1 for eid in trans_map
                   if eid.startswith(f"{fname}/"))

    # ── Restore originals ─────────────────────────────────────────────

    def refresh_backups(self, project_dir: str) -> list[str]:
        """Before a resume merge: put a game update into the backup."""
        game_dir = os.path.join(project_dir, "game")
        backup_dir = os.path.join(project_dir, "game_original")
        if not os.path.isdir(backup_dir):
            return []
        return game_io.refresh_on_resume(
            [(f, os.path.join(game_dir, f), os.path.join(backup_dir, f))
             for f in _rpy_files(game_dir)], backup_dir + ".manifest.json")

    def restore_originals(self, project_dir: str):
        """Restore original .rpy files from backup."""
        game_dir = os.path.join(project_dir, "game")
        backup_dir = os.path.join(project_dir, "game_original")
        if not os.path.isdir(backup_dir):
            raise FileNotFoundError(
                "No game_original/ backup exists. Export to game first to create one.")
        restored = 0
        for fname in _rpy_files(backup_dir):
            src = os.path.join(backup_dir, fname)
            dst = os.path.join(game_dir, fname)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            game_io.atomic_copy(src, dst)
            restored += 1
        log.info("Restored %d original .rpy files", restored)
