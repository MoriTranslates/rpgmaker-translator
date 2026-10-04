"""Data model for translation entries and project state."""

import json
import os
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

# Version of the entry-ID numbering written into saved states / patches.
#   1 (or missing) — legacy: MV/MZ event IDs used one counter shared by all
#                    command kinds, bumped only for extracted text; Ren'Py
#                    skipped dialogue from aliases starting "pass"/"return".
#   2              — per-kind MV/MZ counters independent of extraction,
#                    narrower bare \N[n] namebox detection, Ren'Py fix.
# Older states are remapped on load by translator/state_migration.py.
ID_SCHEME = 2


@dataclass
class TranslationEntry:
    """A single translatable text entry from an RPG Maker project."""
    id: str                # Unique key e.g. "Actors/1/name", "Map001/Event3/page0/dialog_5"
    file: str              # Source filename e.g. "Actors.json", "Map001.json"
    field: str             # Field path e.g. "name", "description", "dialog"
    original: str          # Original Japanese text
    translation: str = ""  # English translation (empty until translated)
    status: str = "untranslated"  # "untranslated" | "translated" | "reviewed" | "skipped"
    context: str = ""      # Surrounding text for LLM context
    namebox: str = ""      # \N<name> prefix stripped during parse, restored on export
    has_face: bool = False # True when 101 header has a face graphic (narrower text area)


def _read_id_scheme(data: dict) -> int:
    """ID scheme stored in a state / patch dict (missing or bad = 1)."""
    try:
        return int(data.get("id_scheme", 1))
    except (TypeError, ValueError):
        return 1


