"""After an RPG Maker MV/MZ export: does every picture and sound still exist?

Event commands name their assets by file name (Show Picture, Play BGM,
picture-choice options like ``C_はい|350|200`` ...).  If a translation ever
replaces one of those names, the game stops with "Failed to load: ...".
This module lists the names the exported data points at that have no file
in the game, and separates the ones the export introduced from ones the
original game already had (games ship with dead references in unused
events; those are not our fault and must not alarm the user).

Read-only: it never changes a game file.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Event command code -> list of (parameter getter, asset folder).
# A getter returns the asset name from the command's parameters, or "".


def _p(i):
    return lambda p: p[i] if len(p) > i and isinstance(p[i], str) else ""


def _audio(i):
    def get(p):
        if len(p) > i and isinstance(p[i], dict):
            name = p[i].get("name", "")
            return name if isinstance(name, str) else ""
        return ""
    return get


_REFS = {
    231: [(_p(1), "img/pictures")],            # Show Picture
    241: [(_audio(0), "audio/bgm")],           # Play BGM
    245: [(_audio(0), "audio/bgs")],           # Play BGS
    249: [(_audio(0), "audio/me")],            # Play ME
    250: [(_audio(0), "audio/se")],            # Play SE
    132: [(_audio(0), "audio/bgm")],           # Change Battle BGM
    133: [(_audio(0), "audio/me")],            # Change Victory ME
    139: [(_audio(0), "audio/me")],            # Change Defeat ME
    140: [(_audio(1), "audio/bgm")],           # Change Vehicle BGM
    261: [(_p(0), "movies")],                  # Play Movie
    283: [(_p(0), "img/battlebacks1"), (_p(1), "img/battlebacks2")],
    284: [(_p(0), "img/parallaxes")],          # Change Parallax
    322: [(_p(1), "img/faces"), (_p(3), "img/characters"),
          (_p(5), "img/sv_actors")],           # Change Actor Images
}

# Picture-choice option: "<image name>|<number>[|<number>...]"
_PICTURE_CHOICE_RE = re.compile(r"^([^|]+)\|-?\d+(?:\|-?\d+)*\s*$")


@dataclass(frozen=True)
class MissingAsset:
    file: str        # data file, e.g. "Map003.json"
    folder: str      # asset folder, e.g. "img/pictures" ("img" = any img folder)
    name: str        # the name the command asks for

    def describe(self) -> str:
        return f"{self.folder}/{self.name} (in {self.file})"


def _stems(root: str, folder: str) -> frozenset:
    """File names without extension under root/folder.  Encrypted copies
    (``.rpgmvp``, ``.png_``, ``.rpgmvo``, ``.ogg_`` ...) count, since the game
    loads those under the same name."""
    d = os.path.join(root, folder)
    if not os.path.isdir(d):
        return frozenset()
    return frozenset(os.path.splitext(n)[0] for n in os.listdir(d))


def _all_img_stems(root: str) -> frozenset:
    img = os.path.join(root, "img")
    return frozenset(os.path.splitext(n)[0]
                     for _r, _d, files in os.walk(img) for n in files)


def _command_lists(data, filename: str):
    """Yield every event command list in one data file."""
    if filename.startswith("Map") and isinstance(data, dict):
        for ev in data.get("events") or []:
            if isinstance(ev, dict):
                for page in ev.get("pages") or []:
                    if isinstance(page, dict) and isinstance(page.get("list"), list):
                        yield page["list"]
    elif filename == "CommonEvents.json" and isinstance(data, list):
        for ce in data:
            if isinstance(ce, dict) and isinstance(ce.get("list"), list):
                yield ce["list"]
    elif filename == "Troops.json" and isinstance(data, list):
        for tr in data:
            if isinstance(tr, dict):
                for page in tr.get("pages") or []:
                    if isinstance(page, dict) and isinstance(page.get("list"), list):
                        yield page["list"]


def find_missing(data_dir: str, content_root: str) -> set[MissingAsset]:
    """Every asset name the data in *data_dir* uses that has no file.

    Files that cannot be read are skipped (logged): this is a warning aid,
    the export itself already verified what it wrote.
    """
    folders = {f for refs in _REFS.values() for _g, f in refs}
    stems = {f: _stems(content_root, f) for f in folders}
    img_any = _all_img_stems(content_root)
    missing: set[MissingAsset] = set()
    if not os.path.isdir(data_dir):
        return missing
    for filename in sorted(os.listdir(data_dir)):
        if not filename.endswith(".json"):
            continue
        if not (filename.startswith("Map") or filename in (
                "CommonEvents.json", "Troops.json")):
            continue
        try:
            with open(os.path.join(data_dir, filename), encoding="utf-8-sig") as f:
                data = json.load(f)
        except (OSError, ValueError) as exc:
            # Unreadable file: nothing to check in it; the export reports
            # its own write problems, so this only narrows the warning.
            log.warning("Asset check skipped %s: %s", filename, exc)
            continue
        for cmds in _command_lists(data, filename):
            for cmd in cmds:
                if not isinstance(cmd, dict):
                    continue
                code = cmd.get("code")
                params = cmd.get("parameters") or []
                for get, folder in _REFS.get(code, ()):
                    name = get(params)
                    if name and name not in stems[folder]:
                        missing.add(MissingAsset(filename, folder, name))
                if code == 102 and params and isinstance(params[0], list):
                    for opt in params[0]:
                        m = _PICTURE_CHOICE_RE.match(opt) if isinstance(opt, str) else None
                        if m and m.group(1).strip() not in img_any:
                            missing.add(MissingAsset(filename, "img",
                                                     m.group(1).strip()))
    return missing


def check_export(content_root: str, backup_dir_name: str = "data_original"
                 ) -> tuple[list[MissingAsset], int]:
    """Compare exported data/ with the untouched backup.

    Returns (new, preexisting_count): *new* are missing assets the export
    introduced (the backup's data did not point at them), sorted for
    display; *preexisting_count* is how many dead references the original
    game already had.  Without a backup every missing asset counts as new.
    """
    data_dir = os.path.join(content_root, "data")
    live = find_missing(data_dir, content_root)
    backup = os.path.join(content_root, backup_dir_name)
    if os.path.isdir(backup):
        before = find_missing(backup, content_root)
        names_before = {(m.folder, m.name) for m in before}
        new = [m for m in live if (m.folder, m.name) not in names_before]
        pre = len(live) - len(new)
    else:
        new, pre = list(live), 0
    new.sort(key=lambda m: (m.file, m.folder, m.name))
    return new, pre


def export_note(content_root: str, limit: int = 10) -> str:
    """User-facing note for the export result, or "" when all is well."""
    new, pre = check_export(content_root)
    if not new:
        if pre:
            log.info("Asset check: %d missing file(s) already missing in the "
                     "original game", pre)
        return ""
    lines = [m.describe() for m in new[:limit]]
    more = f"\n…and {len(new) - limit} more (see the log)." if len(new) > limit else ""
    for m in new:
        log.warning("Export points at a missing file: %s", m.describe())
    return ("Warning: the translated game points at files that don't exist, "
            "so it will stop with \"Failed to load\" when it reaches them. "
            "A translation probably replaced a file name. Find these names "
            "in the table, set the entry back to its original (or to "
            "Skipped) and export again:\n  " + "\n  ".join(lines) + more)
