"""Entry-ID scheme 2: stable MV numbering, narrower \\N[n] namebox
detection, Ren'Py skip-regex fix, and migration of legacy saved states."""

import copy
import json
import os
import shutil

import pytest

from conftest import FAKE_MV_GAME


# ── Helpers ──────────────────────────────────────────────────────────

def cmd(code, params, indent=0):
    return {"code": code, "indent": indent, "parameters": params}


def header(face="", speaker=None):
    p = [face, 0, 0, 2]
    if speaker is not None:
        p.append(speaker)
    return cmd(101, p)


def page(cmds):
    return {"conditions": {}, "directionFix": False, "image": {},
            "list": cmds + [cmd(0, [])], "moveType": 0, "priorityType": 0,
            "trigger": 0}


def map_json(events):
    return {"displayName": "", "events": [None] + events,
            "width": 10, "height": 10, "data": []}


# Map002, event 1 page 0 — every kind that used to shift the shared counter.
# (Japanese text, English-release text) per translatable slot.
def _map002_list(lang):
    jp = lang == "jp"
    return [
        # Non-Japanese first choice: old scheme counted it only in English
        cmd(102, [["OK", "いいえ" if jp else "No"], 1, 0, 2, 0]),
        cmd(402, [0, "OK"]), cmd(0, [], 1),
        cmd(402, [1, "いいえ" if jp else "No"]), cmd(0, [], 1),
        cmd(404, []),
        # Whitelisted plugin command: extracted only when Japanese
        cmd(356, ["D_TEXT 宝箱 24" if jp else "D_TEXT Chest 24"]),
        # Narration whose subject is a bare actor code — NOT a namebox
        header(),
        cmd(401, ["\\N[1]は剣を手に入れた！" if jp
                  else "\\N[1] obtained the sword!"]),
        # Bare actor code + 「 — a speaker label
        header(),
        cmd(401, ["\\N[1]「やった！」" if jp else "\\N[1]\"Yay!\""]),
        # Bare actor code alone on line 1 — a speaker label
        header(),
        cmd(401, ["\\N[1]"]),
        cmd(401, ["行くぞ。" if jp else "Let's go."]),
        # Plain dialogue after everything
        header(),
        cmd(401, ["最後の台詞。" if jp else "The last line."]),
    ]


def _write_map002(game_dir, lang):
    ev = {"id": 1, "name": "宝箱" if lang == "jp" else "Chest",
          "note": "", "x": 0, "y": 0,
          "pages": [page(_map002_list(lang))]}
    with open(os.path.join(game_dir, "data", "Map002.json"), "w",
              encoding="utf-8") as f:
        json.dump(map_json([ev]), f, ensure_ascii=False)


def _englishify_common_events(game_dir):
    path = os.path.join(game_dir, "data", "CommonEvents.json")
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    ce = data[1]
    ce["name"] = "Chief's Talk"          # event names get translated too
    ce["list"][0]["parameters"][4] = "Chief"
    ce["list"][1]["parameters"][0] = "Welcome, young one."
    ce["list"][2]["parameters"][0] = "Protect the village."
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


@pytest.fixture
def jp_game(tmp_path):
    dst = tmp_path / "jp"
    shutil.copytree(FAKE_MV_GAME, dst)
    _write_map002(str(dst), "jp")
    return str(dst)


@pytest.fixture
def en_game(tmp_path):
    dst = tmp_path / "en"
    shutil.copytree(FAKE_MV_GAME, dst)
    _write_map002(str(dst), "en")
    _englishify_common_events(str(dst))
    return str(dst)


def _parser():
    from translator.rpgmaker_mv import RPGMakerMVParser
    return RPGMakerMVParser()


def _read_list(game_dir, mapfile="Map002.json"):
    with open(os.path.join(game_dir, "data", mapfile), encoding="utf-8") as f:
        return json.load(f)["events"][1]["pages"][0]["list"]


M2 = "Map002.json/Ev1(宝箱)/p0"


# ── Bug 1: numbering independent of extraction ───────────────────────

