"""Saved-state entry-ID migration (ID scheme 1 -> 2).

Why this exists
---------------
ID scheme 2 (see ``project_model.ID_SCHEME``) changed how event entries are
numbered, so a ``_translation_state.json`` / autosave / patch written by an
older version refers to entries by IDs that now mean something else:

* RPG Maker MV/MZ — event IDs (``Map001.json/Ev1(EV001)/p0/dialog_3``) used
  one counter shared by every command kind and only bumped for extracted
  (Japanese) text.  Scheme 2 uses one counter per kind, bumped for every
  command of that kind.  Narration starting with a bare ``\\N[n]`` is also
  no longer stripped into ``namebox`` (only a real speaker label is).
* Ren'Py — dialogue from aliases starting with "pass"/"return" was skipped,
  so later lines in the same label had lower indices.

How it works
------------
The game is re-parsed with the current parser (reading the pristine
``data_original/`` / ``game_original/`` backup when the game was already
exported) and each old event entry is matched to a new one by structure and
text — same event page / label, same kind, same raw text (``namebox +
original``, invariant across both schemes), in order of occurrence.  The
translation, status and context carry over.  Non-event entries (database,
System, plugins, speaker names, Ren'Py defines) have unchanged IDs and are
kept as they are.

For MV/MZ, export locates event text by its original wording (IDs only tag
the kind), so a project that cannot be migrated (game folder missing) still
exports correctly and is simply loaded as-is.  The module is GUI-free so it
can be unit tested; main_window only shows ``MigrationResult.message``.
"""

import logging
import os
import re
import shutil
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field

from .project_model import ID_SCHEME, TranslationProject

log = logging.getLogger(__name__)

# Engines whose entry IDs changed between scheme 1 and 2.
_MV_TYPES = {"rpgmaker_mv", "rpgmaker_mz", "rpgmaker"}
_RENPY_TYPES = {"renpy"}

# MV/MZ event-command entry IDs:  <prefix>/<kind>_<n>[_<i>][/<plugin>/<key>]
_MV_EVENT_ID_RE = re.compile(
    r'^(?P<prefix>.+?)/'
    r'(?P<kind>dialog|scroll|comment|choice|change_name|change_nickname|'
    r'change_profile|plugin_mv|plugin_mz|script_var)'
    r'_(?P<num>\d+)(?P<rest>(?:_\d+)*(?:/.*)?)$', re.S)

# Ren'Py dialogue / choice IDs:  <file>/<label>/<kind>_<n>
_RENPY_EVENT_ID_RE = re.compile(
    r'^(?P<prefix>[^/]+/[^/]+)/(?P<kind>dialog|choice)_(?P<num>\d+)'
    r'(?P<rest>)$')

# Event names inside MV prefixes are display text (often translated in an
# English release) — strip them for structural comparison.
_PREFIX_NAME_RES = (
    re.compile(r'^(CommonEvents\.json/CE\d+)\(.*\)$', re.S),
    re.compile(r'^(Troops\.json/Troop\d+)\(.*\)(/p\d+)$', re.S),
    re.compile(r'^([^/]+\.json/Ev\d+)\(.*\)(/p\d+)$', re.S),
)


def _engine_for(project_type: str):
    if project_type in _MV_TYPES:
        return "mv"
    if project_type in _RENPY_TYPES:
        return "renpy"
    return None


def _canonical_prefix(prefix: str) -> str:
    for rx in _PREFIX_NAME_RES:
        m = rx.match(prefix)
        if m:
            return "".join(m.groups())
    return prefix


def _parse_event_id(entry_id: str, engine: str = "mv"):
    """Return (canonical_prefix, kind, rest) for an event entry ID, else None."""
    rx = _RENPY_EVENT_ID_RE if engine == "renpy" else _MV_EVENT_ID_RE
    m = rx.match(entry_id or "")
    if not m:
        return None
    prefix = m.group("prefix")
    if engine != "renpy":
        prefix = _canonical_prefix(prefix)
    return prefix, m.group("kind"), m.group("num"), m.group("rest")


def is_event_entry(entry_id: str) -> bool:
    """True for MV/MZ event-command entry IDs (numbered per event page)."""
    return _parse_event_id(entry_id, "mv") is not None


def canonical_id(entry_id: str) -> str:
    """Entry ID with event / common-event / troop names removed.

    ``CommonEvents.json/CE3(村長の話)/dialog_1`` and
    ``CommonEvents.json/CE3(Chief's Talk)/dialog_1`` compare equal.
    Non-event IDs are returned unchanged.
    """
    parsed = _parse_event_id(entry_id, "mv")
    if not parsed:
        return entry_id
    prefix, kind, num, rest = parsed
    return f"{prefix}/{kind}_{num}{rest}"


