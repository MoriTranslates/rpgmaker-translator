"""RPG Maker VX Ace (.rvdata2) parser and writer.

Handles extraction of translatable strings from RPG Maker VX Ace data files
(Ruby Marshal format) and writing translations back.  Uses the same event
command codes as MV/MZ (101, 401, 102, 405, 320, 324, 325, 356) since the
format is identical — just stored as Ruby objects instead of JSON.

Requires: rubymarshal  (pip install rubymarshal)

File layout expected::

    GameFolder/
    ├── Game.exe / Game.rgss3a (optional encrypted archive)
    ├── Data/
    │   ├── Actors.rvdata2
    │   ├── Items.rvdata2
    │   ├── Map001.rvdata2
    │   ├── System.rvdata2
    │   ├── CommonEvents.rvdata2
    │   ├── Troops.rvdata2
    │   └── ...
    └── Graphics/, Audio/, ...

Ruby Marshal objects use @-prefixed attribute names:
  RPG::Actor  → @name, @nickname, @description, @note, @face_name, @face_index
  RPG::Item   → @name, @description, @note
  RPG::Map    → @events {id → RPG::Event}, @display_name
  RPG::Event  → @name, @pages [RPG::Event::Page]
  Page        → @list [RPG::EventCommand]
  EventCommand→ @code, @parameters
"""

import logging
import os
import re
from collections import deque
from typing import Optional

from . import JAPANESE_RE, game_io
from .project_model import TranslationEntry
from .ruby_marshal import loads as ruby_loads, writes as ruby_writes


log = logging.getLogger(__name__)

# ── Event command codes (identical to MV/MZ) ─────────────────────────
CODE_SHOW_TEXT_HEADER = 101   # face/position setup — not translatable itself
CODE_SHOW_TEXT = 401          # Show Text line — @parameters[0] is text
CODE_SHOW_CHOICES = 102       # Show Choices — @parameters[0] is list of strings
CODE_SCROLL_TEXT_HEADER = 105 # Scroll Text setup — not translatable
CODE_SCROLL_TEXT = 405        # Scroll Text line — @parameters[0] is text
CODE_CHANGE_NAME = 320        # Change Actor Name — params[0]=actorId, params[1]=name
CODE_CHANGE_NICKNAME = 324    # Change Actor Nickname
CODE_CHANGE_PROFILE = 325     # Change Actor Profile
CODE_PLUGIN_COMMAND = 356     # Script-based plugin command (MV-style, rare in Ace)

# Database files and their translatable fields (@ prefix stripped for lookup)
DATABASE_FILES = {
    "Actors.rvdata2":   ["name", "nickname", "description"],
    "Classes.rvdata2":  ["name"],
    "Items.rvdata2":    ["name", "description"],
    "Weapons.rvdata2":  ["name", "description"],
    "Armors.rvdata2":   ["name", "description"],
    "Skills.rvdata2":   ["name", "description", "message1", "message2"],
    "States.rvdata2":   ["name", "message1", "message2", "message3", "message4"],
    "Enemies.rvdata2":  ["name"],
}

# Gender detection keywords (same as MV/MZ parser)
_FEMALE_HINTS = re.compile(
    r'彼女|お姉|少女|王女|巫女|メイド|おかあ|女|姫|嬢|娘|母|姉|妹|妻|'
    r'\bactress\b|\bfemale\b|\bgirl\b|\bwoman\b|\bprincess\b|\bqueen\b|\blady\b|\bwitch\b|\bpriestess\b|\bmaid\b',
    re.IGNORECASE
)
_MALE_HINTS = re.compile(
    r'おとうさん|少年|勇者|騎士|王子|息子|男|父|兄|弟|夫|彼|'
    r'\bactor\b|\bmale\b|\bboy\b|\bman\b|\bprince\b|\bking\b|\bknight\b|\bhero\b|\blord\b',
    re.IGNORECASE
)

# Namebox: \N<name> prefix used by some VX Ace plugins
_NAMEBOX_RE = re.compile(r'\\[Nn]<([^>]+)>')
_ACTOR_CODE_RE = re.compile(r'\\[Nn]\[(\d+)\]')