def test_event_ids_stable_between_jp_and_english_release(jp_game, en_game):
    p = _parser()
    jp = {e.id: e for e in p.load_project(jp_game)}
    en = {e.id: e for e in p.load_project_raw(en_game)}

    assert jp[f"{M2}/choice_1_1"].original == "いいえ"
    assert jp[f"{M2}/plugin_mv_1"].original == "宝箱"
    assert jp[f"{M2}/dialog_1"].original == "\\N[1]は剣を手に入れた！"
    assert jp[f"{M2}/dialog_4"].original == "最後の台詞。"

    from translator.state_migration import canonical_id
    en_canon = {canonical_id(i): e for i, e in en.items()}
    # Every Japanese event entry has a same-numbered English twin
    pairs = {
        f"{M2}/choice_1_1": "No",
        f"{M2}/dialog_1": "\\N[1] obtained the sword!",
        f"{M2}/dialog_2": "\\N[1]\"Yay!\"",
        f"{M2}/dialog_3": "\\N[1]\nLet's go.",
        f"{M2}/dialog_4": "The last line.",
        "CommonEvents.json/CE1(村長の話)/dialog_1":
            "Welcome, young one.\nProtect the village.",
    }
    for jp_id, en_text in pairs.items():
        twin = en_canon[canonical_id(jp_id)]
        assert twin.namebox + twin.original == en_text, jp_id


def test_import_from_game_folder_attaches_right_lines(jp_game, en_game):
    from translator.project_model import TranslationProject
    p = _parser()
    proj = TranslationProject(entries=p.load_project(jp_game))
    proj.import_from_game_folder(p.load_project_raw(en_game))
    by_id = {e.id: e for e in proj.entries}
    assert by_id[f"{M2}/choice_1_1"].translation == "No"
    assert by_id[f"{M2}/dialog_4"].translation == "The last line."
    # Common event renamed in the English release still lines up
    assert by_id["CommonEvents.json/CE1(村長の話)/dialog_1"].translation == \
        "Welcome, young one.\nProtect the village."


# ── Bug 2: bare \N[n] namebox detection ──────────────────────────────

def test_bare_actor_code_narration_not_stripped(jp_game):
    by_id = {e.id: e for e in _parser().load_project(jp_game)}
    narr = by_id[f"{M2}/dialog_1"]
    assert narr.namebox == ""
    assert narr.original == "\\N[1]は剣を手に入れた！"
    assert "[Speaker:" not in narr.context

    quoted = by_id[f"{M2}/dialog_2"]
    assert (quoted.namebox, quoted.original) == ("\\N[1]", "「やった！」")
    assert quoted.context.startswith("[Speaker: アリス]")

    alone = by_id[f"{M2}/dialog_3"]
    assert (alone.namebox, alone.original) == ("\\N[1]", "\n行くぞ。")


def test_bare_actor_code_colon_is_namebox(mv_parser):
    ents = mv_parser._extract_event_commands(
        [header(), cmd(401, ["\\n[2]：こんにちは"])], "Map009.json", "Ev1(x)/p0")
    assert (ents[0].namebox, ents[0].original) == ("\\n[2]", "：こんにちは")


def test_narration_with_actor_code_exports_intact(jp_game):
    p = _parser()
    entries = p.load_project(jp_game)
    e = next(x for x in entries if x.id == f"{M2}/dialog_1")
    e.translation, e.status = "\\N[1] obtained the sword!", "translated"
    p.save_project(jp_game, entries)
    lines = [c["parameters"][0] for c in _read_list(jp_game) if c["code"] == 401]
    assert "\\N[1] obtained the sword!" in lines


# ── Bug 3: Ren'Py skip regex ─────────────────────────────────────────

RPY = (
    'define passerby = Character("通行人")\n'
    'define e = Character("エミ")\n'
    '\n'
    'label start:\n'
    '    passerby "こんにちは"\n'
    '    "いい天気だ"\n'
    '    returner "戻ったよ"\n'
    '    e "さようなら"\n'
    '    pass\n'
    '    return\n'
)


@pytest.fixture
def renpy_game(tmp_path):
    root = tmp_path / "rp"
    (root / "game").mkdir(parents=True)
    (root / "renpy").mkdir()
    (root / "game" / "script.rpy").write_text(RPY, encoding="utf-8")
    return root


