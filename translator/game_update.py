"""Merge a game update into a saved project (the resume merge).

When a project is resumed, the game's ORIGINAL text is parsed again (from
the backup an export keeps and refreshes, or from the game before any
export) and merged into the project's entries.

Entry IDs are positional for every engine (command index, line number,
per-kind counter), so a game update that inserts one line renumbers
everything after it.  The merge therefore never trusts an ID alone:

1. same ID (event / common-event names ignored, see ``canonical_id``) and
   same text -> the entry is unchanged;
2. otherwise the same text in the same scope (same page / event / label /
   file and kind, the ID with its last number removed), in order of
   occurrence -> the entry moved: it takes the new ID and keeps its
   translation and status;
3. otherwise, for entries with work in them, the same text anywhere in
   the same file and field -> moved;
4. a fresh entry left over is new -- or "changed" when an old entry with
   work used to sit at its ID;
5. an old entry left over is gone from the game.  It is retired from the
   project, and its translation is kept in ``migration_orphans`` -- but
   only when the fresh parse covered its file and field.  An entry whose
   file could not be read, or whose kind the parse did not produce at all
   (parse options), is kept untouched: "couldn't check" is not "removed".

Nothing with a translation is ever dropped: it is either still in the
project or in ``migration_orphans`` with the reason.
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import asdict

# Below this many translated lines the "most lines changed" guard does not
# apply: a tiny project can legitimately see most of its lines change.
LOW_MATCH_MIN_LINES = 20

_LAST_NUMBER_RE = re.compile(r"\d+(?=\D*$)")


def _canon(entry_id: str) -> str:
    from .state_migration import canonical_id
    return canonical_id(entry_id)


def _scope(canon: str) -> str:
    """The ID without its last number: page / event / label / file + kind."""
    return _LAST_NUMBER_RE.sub("#", canon, count=1)


def _raw(entry) -> str:
    """Text exactly as in the game: the speaker prefix split off by the
    parser (MV/Ace namebox) is re-attached, so a different "speaker
    processing" setting does not make a line look changed."""
    return (entry.namebox or "") + (entry.original or "")


def _has_work(entry) -> bool:
    return bool(entry.translation) or entry.status != "untranslated"


def _worked(entry) -> bool:
    return bool(entry.translation) and entry.status in ("translated", "reviewed")


def merge(project, fresh_entries: list, unreadable_files=(),
          force: bool = False) -> dict:
    """Merge *fresh_entries* into *project* (see the module docstring).

    *unreadable_files* lists files the fresh parse could not read: their
    entries are left exactly as they are.  With *force*, the low-match
    guard is skipped (the user confirmed).

    Returns ``{"status", "new", "changed", "moved", "retired",
    "unchanged", "kept_unchecked"}``.  ``status`` is ``"merged"`` or
    ``"low_match"`` (nothing was changed).
    """
    old_entries = list(project.entries)
    unreadable = set(unreadable_files or ())
    stats = {"status": "merged", "new": 0, "changed": 0, "moved": 0,
             "retired": 0, "unchanged": 0, "kept_unchecked": 0}

    old_canon = {id(o): _canon(o.id) for o in old_entries}
    by_canon = {}
    for o in old_entries:
        by_canon.setdefault(old_canon[id(o)], o)
    fresh_canon = {id(f): _canon(f.id) for f in fresh_entries}

    paired_old: dict[int, object] = {}     # id(fresh) -> old entry
    used_old: set[int] = set()

    # 1. same ID, same text
    for f in fresh_entries:
        o = by_canon.get(fresh_canon[id(f)])
        if o is not None and id(o) not in used_old and _raw(o) == _raw(f):
            paired_old[id(f)] = o
            used_old.add(id(o))

    # 2. same scope + text, in order
    queues = defaultdict(deque)
    for o in old_entries:
        if id(o) not in used_old:
            queues[(_scope(old_canon[id(o)]), _raw(o))].append(o)
    for f in fresh_entries:
        if id(f) in paired_old:
            continue
        q = queues.get((_scope(fresh_canon[id(f)]), _raw(f)))
        while q and id(q[0]) in used_old:
            q.popleft()
        if q:
            o = q.popleft()
            paired_old[id(f)] = o
            used_old.add(id(o))

    # 3. entries with work: same file + field + text, anywhere in the file
    queues = defaultdict(deque)
    for o in old_entries:
        if id(o) not in used_old and _has_work(o):
            queues[(o.file, o.field, _raw(o))].append(o)
    for f in fresh_entries:
        if id(f) in paired_old:
            continue
        q = queues.get((f.file, f.field, _raw(f)))
        while q and id(q[0]) in used_old:
            q.popleft()
        if q:
            o = q.popleft()
            paired_old[id(f)] = o
            used_old.add(id(o))

    # What the fresh parse covered: (file, field) pairs it produced
    covered = {(f.file, f.field) for f in fresh_entries}
    covered_files = {f.file for f in fresh_entries}

    def checkable(o) -> bool:
        return (o.file not in unreadable and o.file in covered_files
                and (o.file, o.field) in covered)

    lost_work = [o for o in old_entries
                 if id(o) not in used_old and _worked(o) and checkable(o)]
    with_work = sum(1 for o in old_entries if _worked(o))
    if (not force and with_work >= LOW_MATCH_MIN_LINES
            and len(lost_work) > with_work / 2):
        stats["status"] = "low_match"
        stats["changed"] = len(lost_work)
        return stats

    # Build the merged list in the game's (fresh) order
    out = []
    replaced: set[int] = set()       # old entries whose slot got new text
    for f in fresh_entries:
        o = paired_old.get(id(f))
        if o is not None:
            if o.id == f.id:
                stats["unchanged"] += 1
            else:
                stats["moved"] += 1
                o.id = f.id
            o.file = f.file
            o.context = f.context or o.context     # neighbours may have changed
            if o.status == "untranslated" and f.status == "skipped":
                o.status = "skipped"
            out.append(o)
            continue
        prev = by_canon.get(fresh_canon[id(f)])
        if prev is not None and id(prev) not in used_old \
                and id(prev) not in replaced:
            # Same slot, different text: the fresh entry takes its place
            replaced.add(id(prev))
            if _has_work(prev):
                stats["changed"] += 1
                if prev.translation:
                    project.migration_orphans.append(
                        {**asdict(prev),
                         "reason": "original changed (game update)"})
                # The user's own "skip" for this slot still applies
                if prev.status == "skipped" and not prev.translation:
                    f.status = "skipped"
            else:
                stats["new"] += 1
        else:
            stats["new"] += 1
        out.append(f)

    # Old entries the fresh parse did not produce
    kept = []
    for o in old_entries:
        if id(o) in used_old or id(o) in replaced:
            continue
        if not checkable(o):
            kept.append(o)                  # couldn't check: leave as is
            stats["kept_unchecked"] += 1
            continue
        if _has_work(o):
            stats["retired"] += 1
            if o.translation:
                project.migration_orphans.append(
                    {**asdict(o), "reason": "line no longer in the game "
                                            "(game update)"})
    project.entries = out + kept
    project._build_index()
    return stats