def _str(val) -> str:
    """Coerce RubyString or any string-like value to plain str."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if hasattr(val, "encode"):  # RubyString
        return str(val)
    return ""


def _block_has_text(line_cmds: list) -> bool:
    """True if any 401/405 line in the block has non-empty text.

    Mirrors the load-side rule: a block whose lines are all empty produces
    no entry and does NOT consume a dialogue_index.
    """
    for c in line_cmds:
        lp = _attr(c, "parameters") or []
        if lp and _str(lp[0]):
            return True
    return False


def _keep_blank_slots(line_cmds: list, trans_lines: list) -> list:
    """Put translated lines back where the block's non-empty lines were.

    The parser drops empty 401/405 lines from the text, so a block like
    ``["", "Text"]`` became "Text"; writing lines from the top then moved
    the blank line to the bottom of the message box (even when exporting
    the original text).  When the translation has exactly as many lines as
    the block had non-empty lines, keep the blank lines where they were.
    Otherwise the lines are returned unchanged (filled from the top).
    """
    texts = [_str((_attr(c, "parameters") or [""])[0])
             if (_attr(c, "parameters") or []) else "" for c in line_cmds]
    slots = [k for k, t in enumerate(texts) if t]
    if len(slots) == len(texts) or len(trans_lines) != len(slots):
        return trans_lines
    out = [""] * len(texts)
    for k, slot in enumerate(slots):
        out[slot] = trans_lines[k]
    return out


def _block_text(line_cmds: list, namebox: bool = True) -> tuple[str, str]:
    """``(text, namebox)`` exactly as the parser extracts a 401/405 block.

    Non-empty lines joined by "\\n"; for Show Text a leading ``\\N<name>``
    namebox is split off.  Export compares this with ``entry.original``
    so a translation is only written where its original still is.
    """
    lines = []
    for c in line_cmds:
        lp = _attr(c, "parameters") or []
        if lp:
            s = _str(lp[0])
            if s:
                lines.append(s)
    full = "\n".join(lines)
    nb = ""
    if namebox:
        m = _NAMEBOX_RE.match(full)
        if m:
            nb = m.group(0)
            full = full[m.end():].lstrip("\n")
    return full, nb


def _has_japanese(text: str) -> bool:
    return bool(JAPANESE_RE.search(text))


def _detect_gender(description: str, note: str, nickname: str) -> str:
    all_text = f"{description} {note} {nickname}"
    female = len(_FEMALE_HINTS.findall(all_text))
    male = len(_MALE_HINTS.findall(all_text))
    if female > male:
        return "female"
    if male > female:
        return "male"
    return ""


def _attr(obj, key, default=None):
    """Read an @-prefixed attribute from a Ruby object.

    Strings are cast to plain str (rubymarshal returns RubyString).
    """
    if obj is None:
        return default
    attrs = getattr(obj, "attributes", None)
    if attrs is None:
        return default
    val = attrs.get(f"@{key}", default)
    # rubymarshal returns RubyString — cast to plain str for regex compat
    if val is not None and not isinstance(val, str) and hasattr(val, "encode"):
        return str(val)
    return val


def _set_attr(obj, key, value):
    """Write an @-prefixed attribute on a Ruby object."""
    obj.attributes[f"@{key}"] = value


class RPGMakerAceParser:
    """Parser for RPG Maker VX Ace .rvdata2 data files."""

    def __init__(self):
        self.context_size = 3
        self._require_japanese = True
        # Files the last load_project() could not read, for the UI
        self.load_warnings: list[str] = []
        self._actor_names: dict[int, str] = {}
        self._face_to_actor: dict[tuple, int] = {}

    # ── Public interface (matches MV/MZ parser) ──────────────────────

    @staticmethod
    def is_ace_project(path: str) -> bool:
        """Check if path contains a VX Ace project (Data/*.rvdata2)."""
        data_dir = os.path.join(path, "Data")
        if game_io.interrupted_swap(data_dir):
            # Restore Originals killed mid-swap: still this game (read-only
            # check; _find_data_dir puts the folder back on open / export)
            data_dir += ".rmt-old"
        if not os.path.isdir(data_dir):
            return False
        return any(f.endswith(".rvdata2") for f in os.listdir(data_dir))

    def _should_extract(self, text: str) -> bool:
        if not text or not text.strip():
            return False
        if self._require_japanese:
            return _has_japanese(text)
        return True

    def get_game_title(self, project_dir: str) -> str:
        data_dir = self._find_data_dir(project_dir)
        if not data_dir:
            return ""
        sys_path = os.path.join(data_dir, "System.rvdata2")
        if not os.path.exists(sys_path):
            return ""
        try:
            data = self._read_rvdata2(sys_path)
            return _attr(data, "game_title", "")
        except Exception:
            return ""

    def load_actors_raw(self, project_dir: str) -> list:
        """Load raw actor data for the gender assignment dialog."""
        data_dir = self._find_data_dir(project_dir)
        if not data_dir:
            return []
        path = os.path.join(data_dir, "Actors.rvdata2")
        if not os.path.exists(path):
            return []
        try:
            data = self._read_rvdata2(path)
        except Exception:
            return []
        if not isinstance(data, list):
            return []

        actors = []
        for item in data:
            if item is None:
                continue
            name = (_attr(item, "name") or "").strip()
            if not name:
                continue
            description = (_attr(item, "description") or "").strip()
            nickname = (_attr(item, "nickname") or "").strip()
            note = (_attr(item, "note") or "").strip()
            auto_gender = _detect_gender(description, note, nickname)
            actors.append({
                "id": _attr(item, "id", 0),
                "name": name,
                "nickname": nickname,
                "profile": description,  # VX Ace uses "description" not "profile"
                "auto_gender": auto_gender or "unknown",
            })
        return actors

    def _note_load_failure(self, filename: str, exc: Exception):
        """Record a data file that could not be read (logged + shown)."""
        log.error("Failed to read %s: %s", filename, exc)
        self.load_warnings.append(f"{filename}: {exc}")

    def load_project(self, project_dir: str) -> list:
        """Load all translatable entries from a VX Ace project."""
        data_dir = self._find_data_dir(project_dir)
        if not data_dir:
            raise FileNotFoundError(
                f"No 'Data' folder found in {project_dir}.\n"
                "Please select an RPG Maker VX Ace project folder."
            )
        return self._load_from(data_dir)

    def _load_from(self, data_dir: str) -> list:
        """Parse every entry, reading the .rvdata2 files from *data_dir*."""
        self.load_warnings = []

        self._actor_names = self._load_actor_names(data_dir)
        self._face_to_actor = self._load_face_to_actor(data_dir)

        entries = []
        entries.extend(self._parse_database_files(data_dir))
        entries.extend(self._parse_system(data_dir))
        entries.extend(self._parse_common_events(data_dir))
        entries.extend(self._parse_troops(data_dir))
        entries.extend(self._parse_maps(data_dir))

        # Deduplicate speaker names
        seen_speakers = set()
        deduped = []
        for e in entries:
            if e.field == "speaker_name":
                if e.original in seen_speakers:
                    continue
                seen_speakers.add(e.original)
            deduped.append(e)
        return deduped

    def save_project(self, project_dir: str, entries: list):
        """Write translations back into .rvdata2 files."""
        data_dir = self._find_data_dir(project_dir)
        if not data_dir:
            raise FileNotFoundError(
                f"Could not find Data/ folder in {project_dir}."
            )

        backup_dir = data_dir + "_original"
        manifest = game_io.ExportManifest(backup_dir + ".manifest.json")
        # Backup originals on first export
        if self._backup_data_dir(data_dir):
            manifest.start_with_backup()
        source_dir = backup_dir if os.path.isdir(backup_dir) else data_dir

        # Build lookup: file → [entries]
        by_file: dict[str, list] = {}
        all_files: set[str] = set()
        for e in entries:
            all_files.add(e.file)
            if not (e.translation and e.status in ("translated", "reviewed")):
                continue
            by_file.setdefault(e.file, []).append(e)

        # Only files that hold game text are ever written (Scripts.rvdata2
        # is left to the script patchers, which keep their own backup).
        # Files changed outside the translator (game update, RPG Maker
        # re-save, hand edit) are not written; the backup is never touched.
        self.last_export_notes: list[str] = []
        outside = set(game_io.changed_outside(
            self._backup_pairs(data_dir), manifest))

        problems: list[str] = []
        skipped: dict[str, int] = {}
        for filename in sorted(all_files):
            if filename in outside:
                continue
            file_entries = by_file.get(filename, [])
            source_path = os.path.join(source_dir, filename)
            out_path = os.path.join(data_dir, filename)
            if not os.path.exists(source_path) and os.path.isfile(out_path) \
                    and file_entries and source_path != out_path:
                # The backup has no copy of this file.  Only a file this app
                # provably never wrote is backed up and written; otherwise
                # it may hold an older export's English: not written.
                if not game_io.never_written(filename, manifest):
                    skipped[filename] = len(file_entries)
                    log.warning("%s is not in the backup: not written",
                                filename)
                    continue
                try:
                    data = self._read_rvdata2(out_path)
                    n = self._apply_translations(data, filename, file_entries)
                    if n:
                        skipped[filename] = n
                    game_io.backup_file_once(out_path, source_path)
                    self._write_rvdata2(out_path, data)
                    manifest.record(filename, out_path)
                except Exception as exc:
                    log.error("Failed to export %s: %s", filename, exc)
                    problems.append(f"{filename} ({exc})")
                continue
            if not os.path.exists(source_path):
                if file_entries:
                    problems.append(f"{filename} (file not found, "
                                    f"{len(file_entries)} translations)")
                continue
            try:
                if not file_entries:
                    # No (remaining) translations: put the original back
                    # so reverted lines don't keep stale English.
                    if source_path != out_path:
                        game_io.atomic_copy(source_path, out_path)
                else:
                    data = self._read_rvdata2(source_path)
                    n = self._apply_translations(data, filename, file_entries)
                    if n:
                        skipped[filename] = n
                    self._write_rvdata2(out_path, data)
                manifest.record(filename, out_path)
            except Exception as exc:
                log.error("Failed to export %s: %s", filename, exc)
                problems.append(f"{filename} ({exc})")
        manifest.finish()
        game_io.raise_export_problems(problems, skipped,
                                      outside=sorted(outside & all_files),
                                      manifest=manifest)

    @staticmethod
    def _backup_pairs(data_dir: str) -> list:
        """(name, live, backup) for every data file in either folder.

        Used to find files changed outside the translator.  Scripts.rvdata2
        is left out -- only the script plugins change it, and they keep
        their own backup.
        """
        backup_dir = data_dir + "_original"
        names = set()
        for d in (data_dir, backup_dir):
            if os.path.isdir(d):
                names.update(fn for fn in os.listdir(d)
                             if fn.endswith(".rvdata2")
                             and fn != "Scripts.rvdata2")
        return [(fn, os.path.join(data_dir, fn), os.path.join(backup_dir, fn))
                for fn in sorted(names)]

    def restore_originals(self, project_dir: str):
        """Restore Data_original/ → Data/."""
        data_dir = self._find_data_dir(project_dir)
        if not data_dir:
            raise FileNotFoundError(
                f"No Data/ folder found in {project_dir}")
        backup_dir = data_dir + "_original"
        if not os.path.isdir(backup_dir):
            raise FileNotFoundError(
                f"No backup found at {backup_dir}. Export the game at "
                "least once before restoring.")
        # Copy-then-swap: a failed restore leaves Data/ untouched.
        game_io.restore_tree(backup_dir, data_dir)

    # ── Ruby Marshal I/O ─────────────────────────────────────────────

    @staticmethod
    def _read_rvdata2(path: str):
        with open(path, "rb") as f:
            return ruby_loads(f.read())

    @staticmethod
    def _write_rvdata2(path: str, data):
        # Serialise first, then replace atomically: a serialiser error or
        # crash never leaves a truncated .rvdata2 behind.
        game_io.atomic_write_bytes(path, ruby_writes(data))

    # ── Data dir discovery ───────────────────────────────────────────

    @staticmethod
    def _find_data_dir(project_dir: str) -> Optional[str]:
        for name in ("Data", "data"):
            game_io.recover_interrupted_restore(
                os.path.join(project_dir, name))
        for name in ("Data", "data"):
            d = os.path.join(project_dir, name)
            if os.path.isdir(d):
                return d
        return None

    @staticmethod
    def _backup_data_dir(data_dir: str) -> bool:
        return game_io.backup_tree_once(data_dir, data_dir + "_original")

    # ── Actor lookup helpers ─────────────────────────────────────────

    def _load_actor_names(self, data_dir: str) -> dict[int, str]:
        path = os.path.join(data_dir, "Actors.rvdata2")
        if not os.path.exists(path):
            return {}
        try:
            data = self._read_rvdata2(path)
        except Exception:
            return {}
        names = {}
        if isinstance(data, list):
            for item in data:
                if item is None:
                    continue
                aid = _attr(item, "id", 0)
                name = (_attr(item, "name") or "").strip()
                if aid and name:
                    names[aid] = name
        return names

    def _load_face_to_actor(self, data_dir: str) -> dict[tuple, int]:
        path = os.path.join(data_dir, "Actors.rvdata2")
        if not os.path.exists(path):
            return {}
        try:
            data = self._read_rvdata2(path)
        except Exception:
            return {}
        lookup = {}
        if isinstance(data, list):
            for item in data:
                if item is None:
                    continue
                aid = _attr(item, "id", 0)
                face = _attr(item, "face_name", "")
                idx = _attr(item, "face_index", 0)
                if aid and face:
                    lookup[(face, idx)] = aid
        return lookup

    # ── Database parsing ─────────────────────────────────────────────

    def _parse_database_files(self, data_dir: str) -> list:
        entries = []
        for filename, fields in DATABASE_FILES.items():
            path = os.path.join(data_dir, filename)
            if not os.path.exists(path):
                continue
            try:
                data = self._read_rvdata2(path)
            except Exception as exc:
                self._note_load_failure(os.path.basename(path), exc)
                continue

            if not isinstance(data, list):
                continue

            for item in data:
                if item is None:
                    continue
                item_id = _attr(item, "id", 0)
                for fld in fields:
                    text = _str(_attr(item, fld, ""))
                    if text and self._should_extract(text):
                        entry_id = f"{filename}/{item_id}/{fld}"
                        entries.append(TranslationEntry(
                            id=entry_id,
                            file=filename,
                            field=fld,
                            original=text,
                        ))
        return entries

    def _parse_system(self, data_dir: str) -> list:
        """Parse System.rvdata2 for terms, currency, elements, etc."""
        path = os.path.join(data_dir, "System.rvdata2")
        if not os.path.exists(path):
            return []
        try:
            data = self._read_rvdata2(path)
        except Exception as exc:
            self._note_load_failure("System.rvdata2", exc)
            return []

        entries = []
        filename = "System.rvdata2"

        # Game title
        title = _str(_attr(data, "game_title", ""))
        if title and self._should_extract(title):
            entries.append(TranslationEntry(
                id=f"{filename}/game_title",
                file=filename, field="game_title", original=title,
            ))

        # Currency unit
        currency = _str(_attr(data, "currency_unit", ""))
        if currency and self._should_extract(currency):
            entries.append(TranslationEntry(
                id=f"{filename}/currency_unit",
                file=filename, field="currency_unit", original=currency,
            ))

        # Elements list (index 0 is empty)
        elements = _attr(data, "elements", [])
        for i, elem in enumerate(elements):
            s = _str(elem)
            if s and self._should_extract(s):
                entries.append(TranslationEntry(
                    id=f"{filename}/elements/{i}",
                    file=filename, field="element", original=s,
                ))

        # Skill types, weapon types, armor types
        for list_key, field_name in [
            ("skill_types", "skill_type"),
            ("weapon_types", "weapon_type"),
            ("armor_types", "armor_type"),
        ]:
            items = _attr(data, list_key, [])
            for i, item in enumerate(items):
                s = _str(item)
                if s and self._should_extract(s):
                    entries.append(TranslationEntry(
                        id=f"{filename}/{list_key}/{i}",
                        file=filename, field=field_name, original=s,
                    ))

        # Terms (basic + params + etypes + commands)
        terms = _attr(data, "terms")
        if terms is not None:
            for term_key in ("basic", "params", "etypes", "commands"):
                term_list = _attr(terms, term_key, [])
                for i, term in enumerate(term_list):
                    s = _str(term)
                    if s and self._should_extract(s):
                        entries.append(TranslationEntry(
                            id=f"{filename}/terms/{term_key}/{i}",
                            file=filename, field="term", original=s,
                        ))

        # Switches and variables with JP names (rare but possible)
        for list_key in ("switches", "variables"):
            items = _attr(data, list_key, [])
            for i, item in enumerate(items):
                s = _str(item)
                if s and self._should_extract(s):
                    entries.append(TranslationEntry(
                        id=f"{filename}/{list_key}/{i}",
                        file=filename, field=list_key[:-1], original=s,
                    ))

        return entries

    # ── Event parsing (maps, common events, troops) ──────────────────

    def _parse_maps(self, data_dir: str) -> list:
        entries = []
        for fname in sorted(os.listdir(data_dir)):
            if not fname.startswith("Map") or not fname.endswith(".rvdata2"):
                continue
            if fname == "MapInfos.rvdata2":
                continue
            path = os.path.join(data_dir, fname)
            try:
                data = self._read_rvdata2(path)
            except Exception as exc:
                self._note_load_failure(os.path.basename(path), exc)
                continue

            # Map display name
            display_name = _str(_attr(data, "display_name", ""))
            if display_name and self._should_extract(display_name):
                entries.append(TranslationEntry(
                    id=f"{fname}/display_name",
                    file=fname, field="display_name", original=display_name,
                ))

            events = _attr(data, "events") or {}
            for event_id, event in events.items():
                if event is None:
                    continue
                ev_name = _attr(event, "name", "")
                pages = _attr(event, "pages") or []
                for page_idx, page in enumerate(pages):
                    cmd_list = _attr(page, "list") or []
                    prefix = f"{fname}/Ev{event_id}/p{page_idx}"
                    entries.extend(self._parse_event_commands(
                        cmd_list, fname, prefix))
        return entries

    def _parse_common_events(self, data_dir: str) -> list:
        path = os.path.join(data_dir, "CommonEvents.rvdata2")
        if not os.path.exists(path):
            return []
        try:
            data = self._read_rvdata2(path)
        except Exception as exc:
            self._note_load_failure("CommonEvents.rvdata2", exc)
            return []
        if not isinstance(data, list):
            return []

        entries = []
        for item in data:
            if item is None:
                continue
            ce_id = _attr(item, "id", 0)
            cmd_list = _attr(item, "list") or []
            prefix = f"CommonEvents.rvdata2/CE{ce_id}"
            entries.extend(self._parse_event_commands(
                cmd_list, "CommonEvents.rvdata2", prefix))
        return entries

    def _parse_troops(self, data_dir: str) -> list:
        path = os.path.join(data_dir, "Troops.rvdata2")
        if not os.path.exists(path):
            return []
        try:
            data = self._read_rvdata2(path)
        except Exception as exc:
            self._note_load_failure("Troops.rvdata2", exc)
            return []
        if not isinstance(data, list):
            return []

        entries = []
        for item in data:
            if item is None:
                continue
            troop_id = _attr(item, "id", 0)
            pages = _attr(item, "pages") or []
            for page_idx, page in enumerate(pages):
                cmd_list = _attr(page, "list") or []
                prefix = f"Troops.rvdata2/T{troop_id}/p{page_idx}"
                entries.extend(self._parse_event_commands(
                    cmd_list, "Troops.rvdata2", prefix))
        return entries

    def _parse_event_commands(self, cmd_list: list, filename: str,
                              prefix: str) -> list:
        """Parse a list of RPG::EventCommand objects into TranslationEntry.

        Groups consecutive 401/405 commands into single dialogue blocks,
        exactly like the MV/MZ parser.
        """
        entries = []
        recent_context: deque = deque(maxlen=self.context_size)
        i = 0
        dialogue_index = 0

        while i < len(cmd_list):
            cmd = cmd_list[i]
            code = _attr(cmd, "code", 0)
            params = _attr(cmd, "parameters") or []

            # ── 101 + 401 block: Show Text ─────────────────────────
            if code == CODE_SHOW_TEXT_HEADER:
                speaker = ""
                has_face = False
                # VX Ace 101: [faceName, faceIndex, background, position]
                if len(params) >= 2:
                    face_name = _str(params[0])
                    face_index = params[1] if isinstance(params[1], int) else 0
                    if face_name:
                        has_face = True
                        # Try to resolve face to actor name
                        actor_id = self._face_to_actor.get(
                            (face_name, face_index))
                        if actor_id and actor_id in self._actor_names:
                            speaker = self._actor_names[actor_id]

                # Collect consecutive 401 lines
                lines = []
                i += 1
                while i < len(cmd_list):
                    nc = cmd_list[i]
                    if _attr(nc, "code", 0) != CODE_SHOW_TEXT:
                        break
                    np = _attr(nc, "parameters") or []
                    if np:
                        s = _str(np[0])
                        if s:
                            lines.append(s)
                    i += 1

                if not lines:
                    continue

                full_text = "\n".join(lines)

                # Check for \N<name> namebox prefix
                namebox = ""
                m = _NAMEBOX_RE.match(full_text)
                if m:
                    namebox = m.group(0)
                    inner = m.group(1)
                    # Resolve \n[N] actor references
                    actor_m = _ACTOR_CODE_RE.match(inner)
                    if actor_m:
                        aid = int(actor_m.group(1))
                        if aid in self._actor_names:
                            speaker = self._actor_names[aid]
                    else:
                        speaker = inner
                    full_text = full_text[m.end():].lstrip("\n")

                # Always increment dialogue_index for ID stability on export
                entry_id = f"{prefix}/{dialogue_index}"
                dialogue_index += 1

                if not self._should_extract(full_text):
                    continue

                # Build context
                context_parts = []
                if speaker:
                    context_parts.append(f"[Speaker: {speaker}]")
                for ctx in recent_context:
                    context_parts.append(ctx)
                context = "\n".join(context_parts)

                entries.append(TranslationEntry(
                    id=entry_id,
                    file=filename,
                    field="dialogue",
                    original=full_text,
                    context=context,
                    namebox=namebox,
                    has_face=has_face,
                ))

                # Update context window
                label = f"[{speaker}]: " if speaker else ""
                recent_context.append(f"{label}{full_text[:80]}")
                continue

            # ── 105 + 405 block: Scroll Text ───────────────────────
            if code == CODE_SCROLL_TEXT_HEADER:
                lines = []
                i += 1
                while i < len(cmd_list):
                    nc = cmd_list[i]
                    if _attr(nc, "code", 0) != CODE_SCROLL_TEXT:
                        break
                    np = _attr(nc, "parameters") or []
                    if np:
                        s = _str(np[0])
                        if s:
                            lines.append(s)
                    i += 1

                if not lines:
                    continue
                full_text = "\n".join(lines)
                entry_id = f"{prefix}/{dialogue_index}"
                dialogue_index += 1
                if not self._should_extract(full_text):
                    continue

                entries.append(TranslationEntry(
                    id=entry_id,
                    file=filename,
                    field="dialogue",
                    original=full_text,
                ))
                continue

            # ── 102: Show Choices ──────────────────────────────────
            if code == CODE_SHOW_CHOICES and params:
                choices = params[0] if isinstance(params[0], list) else []
                for ci, choice in enumerate(choices):
                    choice = _str(choice)
                    if choice and self._should_extract(choice):
                        entry_id = f"{prefix}/c{dialogue_index}_{ci}"
                        entries.append(TranslationEntry(
                            id=entry_id,
                            file=filename,
                            field="choice",
                            original=choice,
                        ))
                dialogue_index += 1  # Always increment for ID stability
                i += 1
                continue

            # ── 320/324/325: Change Actor Name/Nickname/Profile ───
            if code in (CODE_CHANGE_NAME, CODE_CHANGE_NICKNAME,
                        CODE_CHANGE_PROFILE):
                entry_id = f"{prefix}/{dialogue_index}"
                dialogue_index += 1  # Always increment
                if len(params) >= 2:
                    text = _str(params[1])
                    if text and self._should_extract(text):
                        field_map = {
                            CODE_CHANGE_NAME: "actor_name",
                            CODE_CHANGE_NICKNAME: "actor_nickname",
                            CODE_CHANGE_PROFILE: "actor_profile",
                        }
                        entries.append(TranslationEntry(
                            id=entry_id,
                            file=filename,
                            field=field_map[code],
                            original=text,
                        ))
                i += 1
                continue

            i += 1

        return entries

    # ── Apply translations back to Ruby data ─────────────────────────

    def _orig_ok(self, current, entry) -> bool:
        """Does the game still hold *entry*'s original at its position?

        Entries are written by position (ID).  After a game update the
        text there may be different: then the game's new text is kept,
        and the skip is counted so the export is reported as incomplete.
        A translation equal to its original changes nothing, so it is
        never counted.
        """
        self.__dict__.setdefault("_visited", set()).add(entry.id)
        if _str(current) == entry.original:
            return True
        if entry.translation != entry.original:
            self._skipped += 1
        return False

    def _apply_translations(self, data, filename: str, entries: list) -> int:
        """Apply translation entries back into a loaded Ruby object tree.

        Returns how many translations were not written because the game
        text at their position no longer matches their original.
        """
        self._skipped = 0
        self._visited: set = set()
        # Build lookup: entry_id → translation
        trans_map = {e.id: e for e in entries
                     if e.translation and e.status in ("translated", "reviewed")}
        if not trans_map:
            return 0

        if filename.startswith("Map") and filename != "MapInfos.rvdata2":
            self._apply_map(data, filename, trans_map)
        elif filename == "CommonEvents.rvdata2":
            self._apply_common_events(data, trans_map)
        elif filename == "Troops.rvdata2":
            self._apply_troops(data, trans_map)
        elif filename == "System.rvdata2":
            self._apply_system(data, filename, trans_map)
        elif filename in DATABASE_FILES:
            self._apply_database(data, filename, trans_map)
        # Entries whose place no longer exists (block, event, row removed
        # by a game update) were never looked at: not written either.
        self._skipped += sum(1 for e in trans_map.values()
                             if e.id not in self._visited
                             and e.translation != e.original)
        return self._skipped

    def _apply_database(self, data, filename: str, trans_map: dict):
        if not isinstance(data, list):
            return
        fields = DATABASE_FILES.get(filename, [])
        for item in data:
            if item is None:
                continue
            item_id = _attr(item, "id", 0)
            for fld in fields:
                eid = f"{filename}/{item_id}/{fld}"
                entry = trans_map.get(eid)
                if entry and self._orig_ok(_attr(item, fld, ""), entry):
                    _set_attr(item, fld, entry.translation)

    def _apply_system(self, data, filename: str, trans_map: dict):
        # Game title / currency
        for fld in ("game_title", "currency_unit"):
            e = trans_map.get(f"{filename}/{fld}")
            if e and self._orig_ok(_attr(data, fld, ""), e):
                _set_attr(data, fld, e.translation)

        def apply_list(items, id_prefix):
            for i in range(len(items)):
                e = trans_map.get(f"{id_prefix}/{i}")
                if e and self._orig_ok(items[i], e):
                    items[i] = e.translation

        # Elements and type lists
        for list_key in ("elements", "skill_types", "weapon_types",
                         "armor_types"):
            apply_list(_attr(data, list_key, []), f"{filename}/{list_key}")

        # Terms
        terms = _attr(data, "terms")
        if terms is not None:
            for term_key in ("basic", "params", "etypes", "commands"):
                apply_list(_attr(terms, term_key, []),
                           f"{filename}/terms/{term_key}")

        # Switches/variables
        for list_key in ("switches", "variables"):
            apply_list(_attr(data, list_key, []), f"{filename}/{list_key}")

    def _apply_map(self, data, filename: str, trans_map: dict):
        # Display name
        e = trans_map.get(f"{filename}/display_name")
        if e and self._orig_ok(_attr(data, "display_name", ""), e):
            _set_attr(data, "display_name", e.translation)

        events = _attr(data, "events") or {}
        for event_id, event in events.items():
            if event is None:
                continue
            pages = _attr(event, "pages") or []
            for page_idx, page in enumerate(pages):
                cmd_list = _attr(page, "list") or []
                prefix = f"{filename}/Ev{event_id}/p{page_idx}"
                self._apply_event_commands(cmd_list, prefix, trans_map)

    def _apply_common_events(self, data, trans_map: dict):
        if not isinstance(data, list):
            return
        for item in data:
            if item is None:
                continue
            ce_id = _attr(item, "id", 0)
            cmd_list = _attr(item, "list") or []
            prefix = f"CommonEvents.rvdata2/CE{ce_id}"
            self._apply_event_commands(cmd_list, prefix, trans_map)

    def _apply_troops(self, data, trans_map: dict):
        if not isinstance(data, list):
            return
        for item in data:
            if item is None:
                continue
            troop_id = _attr(item, "id", 0)
            pages = _attr(item, "pages") or []
            for page_idx, page in enumerate(pages):
                cmd_list = _attr(page, "list") or []
                prefix = f"Troops.rvdata2/T{troop_id}/p{page_idx}"
                self._apply_event_commands(cmd_list, prefix, trans_map)

    def _apply_event_commands(self, cmd_list: list, prefix: str,
                              trans_map: dict):
        """Write translations back into event command parameters.

        Mirrors the parsing logic: groups 401/405 blocks, splits translations
        back into individual commands.
        """
        i = 0
        dialogue_index = 0

        while i < len(cmd_list):
            cmd = cmd_list[i]
            code = _attr(cmd, "code", 0)
            params = _attr(cmd, "parameters") or []

            if code == CODE_SHOW_TEXT_HEADER:
                # Collect 401 block
                start_401 = i + 1
                j = i + 1
                while j < len(cmd_list):
                    if _attr(cmd_list[j], "code", 0) != CODE_SHOW_TEXT:
                        break
                    j += 1

                orig_count = j - start_401
                if not _block_has_text(cmd_list[start_401:j]):
                    i = j
                    continue

                entry_id = f"{prefix}/{dialogue_index}"
                entry = trans_map.get(entry_id)
                dialogue_index += 1
                if entry and entry.translation:
                    # Same text the parser extracts from this block
                    full, namebox = _block_text(cmd_list[start_401:j])
                    if namebox != (entry.namebox or ""):
                        full = None          # speaker prefix changed too
                    if not self._orig_ok(full, entry):
                        i = j
                        continue

                if entry and entry.translation:
                    # Prepend namebox back if it was stripped
                    translated = entry.translation
                    if entry.namebox:
                        translated = entry.namebox + "\n" + translated

                    # Split translation into same number of 401 lines
                    trans_lines = _keep_blank_slots(
                        cmd_list[start_401:j], translated.split("\n"))

                    # Pad or trim to match original line count
                    while len(trans_lines) < orig_count:
                        trans_lines.append("")
                    if len(trans_lines) > orig_count:
                        last = orig_count - 1
                        trans_lines[last] = " ".join(
                            trans_lines[last:])
                        trans_lines = trans_lines[:orig_count]

                    # Write back
                    for idx, line_cmd in enumerate(
                            cmd_list[start_401:start_401 + orig_count]):
                        lp = _attr(line_cmd, "parameters") or []
                        if lp:
                            lp[0] = trans_lines[idx]

                i = j
                continue

            if code == CODE_SCROLL_TEXT_HEADER:
                start_405 = i + 1
                j = i + 1
                while j < len(cmd_list):
                    if _attr(cmd_list[j], "code", 0) != CODE_SCROLL_TEXT:
                        break
                    j += 1

                orig_count = j - start_405
                if not _block_has_text(cmd_list[start_405:j]):
                    i = j
                    continue

                entry_id = f"{prefix}/{dialogue_index}"
                entry = trans_map.get(entry_id)
                dialogue_index += 1
                if entry and entry.translation:
                    full, _nb = _block_text(cmd_list[start_405:j],
                                            namebox=False)
                    if not self._orig_ok(full, entry):
                        i = j
                        continue

                if entry and entry.translation:
                    trans_lines = _keep_blank_slots(
                        cmd_list[start_405:j], entry.translation.split("\n"))
                    while len(trans_lines) < orig_count:
                        trans_lines.append("")
                    if len(trans_lines) > orig_count:
                        last = orig_count - 1
                        trans_lines[last] = " ".join(trans_lines[last:])
                        trans_lines = trans_lines[:orig_count]
                    for idx, line_cmd in enumerate(
                            cmd_list[start_405:start_405 + orig_count]):
                        lp = _attr(line_cmd, "parameters") or []
                        if lp:
                            lp[0] = trans_lines[idx]

                i = j
                continue

            if code == CODE_SHOW_CHOICES and params:
                choices = params[0] if isinstance(params[0], list) else []
                for ci in range(len(choices)):
                    eid = f"{prefix}/c{dialogue_index}_{ci}"
                    entry = trans_map.get(eid)
                    if (entry and entry.translation
                            and self._orig_ok(choices[ci], entry)):
                        choices[ci] = entry.translation
                dialogue_index += 1
                i += 1
                continue

            if code in (CODE_CHANGE_NAME, CODE_CHANGE_NICKNAME,
                        CODE_CHANGE_PROFILE):
                entry_id = f"{prefix}/{dialogue_index}"
                entry = trans_map.get(entry_id)
                if (entry and entry.translation and len(params) >= 2
                        and self._orig_ok(params[1], entry)):
                    params[1] = entry.translation
                dialogue_index += 1
                i += 1
                continue

            i += 1