def test_renpy_alias_starting_with_keyword_is_dialogue(renpy_game):
    from translator.renpy import RenPyParser
    p = RenPyParser()
    ents = {e.id: e for e in p.load_project(str(renpy_game))}
    assert ents["script.rpy/start/dialog_0"].original == "こんにちは"
    assert ents["script.rpy/start/dialog_0"].context.startswith("[Speaker: 通行人]")
    assert ents["script.rpy/start/dialog_1"].original == "いい天気だ"
    assert ents["script.rpy/start/dialog_2"].original == "戻ったよ"
    assert ents["script.rpy/start/dialog_3"].original == "さようなら"
    # Bare pass / return statements are still skipped
    assert "script.rpy/start/dialog_4" not in ents

    tl = {"script.rpy/start/dialog_0": "Hello",
          "script.rpy/start/dialog_2": "I'm back",
          "script.rpy/start/dialog_3": "Goodbye"}
    entries = list(ents.values())
    for e in entries:
        if e.id in tl:
            e.translation, e.status = tl[e.id], "translated"
    p.save_project(str(renpy_game), entries)
    out = (renpy_game / "game" / "script.rpy").read_text(encoding="utf-8")
    assert '    passerby "Hello"\n' in out
    assert '    "いい天気だ"\n' in out
    assert '    returner "I\'m back"\n' in out
    assert '    e "Goodbye"\n' in out
    assert out.endswith('    pass\n    return\n')


# ── Migration of legacy (scheme 1) saved states ──────────────────────

# What the legacy parser produced for the translated event entries
# (verified against translator/rpgmaker_mv.py at commit 707b0fc):
# shared counter, bumped only for extracted text, bare \N[1] always
# stripped into the namebox.
LEGACY_MV = [
    # (legacy id, field, original, namebox, translation, status)
    ("Map001.json/Ev1(EV001)/p0/dialog_1", "dialog",
     "\\C[2]\\N[2]\\C[0]、おはよう！\n今日は\\V[3]ゴールドを持っているよ\\.\n一緒に冒険に行こう。",
     "", "\\C[2]\\N[2]\\C[0], morning!\nI have \\V[3] gold\\.\nLet's go.",
     "reviewed"),
    ("Map001.json/Ev1(EV001)/p0/dialog_2", "dialog",
     "ようこそ、旅の人。\nこの村は平和です。", "\\N<村人>",
     "Welcome, traveler.\nThis village is peaceful.", "translated"),
    ("Map001.json/Ev1(EV001)/p0/choice_3_0", "choice", "はい", "",
     "Yes", "translated"),
    ("Map001.json/Ev1(EV001)/p0/choice_4_1", "choice", "いいえ", "",
     "No", "translated"),
    ("Map001.json/Ev1(EV001)/p0/dialog_5", "dialog", "ありがとう！", "",
     "Thank you!", "translated"),
    ("Map001.json/Ev1(EV001)/p0/scroll_6", "scroll_text",
     "遠い昔、\n世界は闇に包まれていた。", "",
     "Long ago,\nthe world was shrouded in darkness.", "translated"),
    ("Map001.json/Ev1(EV001)/p0/change_name_7", "name", "アリス姫", "",
     "Princess Alice", "translated"),
    ("Map001.json/Ev1(EV001)/p0/plugin_mv_8", "plugin_command", "宝箱", "",
     "Chest", "translated"),
    # Map002: "OK" choice not extracted -> いいえ took counter 1
    (f"{M2}/choice_1_1", "choice", "いいえ", "", "No", "translated"),
    (f"{M2}/plugin_mv_2", "plugin_command", "宝箱", "", "Chest", "translated"),
    # Legacy bug 2: narration subject stripped into the namebox
    (f"{M2}/dialog_3", "dialog", "は剣を手に入れた！", "\\N[1]",
     "Obtained the sword!", "translated"),
    (f"{M2}/dialog_4", "dialog", "「やった！」", "\\N[1]", "\"Yay!\"",
     "translated"),
    (f"{M2}/dialog_5", "dialog", "\n行くぞ。", "\\N[1]", "\nLet's go.",
     "translated"),
    (f"{M2}/dialog_6", "dialog", "最後の台詞。", "", "The last line.",
     "reviewed"),
]

LEGACY_CONTEXT = {"plugin_mv": "[PLUGIN_CMD:D_TEXT 宝箱 24]"}