@dataclass
class TranslationProject:
    """Holds all translation entries for an RPG Maker project."""
    project_path: str = ""
    project_type: str = "rpgmaker_mv"  # "rpgmaker_mv" | "rpgmaker_mz" | "rpgmaker_ace" | "rpgmaker_2k" | "tyranoscript" | "srpgstudio" | "renpy"
    entries: list = field(default_factory=list)
    glossary: dict = field(default_factory=dict)  # JP -> EN forced mappings
    actor_genders: dict = field(default_factory=dict)  # actor_id -> gender
    # Entry-ID scheme of ``entries`` (see ID_SCHEME).  Fresh parses are
    # current; load_state() reports what the file was written with.
    id_scheme: int = ID_SCHEME
    # Translated entries from a pre-migration state that no longer match
    # any entry of the game (kept as dicts so nothing is silently lost).
    migration_orphans: list = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def translated_count(self) -> int:
        return sum(1 for e in self.entries if e.status in ("translated", "reviewed"))

    @property
    def reviewed_count(self) -> int:
        return sum(1 for e in self.entries if e.status == "reviewed")

    @property
    def untranslated_count(self) -> int:
        return sum(1 for e in self.entries if e.status == "untranslated")

    def _build_index(self):
        """Build internal lookup dicts from the entries list."""
        self._by_file = defaultdict(list)
        self._by_id = {}
        for e in self.entries:
            self._by_file[e.file].append(e)
            self._by_id[e.id] = e
        # Remember what the index was built from so accessors can detect
        # entries being appended/extended or the list being replaced.
        self._index_src = self.entries
        self._index_len = len(self.entries)

    def _ensure_index(self):
        """Rebuild the lookup index if missing or stale (cheap guard)."""
        if (not hasattr(self, "_by_id")
                or getattr(self, "_index_src", None) is not self.entries
                or getattr(self, "_index_len", -1) != len(self.entries)):
            self._build_index()

    def get_entries_for_file(self, filename: str) -> list:
        """Return entries belonging to a specific file."""
        self._ensure_index()
        return self._by_file.get(filename, [])

    def get_files(self) -> list:
        """Return sorted unique filenames."""
        self._ensure_index()
        return sorted(self._by_file.keys())

    def get_entry_by_id(self, entry_id: str) -> Optional[TranslationEntry]:
        """Find an entry by its unique ID."""
        self._ensure_index()
        return self._by_id.get(entry_id)

    def search(self, query: str) -> list:
        """Search entries by original or translation text."""
        q = query.lower()
        return [e for e in self.entries if q in e.original.lower() or q in e.translation.lower()]

    def save_state(self, path: str):
        """Save project state to a JSON file for resume support."""
        data = {
            "project_path": self.project_path,
            "project_type": self.project_type,
            "entries": [asdict(e) for e in self.entries],
            "glossary": self.glossary,
            "actor_genders": self.actor_genders,
            "id_scheme": self.id_scheme,
        }
        if self.migration_orphans:
            data["migration_orphans"] = self.migration_orphans
        dir_name = os.path.dirname(path) if os.path.dirname(path) else "."
        os.makedirs(dir_name, exist_ok=True)
        # Atomic write: write to temp file then rename to prevent corruption
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        # os.replace is atomic on the same filesystem
        os.replace(tmp_path, path)

    @classmethod
    def load_state(cls, path: str) -> "TranslationProject":
        """Load project state from a saved JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        project = cls(
            project_path=data.get("project_path", ""),
            project_type=data.get("project_type", "rpgmaker_mv"),
        )
        # Filter to known fields — forward-compatible with newer save files
        known = {f.name for f in TranslationEntry.__dataclass_fields__.values()}
        project.entries = [
            TranslationEntry(**{k: v for k, v in e.items() if k in known})
            for e in data.get("entries", [])
        ]
        project.glossary = data.get("glossary", {})
        # JSON converts int keys to strings — convert back to int
        raw_genders = data.get("actor_genders", {})
        actor_genders = {}
        for k, v in raw_genders.items():
            try:
                actor_genders[int(k)] = v
            except (ValueError, TypeError):
                pass  # Skip malformed keys (e.g. manually edited save files)
        project.actor_genders = actor_genders
        # Missing key = written before ID schemes existed (legacy scheme 1)
        project.id_scheme = _read_id_scheme(data)
        orphans = data.get("migration_orphans", [])
        project.migration_orphans = orphans if isinstance(orphans, list) else []
        project._build_index()
        return project

    def import_translations(self, old_project: "TranslationProject") -> dict:
        """Import translations from an older version of the same project.

        Matching strategy:
        1. Exact ID match — same entry position in the game files
        2. Original text match — catches entries that moved location

        Only imports entries that have a translation in the old project
        and are currently untranslated in this project.

        Returns:
            Dict with stats: {"by_id": int, "by_text": int, "skipped": int, "new": int}
        """
        self._ensure_index()

        old_by_id = {}
        old_by_text = defaultdict(list)
        for e in old_project.entries:
            if e.translation and e.status in ("translated", "reviewed"):
                old_by_id[e.id] = e
                old_by_text[e.original].append(e)

        stats = {"by_id": 0, "by_text": 0, "skipped": 0, "new": 0}

        # Old states / patches written with an older ID scheme: their event
        # IDs point at different entries now.  Remap them structurally
        # (event + kind + text + order) onto this project's entries.
        remap = {}
        if getattr(old_project, "id_scheme", ID_SCHEME) < ID_SCHEME:
            from .state_migration import remap_by_structure
            remap = remap_by_structure(
                list(old_by_id.values()), self.entries, self.project_type)

        for entry in self.entries:
            if entry.status != "untranslated":
                stats["skipped"] += 1
                continue

            # Strategy 0: structural remap of an old-scheme ID
            old = remap.get(id(entry))
            if old is not None:
                from .state_migration import transfer_entry
                transfer_entry(old, entry, keep_context=False)
                stats["by_id"] += 1
                continue

            # Strategy 1: exact ID match
            old = old_by_id.get(entry.id)
            if old and old.original == entry.original:
                entry.translation = old.translation
                entry.status = old.status
                stats["by_id"] += 1
                continue

            # Strategy 2: same original text (first match)
            candidates = old_by_text.get(entry.original, [])
            if candidates:
                entry.translation = candidates[0].translation
                entry.status = candidates[0].status
                stats["by_text"] += 1
                continue

            stats["new"] += 1

        return stats

    def import_from_game_folder(self, donor_entries: list,
                               swap: bool = False,
                               text_map: dict = None) -> dict:
        """Import translations from an already-translated game folder.

        The donor entries come from parsing a translated game with
        ``RPGMakerMVParser.load_project_raw()``.  Their ``original`` field
        contains the translated text (since the game files are already in
        the target language).

        Uses a two-tier matching strategy:
        1. ``text_map`` (structural alignment) — matches dialogue blocks by
           event structure rather than sequential counter IDs, handling
           cross-version shifts when developers add/remove content.
        2. ID matching — fallback for database entries (Actors, Items, etc.)
           whose IDs are stable across versions.

        Args:
            donor_entries: Entries parsed from the donor game folder.
            swap: If True, donor text becomes the new ``original`` (JP)
                and the project's current ``original`` becomes the
                ``translation`` (EN).  Use when the project was opened
                from the translated game and the donor is the JP original.
            text_map: Optional dict mapping project original text to donor
                translated text, built by structural alignment of raw JSON
                command lists.  Takes priority over ID matching.

        Returns:
            Dict with stats: {"imported": int, "by_text": int,
                              "identical": int, "skipped": int, "new": int}
        """
        self._ensure_index()
        from .state_migration import canonical_id, is_event_entry

        # Event IDs embed event / common-event / troop names, which an
        # English release often translates — match on the name-free form.
        donor_by_id = {canonical_id(e.id): e.original for e in donor_entries}
        # A project still on an old ID scheme (migration skipped) numbers
        # event entries differently from the donor parse: never ID-match
        # those, it would attach English to the wrong line.
        legacy_ids = getattr(self, "id_scheme", ID_SCHEME) < ID_SCHEME

        stats = {"imported": 0, "by_text": 0, "identical": 0,
                 "skipped": 0, "new": 0}

        for entry in self.entries:
            if entry.status != "untranslated":
                stats["skipped"] += 1
                continue

            # Strategy 1: structural text_map (cross-version safe)
            if text_map and entry.original in text_map:
                donor_text = text_map[entry.original]
                if swap:
                    entry.translation = entry.original
                    entry.original = donor_text
                else:
                    entry.translation = donor_text
                entry.status = "translated"
                stats["by_text"] += 1
                continue

            # Strategy 2: ID matching (stable for database entries)
            if legacy_ids and is_event_entry(entry.id):
                donor_text = None
            else:
                donor_text = donor_by_id.get(canonical_id(entry.id))
            if donor_text is None:
                stats["new"] += 1
                continue

            # Same text = untranslated in donor game (or unchanged)
            if donor_text == entry.original:
                stats["identical"] += 1
                continue

            if swap:
                # Donor = JP original, project's current original = EN translation
                entry.translation = entry.original
                entry.original = donor_text
            else:
                # Normal: donor text = translation
                entry.translation = donor_text
            entry.status = "translated"
            stats["imported"] += 1

        return stats

    def stats_for_file(self, filename: str) -> tuple:
        """Return (translated_count, total_count) for a file."""
        file_entries = self.get_entries_for_file(filename)
        translated = sum(1 for e in file_entries if e.status in ("translated", "reviewed"))
        return translated, len(file_entries)

    # ── Patch export / import ────────────────────────────────────

    def export_patch(self, zip_path: str, game_title: str = "",
                     patch_version: str = "1.0"):
        """Export translated entries as a distributable patch zip.

        Contains only translation mappings (no game data), so it's safe
        to distribute without copyright concerns.  Recipients open the
        original game in the translator, apply the patch, then export.
        """
        translated = [e for e in self.entries
                      if e.status in ("translated", "reviewed")]
        reviewed = sum(1 for e in translated if e.status == "reviewed")

        # patch.json — translation data only (no project_path)
        patch_data = {
            "entries": [asdict(e) for e in translated],
            "glossary": self.glossary,
            "actor_genders": self.actor_genders,
            "id_scheme": self.id_scheme,
        }

        # metadata.json — human-readable info
        metadata = {
            "game_title": game_title,
            "patch_version": patch_version,
            "created": date.today().isoformat(),
            "total_entries": self.total,
            "translated": len(translated),
            "reviewed": reviewed,
            "tool": "RPG Maker Translator",
        }

        # README.txt — instructions
        readme = (
            f"Translation Patch — {game_title or 'RPG Maker Game'}\n"
            f"{'=' * 50}\n\n"
            f"Version: {patch_version}\n"
            f"Created: {date.today().isoformat()}\n"
            f"Entries: {len(translated)} translated"
            f" ({reviewed} reviewed)\n\n"
            "HOW TO APPLY:\n"
            "1. Install RPG Maker Translator\n"
            "2. Open the ORIGINAL (untranslated) game via\n"
            "   Project > Open Project\n"
            "3. Go to Game > Apply Translation Patch\n"
            "4. Select this zip file\n"
            "5. Review the imported translations\n"
            "6. Export to game via Game > Export to Game\n\n"
            "This patch contains ONLY translation data.\n"
            "You must own the original game to use it.\n"
        )

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("patch.json",
                        json.dumps(patch_data, ensure_ascii=False, indent=2))
            zf.writestr("metadata.json",
                        json.dumps(metadata, ensure_ascii=False, indent=2))
            zf.writestr("README.txt", readme)

    @classmethod
    def import_patch(cls, zip_path: str) -> "TranslationProject":
        """Load a translation patch from a zip file.

        Returns a TranslationProject that can be passed to
        import_translations() on the target project.
        """
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "patch.json" not in zf.namelist():
                raise ValueError(
                    "This zip file is not a translation patch.\n"
                    "Expected patch.json inside the archive."
                )
            patch_raw = zf.read("patch.json")
            data = json.loads(patch_raw)

            # Read metadata if present (for display purposes)
            metadata = {}
            if "metadata.json" in zf.namelist():
                metadata = json.loads(zf.read("metadata.json"))

        project = cls()
        known = {f.name for f in TranslationEntry.__dataclass_fields__.values()}
        project.entries = [
            TranslationEntry(**{k: v for k, v in e.items() if k in known})
            for e in data.get("entries", [])
        ]
        project.glossary = data.get("glossary", {})
        raw_genders = data.get("actor_genders", {})
        actor_genders = {}
        for k, v in raw_genders.items():
            try:
                actor_genders[int(k)] = v
            except (ValueError, TypeError):
                pass
        project.actor_genders = actor_genders
        # Patches made before ID schemes existed carry legacy IDs —
        # import_translations() remaps them onto the current scheme.
        project.id_scheme = _read_id_scheme(data)
        project._build_index()
        # Stash metadata for the caller to display
        project._patch_metadata = metadata
        return project