def _group_key(entry, engine):
    """(canonical prefix, kind class) for an event entry, else None."""
    parsed = _parse_event_id(entry.id, engine)
    if not parsed:
        return None
    prefix, kind, _num, rest = parsed
    if kind == "plugin_mz":
        kind = kind + rest          # plugin_mz/<Plugin>/<key>
    return prefix, kind


def _raw_text(entry) -> str:
    """Source text exactly as in the game file (namebox re-attached)."""
    return (entry.namebox or "") + (entry.original or "")


def _has_user_data(entry) -> bool:
    return bool(entry.translation) or entry.status != "untranslated"


def match_entries(old_entries: list, new_entries: list, engine: str):
    """Pair old-scheme event entries with new-scheme event entries.

    Returns ``(pairs, unmatched_old)`` where pairs is a list of
    (old, new).  Pass 1 matches inside the same event page / label and
    kind by raw text in order of occurrence; pass 2 lets entries that
    carry user data match by raw text anywhere in the same file and kind
    (event moved / renumbered between game versions).
    """
    new_by_group = defaultdict(deque)
    new_keys = {}
    for e in new_entries:
        key = _group_key(e, engine)
        if key is None:
            continue
        new_keys[id(e)] = key
        new_by_group[(key, _raw_text(e))].append(e)

    used = set()
    pairs = []
    leftover = []
    for old in old_entries:
        key = _group_key(old, engine)
        if key is None:
            continue
        q = new_by_group.get((key, _raw_text(old)))
        while q and id(q[0]) in used:
            q.popleft()
        if q:
            new = q.popleft()
            used.add(id(new))
            pairs.append((old, new))
        else:
            leftover.append((old, key))

    # Pass 2: same file + kind + text, any event
    by_file = defaultdict(deque)
    for e in new_entries:
        key = new_keys.get(id(e))
        if key is None or id(e) in used:
            continue
        by_file[(e.file, key[1], _raw_text(e))].append(e)
    unmatched = []
    for old, key in leftover:
        if not _has_user_data(old):
            unmatched.append(old)
            continue
        q = by_file.get((old.file, key[1], _raw_text(old)))
        while q and id(q[0]) in used:
            q.popleft()
        if q:
            new = q.popleft()
            used.add(id(new))
            pairs.append((old, new))
        else:
            unmatched.append(old)
    return pairs, unmatched


def transfer_entry(old, new, keep_context: bool = True) -> bool:
    """Copy user data from an old-scheme entry onto its new-scheme twin.

    Returns True when the old entry had a bare ``\\N[n]`` stripped into its
    namebox that the new parser keeps inline (narration subject).  The
    code is then prepended to the translation exactly the way the old
    export re-attached it, so the exported game text is unchanged.
    """
    merged = False
    translation = old.translation
    if (old.namebox and not new.namebox
            and new.original == old.namebox + old.original):
        merged = True
        if translation:
            sep = " " if translation[:1].isalpha() else ""
            translation = old.namebox + sep + translation
    new.translation = translation
    new.status = old.status
    if keep_context and not merged and old.context:
        new.context = old.context
    return merged


def remap_by_structure(old_entries: list, new_entries: list,
                       project_type: str) -> dict:
    """Map ``id(new_entry) -> old_entry`` for old-scheme entries (patches)."""
    engine = _engine_for(project_type)
    if engine is None:
        return {}
    pairs, _ = match_entries(old_entries, new_entries, engine)
    return {id(new): old for old, new in pairs}


# ── Whole-project migration ──────────────────────────────────────────

@dataclass
class MigrationResult:
    status: str = "current"     # current | not_needed | migrated | no_game | error | low_match
    migrated: int = 0           # old entries whose translation/status carried over
    unmatched: int = 0          # translated old entries with no counterpart (kept as orphans)
    new_entries: int = 0        # new entries with no old counterpart (untranslated)
    namebox_merged: int = 0     # narration lines that got their \N[n] subject back
    backup_path: str = ""
    message: str = ""           # user-facing text (empty = nothing to show)
    orphans: list = field(default_factory=list)


def _fresh_event_entries(project, engine, parser):
    """Re-parse the game's event entries with the current parser."""
    path = project.project_path
    if engine == "mv":
        from .rpgmaker_mv import RPGMakerMVParser
        p = parser if isinstance(parser, RPGMakerMVParser) else RPGMakerMVParser()
        fields = {e.field for e in project.entries}
        saved = (p.extract_comments, p.extract_script_strings)
        # Re-create the same optional kinds the old state contained
        p.extract_comments = saved[0] or "comment" in fields
        p.extract_script_strings = saved[1] or "script_variable" in fields
        try:
            return p.load_event_entries(path)
        finally:
            p.extract_comments, p.extract_script_strings = saved
    from .renpy import RenPyParser
    p = parser if isinstance(parser, RenPyParser) else RenPyParser()
    return p.load_project(path, prefer_backup=True)