def _legacy_state(game_dir, path, with_orphan=True):
    """Fresh parse of the game, rewritten into a legacy scheme-1 state."""
    from translator.project_model import TranslationProject
    from translator.state_migration import is_event_entry
    p = _parser()
    entries = [e for e in p.load_project(game_dir) if not is_event_entry(e.id)]
    for e in entries:                       # a translated DB entry too
        if e.id == "Actors.json/1/name":
            e.translation, e.status = "Alice", "translated"
    rows = list(LEGACY_MV)
    if with_orphan:
        rows.append((f"{M2}/dialog_9", "dialog", "消えた台詞。", "",
                     "A removed line.", "translated"))
    for lid, fld, orig, nb, tl, st in rows:
        ctx = LEGACY_CONTEXT["plugin_mv"] if "plugin_mv" in lid else ""
        if lid.endswith("/dialog_3") and lid.startswith(M2):
            ctx = "[Speaker: アリス]"
        entries.append({"id": lid, "file": lid.split("/")[0], "field": fld,
                        "original": orig, "translation": tl, "status": st,
                        "context": ctx, "namebox": nb, "has_face": False})
    data = {"project_path": game_dir, "project_type": "rpgmaker_mv",
            "entries": [e if isinstance(e, dict) else e.__dict__
                        for e in entries],
            "glossary": {"宝箱": "Chest"}, "actor_genders": {"1": "female"}}
    # Legacy files have no "id_scheme" key at all
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return TranslationProject.load_state(path)


def test_legacy_state_loads_as_scheme_1(jp_game, tmp_path):
    proj = _legacy_state(jp_game, str(tmp_path / "s.json"))
    assert proj.id_scheme == 1


def test_migration_remaps_translations(jp_game, tmp_path):
    from translator.state_migration import migrate_project
    state = str(tmp_path / "_translation_state.json")
    proj = _legacy_state(jp_game, state)
    res = migrate_project(proj, parser=_parser(), state_path=state)

    assert res.status == "migrated"
    assert proj.id_scheme == 2
    assert res.migrated == len(LEGACY_MV)
    assert res.unmatched == 1
    assert res.namebox_merged == 1
    assert "migrated" in res.message and "1 unmatched" in res.message
    assert proj.migration_orphans[0]["translation"] == "A removed line."

    by_id = {e.id: e for e in proj.entries}
    assert len(by_id) == len(proj.entries)          # ids unique
    ev1 = "Map001.json/Ev1(EV001)/p0"
    assert by_id[f"{ev1}/choice_1_0"].translation == "Yes"
    assert by_id[f"{ev1}/choice_1_1"].translation == "No"
    assert by_id[f"{ev1}/dialog_3"].translation == "Thank you!"
    assert by_id[f"{ev1}/scroll_1"].translation.startswith("Long ago")
    assert by_id[f"{ev1}/change_name_1"].translation == "Princess Alice"
    assert by_id[f"{ev1}/plugin_mv_1"].translation == "Chest"
    assert by_id[f"{ev1}/dialog_1"].status == "reviewed"
    assert by_id[f"{ev1}/dialog_2"].namebox == "\\N<村人>"
    assert by_id[f"{M2}/choice_1_1"].translation == "No"
    assert by_id[f"{M2}/plugin_mv_1"].translation == "Chest"
    assert by_id[f"{M2}/plugin_mv_1"].context == "[PLUGIN_CMD:D_TEXT 宝箱 24]"
    narr = by_id[f"{M2}/dialog_1"]
    assert narr.original == "\\N[1]は剣を手に入れた！" and narr.namebox == ""
    assert narr.translation == "\\N[1] Obtained the sword!"
    assert "[Speaker:" not in narr.context
    assert by_id[f"{M2}/dialog_2"].translation == "\"Yay!\""
    assert by_id[f"{M2}/dialog_4"].status == "reviewed"
    # Non-event entries untouched
    assert by_id["Actors.json/1/name"].translation == "Alice"
    # Pre-migration backup written next to the state
    assert os.path.basename(res.backup_path) == "_translation_state.pre-v2.json"
    with open(res.backup_path, encoding="utf-8") as f:
        assert "id_scheme" not in json.load(f)