def migrate_entries(old_entries: list, fresh_entries: list, engine: str):
    """Build the migrated entry list.

    Non-event entries are kept untouched and in place; each file's event
    entries are replaced (at the position of that file's first old event
    entry) by the freshly parsed ones carrying the matched user data.
    Returns ``(entries, result)``.
    """
    old_events = [e for e in old_entries if _group_key(e, engine)]
    new_events = [e for e in fresh_entries if _group_key(e, engine)]
    pairs, unmatched = match_entries(old_events, new_events, engine)

    res = MigrationResult(status="migrated")
    matched_new = set()
    for old, new in pairs:
        matched_new.add(id(new))
        if not _has_user_data(old):
            continue            # nothing to carry — keep the fresh entry
        res.migrated += 1
        if transfer_entry(old, new):
            res.namebox_merged += 1
    res.new_entries = sum(1 for e in new_events
                          if id(e) not in matched_new
                          and e.status == "untranslated")
    res.orphans = [asdict(e) for e in unmatched if _has_user_data(e)]
    res.unmatched = len(res.orphans)

    new_by_file = defaultdict(list)
    for e in new_events:
        new_by_file[e.file].append(e)
    out, emitted = [], set()
    for e in old_entries:
        if _group_key(e, engine) is None:
            out.append(e)
        elif e.file not in emitted:
            emitted.add(e.file)
            out.extend(new_by_file.get(e.file, []))
    for fname, ents in new_by_file.items():
        if fname not in emitted:
            out.extend(ents)
    return out, res


def backup_state_file(state_path: str) -> str:
    """Copy the pre-migration state to ``<name>.pre-v2.json`` (once)."""
    if not state_path or not os.path.isfile(state_path):
        return ""
    root, ext = os.path.splitext(state_path)
    backup = f"{root}.pre-v{ID_SCHEME}{ext or '.json'}"
    if not os.path.exists(backup):
        shutil.copy2(state_path, backup)
    return backup


def migrate_project(project: TranslationProject, parser=None,
                    state_path: str = "") -> MigrationResult:
    """Upgrade a just-loaded project to the current ID scheme in place.

    Safe to call on every load: a current project is left alone.  When the
    game cannot be re-parsed (folder missing, unreadable) or the parse does
    not resemble the saved project, the project is left exactly as loaded
    (old IDs, ``id_scheme`` unchanged) and migration is retried next load.
    """
    if getattr(project, "id_scheme", ID_SCHEME) >= ID_SCHEME:
        return MigrationResult(status="current")
    engine = _engine_for(project.project_type)
    if engine is None:
        project.id_scheme = ID_SCHEME
        return MigrationResult(status="not_needed")

    path = project.project_path
    if not path or not os.path.isdir(path):
        log.info("ID migration skipped: game folder %r not found", path)
        return MigrationResult(status="no_game")

    try:
        fresh = _fresh_event_entries(project, engine, parser)
    except Exception as exc:     # never block loading the save
        log.warning("ID migration skipped: re-parse failed: %s", exc)
        return MigrationResult(
            status="error",
            message=("This project was saved by an older version, but the "
                     f"game files could not be re-read to upgrade it:\n{exc}"
                     "\n\nIt was loaded unchanged."))

    entries, res = migrate_entries(project.entries, fresh, engine)

    # Sanity check: a wrong / already-exported / updated game would orphan
    # most translations — keep the old state rather than lose them.
    old_translated = sum(1 for e in project.entries
                         if _group_key(e, engine) and _has_user_data(e))
    if old_translated and res.unmatched > old_translated / 2:
        log.warning("ID migration skipped: only %d/%d translated event "
                    "entries matched", old_translated - res.unmatched,
                    old_translated)
        return MigrationResult(
            status="low_match", unmatched=res.unmatched,
            message=("This project was saved by an older version, but most of "
                     f"its translated lines ({res.unmatched} of {old_translated}) "
                     "no longer match the game files, so it was not upgraded "
                     "and was loaded unchanged."))

    res.backup_path = backup_state_file(state_path)
    project.entries = entries
    project.migration_orphans = list(project.migration_orphans) + res.orphans
    project.id_scheme = ID_SCHEME
    project._build_index()

    lines = [f"Upgraded project to the new entry-ID format: "
             f"{res.migrated} entries migrated, {res.unmatched} unmatched."]
    if res.unmatched:
        lines.append(f"{res.unmatched} translated lines no longer match any "
                     "game text; they are kept in the save file under "
                     "\"migration_orphans\".")
    if res.namebox_merged:
        lines.append(f"{res.namebox_merged} narration lines starting with an "
                     "actor-name code (\\N[n]) now include it in the text; "
                     "their translations were kept — consider retranslating "
                     "them.")
    if res.new_entries:
        lines.append(f"{res.new_entries} newly detected lines need translation.")
    if res.backup_path:
        lines.append(f"Previous save backed up as "
                     f"{os.path.basename(res.backup_path)}.")
    res.message = "\n\n".join(lines)
    log.info("ID migration: %s", lines[0])
    return res