def test_migrated_export_matches_legacy_export(jp_game, tmp_path):
    """Exporting the old state as-is and after migration gives the same game."""
    from translator.state_migration import migrate_project
    legacy_game = str(tmp_path / "legacy_game")
    shutil.copytree(jp_game, legacy_game)

    old = _legacy_state(jp_game, str(tmp_path / "a.json"), with_orphan=False)
    _parser().save_project(legacy_game, old.entries)     # stale IDs

    proj = _legacy_state(jp_game, str(tmp_path / "b.json"), with_orphan=False)
    migrate_project(proj, parser=_parser())
    _parser().save_project(jp_game, proj.entries)

    for name in ("Map001.json", "Map002.json", "CommonEvents.json"):
        with open(os.path.join(legacy_game, "data", name), encoding="utf-8") as f:
            a = json.load(f)
        with open(os.path.join(jp_game, "data", name), encoding="utf-8") as f:
            b = json.load(f)
        assert a == b, name

    lines = [c["parameters"] for c in _read_list(jp_game)]
    assert ["OK", "No"] == lines[0][0]
    assert ["D_TEXT Chest 24"] in lines
    assert ["\\N[1] Obtained the sword!"] in lines
    assert ["\\N[1]\"Yay!\""] in lines
    assert ["The last line."] in lines


def test_migration_reads_backup_after_export(jp_game, tmp_path):
    """Already-exported game: data/ is English, data_original/ is not."""
    from translator.state_migration import migrate_project
    p = _parser()
    entries = p.load_project(jp_game)
    for e in entries:
        if e.status == "untranslated":
            e.translation, e.status = "EN", "translated"
    p.save_project(jp_game, entries)        # creates data_original/
    proj = _legacy_state(jp_game, str(tmp_path / "s.json"))
    res = migrate_project(proj, parser=p)
    assert res.status == "migrated" and res.unmatched == 1


def test_migration_is_idempotent(jp_game, tmp_path):
    from translator.project_model import TranslationProject
    from translator.state_migration import migrate_project
    state = str(tmp_path / "s.json")
    proj = _legacy_state(jp_game, state)
    migrate_project(proj, parser=_parser(), state_path=state)
    proj.save_state(state)

    again = TranslationProject.load_state(state)
    assert again.id_scheme == 2
    before = [(e.id, e.translation, e.status) for e in again.entries]
    res = migrate_project(again, parser=_parser(), state_path=state)
    assert res.status == "current" and res.message == ""
    assert [(e.id, e.translation, e.status) for e in again.entries] == before
    assert again.migration_orphans and again.migration_orphans[0]["original"] == "消えた台詞。"


def test_migration_skipped_when_game_missing(jp_game, tmp_path):
    from translator.state_migration import migrate_project
    proj = _legacy_state(jp_game, str(tmp_path / "s.json"))
    before = [(e.id, e.translation) for e in proj.entries]
    proj.project_path = str(tmp_path / "gone")
    res = migrate_project(proj, parser=_parser())
    assert res.status == "no_game" and res.message == ""
    assert proj.id_scheme == 1
    assert [(e.id, e.translation) for e in proj.entries] == before


def test_migration_refuses_unrelated_game(jp_game, tmp_path):
    from translator.state_migration import migrate_project
    proj = _legacy_state(jp_game, str(tmp_path / "s.json"))
    for e in proj.entries:
        if "/dialog_" in e.id:
            e.original = "存在しない" + e.original
    before = [(e.id, e.translation) for e in proj.entries]
    res = migrate_project(proj, parser=_parser())
    assert res.status == "low_match" and res.message
    assert proj.id_scheme == 1
    assert [(e.id, e.translation) for e in proj.entries] == before


def test_unaffected_engine_is_just_stamped():
    from translator.project_model import TranslationProject
    from translator.state_migration import migrate_project
    proj = TranslationProject(project_type="tyranoscript")
    proj.id_scheme = 1
    assert migrate_project(proj).status == "not_needed"
    assert proj.id_scheme == 2


def test_legacy_patch_remapped_on_import(jp_game, tmp_path):
    from translator.project_model import TranslationProject
    legacy = _legacy_state(jp_game, str(tmp_path / "s.json"))
    zip_path = str(tmp_path / "patch.zip")
    legacy.export_patch(zip_path, game_title="t")
    patch = TranslationProject.import_patch(zip_path)
    assert patch.id_scheme == 1

    fresh = TranslationProject(entries=_parser().load_project(jp_game))
    stats = fresh.import_translations(patch)
    by_id = {e.id: e for e in fresh.entries}
    assert by_id["Map001.json/Ev1(EV001)/p0/choice_1_1"].translation == "No"
    assert by_id[f"{M2}/dialog_1"].translation == "\\N[1] Obtained the sword!"
    assert by_id[f"{M2}/dialog_4"].translation == "The last line."
    assert by_id[f"{M2}/dialog_4"].status == "reviewed"
    assert stats["by_id"] >= len(LEGACY_MV)


def test_current_patch_roundtrips_scheme(jp_game, tmp_path):
    from translator.project_model import TranslationProject
    proj = TranslationProject(entries=_parser().load_project(jp_game))
    proj.entries[0].translation, proj.entries[0].status = "x", "translated"
    zip_path = str(tmp_path / "p.zip")
    proj.export_patch(zip_path)
    assert TranslationProject.import_patch(zip_path).id_scheme == 2


def test_renpy_legacy_state_migrated_and_exported(renpy_game, tmp_path):
    from translator.project_model import TranslationProject
    from translator.renpy import RenPyParser
    from translator.state_migration import migrate_project

    # Legacy parser skipped the passerby/returner lines, so the label's
    # remaining lines were dialog_0 / dialog_1.
    legacy = {
        "project_path": str(renpy_game), "project_type": "renpy",
        "entries": [
            {"id": "script.rpy/define/passerby", "file": "script.rpy",
             "field": "name", "original": "通行人", "translation": "Passerby",
             "status": "translated", "context": "[Character Definition]"},
            {"id": "script.rpy/start/dialog_0", "file": "script.rpy",
             "field": "dialog", "original": "いい天気だ",
             "translation": "Nice weather", "status": "translated"},
            {"id": "script.rpy/start/dialog_1", "file": "script.rpy",
             "field": "dialog", "original": "さようなら",
             "translation": "Goodbye", "status": "reviewed",
             "context": "[Speaker: エミ]"},
        ],
        "glossary": {}, "actor_genders": {},
    }
    state = tmp_path / "s.json"
    state.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    proj = TranslationProject.load_state(str(state))
    res = migrate_project(proj, parser=RenPyParser(), state_path=str(state))
    assert res.status == "migrated" and res.migrated == 2
    assert res.new_entries == 2               # passerby + returner lines
    by_id = {e.id: e for e in proj.entries}
    assert by_id["script.rpy/define/passerby"].translation == "Passerby"
    assert by_id["script.rpy/start/dialog_0"].status == "untranslated"
    assert by_id["script.rpy/start/dialog_1"].translation == "Nice weather"
    assert by_id["script.rpy/start/dialog_3"].translation == "Goodbye"

    RenPyParser().save_project(str(renpy_game), proj.entries)
    out = (renpy_game / "game" / "script.rpy").read_text(encoding="utf-8")
    assert '    passerby "こんにちは"\n' in out
    assert '    "Nice weather"\n' in out
    assert '    e "Goodbye"\n' in out
    assert 'Character("Passerby")' in out


def test_renpy_migration_reads_game_original(renpy_game, tmp_path):
    """After an export game/ holds English; re-parse must use the backup."""
    from translator.project_model import TranslationProject
    from translator.renpy import RenPyParser
    from translator.state_migration import migrate_project
    p = RenPyParser()
    ents = p.load_project(str(renpy_game))
    for e in ents:
        e.translation, e.status = "EN " + e.original, "translated"
    p.save_project(str(renpy_game), ents)
    legacy = {"project_path": str(renpy_game), "project_type": "renpy",
              "entries": [{"id": "script.rpy/start/dialog_1", "file": "script.rpy",
                           "field": "dialog", "original": "さようなら",
                           "translation": "Goodbye", "status": "translated"}]}
    state = tmp_path / "s.json"
    state.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    proj = TranslationProject.load_state(str(state))
    res = migrate_project(proj, parser=p)
    assert res.status == "migrated" and res.unmatched == 0
    assert {e.id: e for e in proj.entries}[
        "script.rpy/start/dialog_3"].translation == "Goodbye"
