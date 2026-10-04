"""Export round trip on the fake MV fixture:
parse -> translate -> save_project -> re-read JSON."""

import filecmp
import json
import os

import pytest

import fake_ai
from conftest import FAKE_MV_GAME
from translator import CONTROL_CODE_RE, JAPANESE_RE


def load(game, name):
    with open(os.path.join(game, "data", name), encoding="utf-8") as f:
        return json.load(f)


def load_plugins(game):
    from translator.rpgmaker_mv import RPGMakerMVParser
    return {p["name"]: p for p in
            RPGMakerMVParser._load_plugins_js(os.path.join(game, "js", "plugins.js"))}


def ev_list(mapdata, ev_id):
    return mapdata["events"][ev_id]["pages"][0]["list"]


def texts(cmd_list, code):
    return [c["parameters"][0] for c in cmd_list if c["code"] == code]


def translate_all(entries):
    """Deterministic 'translation' of every entry (no LLM)."""
    for e in entries:
        e.translation = fake_ai.fake_translate(e.original)
        e.status = "translated"
    return entries


@pytest.fixture
def exported(mv_game, mv_parser):
    entries = translate_all(mv_parser.load_project(mv_game))
    mv_parser.save_project(mv_game, entries)
    return mv_game, entries


def test_backups_created(exported):
    game, _ = exported
    backup = os.path.join(game, "data_original")
    assert os.path.isdir(backup)
    # Backup has the original data
    for name in os.listdir(os.path.join(FAKE_MV_GAME, "data")):
        assert load(game.replace("\\", "/"), "../data_original/" + name) == \
            load(FAKE_MV_GAME, name), name
    assert filecmp.cmp(os.path.join(game, "js", "plugins_original.js"),
                       os.path.join(FAKE_MV_GAME, "js", "plugins.js"),
                       shallow=False)


def test_all_exported_files_are_valid_json(exported):
    game, _ = exported
    for name in os.listdir(os.path.join(game, "data")):
        load(game, name)  # raises on invalid JSON


def test_dialogue_line_counts_preserved(exported):
    game, _ = exported
    orig = load(FAKE_MV_GAME, "Map001.json")
    new = load(game, "Map001.json")
    for ev in (1, 2):
        o, n = ev_list(orig, ev), ev_list(new, ev)
        assert [c["code"] for c in o] == [c["code"] for c in n]
        assert len(texts(o, 401)) == len(texts(n, 401))
        assert len(texts(o, 405)) == len(texts(n, 405))


def test_control_codes_intact(exported):
    game, _ = exported
    lines = texts(ev_list(load(game, "Map001.json"), 1), 401)
    assert lines[0] == "\\C[2]\\N[2]\\C[0], Good morning!"
    assert lines[1] == "Today I have \\V[3] gold with me\\."
    assert lines[2] == "Let's go on an adventure together."
    orig_codes = CONTROL_CODE_RE.findall(
        "".join(texts(ev_list(load(FAKE_MV_GAME, "Map001.json"), 1), 401)[:3]))
    assert CONTROL_CODE_RE.findall("".join(lines[:3])) == orig_codes


def test_namebox_restored_and_translated(exported):
    game, _ = exported
    m = load(game, "Map001.json")
    ev1 = texts(ev_list(m, 1), 401)
    assert ev1[3] == "\\N<Villager>Welcome, traveler."
    assert ev1[4] == "This village is peaceful."
    ev2 = texts(ev_list(m, 2), 401)
    # Actor-code namebox is kept verbatim (resolved at runtime)
    assert ev2[0] == "\\n<\\n[1]>Hey, listen."


def test_blocks_with_same_first_line_map_correctly(exported):
    game, _ = exported
    ev2 = texts(ev_list(load(game, "Map001.json"), 2), 401)
    assert ev2[1:] == ["....", "Nobody is here.", "....", "It's quiet."]


def test_choices_scroll_and_commands(exported):
    game, _ = exported
    ev1 = ev_list(load(game, "Map001.json"), 1)
    choice = next(c for c in ev1 if c["code"] == 102)
    assert choice["parameters"][0] == ["Yes", "No"]
    assert texts(ev1, 405) == ["Long ago,", "the world was shrouded in darkness."]
    assert texts(ev1, 401)[5] == "Thank you!"
    assert next(c for c in ev1 if c["code"] == 320)["parameters"] == [1, "Princess Alice"]
    assert next(c for c in ev1 if c["code"] == 356)["parameters"][0].startswith(
        "D_TEXT Treasure")


def test_mz_speaker_header_translated(exported):
    game, _ = exported
    ce = load(game, "CommonEvents.json")[1]["list"]
    assert ce[0]["parameters"][4] == "Village Chief"
    assert texts(ce, 401) == ["Welcome, young one.", "Please protect the village."]


def test_database_and_system(exported):
    game, _ = exported
    actors = load(game, "Actors.json")
    assert actors[0] is None
    assert actors[1]["name"] == "Alice"
    assert actors[1]["profile"] == "A cheerful girl.\nStudying magic."
    assert actors[2]["note"] == "<title:Swordsman>"
    assert actors[1]["faceName"] == "Actor1"   # untouched non-text fields
    system = load(game, "System.json")
    assert system["gameTitle"] == "Tale of the Hero and the Demon King"
    assert system["locale"] == ""
    assert system["terms"]["commands"] == ["Fight", "Escape", None, "Item"]
    assert system["terms"]["messages"]["victory"] == "%1 is victorious!"
    assert system["elements"] == ["", "Physical", "Fire"]
    troops = load(game, "Troops.json")
    assert troops[1]["name"] == "Slime x2"
    assert texts(troops[1]["pages"][0]["list"], 401) == ["A slime appeared!"]
    assert load(game, "Map001.json")["displayName"] == "Starting Village"


def test_plugins_exported(exported):
    game, _ = exported
    plugins = load_plugins(game)
    params = plugins["TestMessagePlugin"]["parameters"]
    assert params["WelcomeText"] == "Welcome!"
    assert json.loads(params["Title"]) == "Adventure Log"   # stays JSON-encoded
    assert params["Picture"] == "img/pictures/立ち絵"
    nested = json.loads(json.loads(plugins["NestedPlugin"]["parameters"]["Messages"])[0])
    assert nested == {"text": "Hello", "id": "1"}


def test_gamefont_swapped(exported):
    game, _ = exported
    fonts = os.path.join(game, "fonts")
    assert os.path.isfile(os.path.join(fonts, "gamefont_original.css"))
    with open(os.path.join(fonts, "gamefont.css"), encoding="utf-8") as f:
        assert "Consolas" in f.read()


def test_no_japanese_left_in_exported_text(exported, mv_parser):
    """Re-parsing the exported game finds no Japanese outside of files the
    parser always re-reads from backup (plugins.js)."""
    game, _ = exported
    leftovers = [e for e in mv_parser.load_project(game)
                 if e.file != "plugins.js" and JAPANESE_RE.search(e.original)]
    assert leftovers == []


def test_export_is_idempotent(exported, mv_parser):
    game, entries = exported
    data = os.path.join(game, "data")
    first = {n: open(os.path.join(data, n), "rb").read() for n in os.listdir(data)}
    plugins_first = open(os.path.join(game, "js", "plugins.js"), "rb").read()
    mv_parser.save_project(game, entries)
    second = {n: open(os.path.join(data, n), "rb").read() for n in os.listdir(data)}
    assert first == second
    assert plugins_first == open(os.path.join(game, "js", "plugins.js"), "rb").read()


def test_reexport_after_edit_reads_from_backup(exported, mv_parser):
    game, entries = exported
    by_id = {e.id: e for e in entries}
    by_id["Map001.json/Ev1(EV001)/p0/dialog_2"].translation = \
        "Hi there, stranger.\nNice and calm here."
    mv_parser.save_project(game, entries)
    ev1 = texts(ev_list(load(game, "Map001.json"), 1), 401)
    assert ev1[3:5] == ["\\N<Villager>Hi there, stranger.", "Nice and calm here."]


def test_untranslated_entries_left_alone(mv_game, mv_parser):
    entries = mv_parser.load_project(mv_game)
    only = next(e for e in entries if e.id == "Actors.json/1/name")
    only.translation, only.status = "Alice", "translated"
    # A translation with status untranslated must not be written
    other = next(e for e in entries if e.id == "Actors.json/2/name")
    other.translation = "Bob"
    mv_parser.save_project(mv_game, entries)
    actors = load(mv_game, "Actors.json")
    assert actors[1]["name"] == "Alice"
    assert actors[2]["name"] == "ボブ"
    # Files with no translated entries keep their original content
    assert load(mv_game, "Map001.json") == load(FAKE_MV_GAME, "Map001.json")


def test_longer_translation_inserts_extra_401(mv_game, mv_parser):
    entries = mv_parser.load_project(mv_game)
    e = next(x for x in entries if x.id == "Map001.json/Ev2(EV002)/p0/dialog_1")
    e.translation, e.status = "Hey.\nListen to me.\nPlease.", "translated"
    mv_parser.save_project(mv_game, entries)
    ev2 = ev_list(load(mv_game, "Map001.json"), 2)
    assert texts(ev2, 401)[:3] == ["\\n<\\n[1]>Hey.", "Listen to me.", "Please."]
    for c in ev2:
        assert set(c) >= {"code", "indent", "parameters"}


def test_shorter_translation_pads_with_empty_lines(mv_game, mv_parser):
    entries = mv_parser.load_project(mv_game)
    e = next(x for x in entries if x.id == "Map001.json/Ev1(EV001)/p0/dialog_1")
    e.translation, e.status = "\\C[2]\\N[2]\\C[0], morning!", "translated"
    mv_parser.save_project(mv_game, entries)
    lines = texts(ev_list(load(mv_game, "Map001.json"), 1), 401)
    assert lines[:3] == ["\\C[2]\\N[2]\\C[0], morning!", "", ""]


def test_full_pipeline_with_fake_llm(mv_game, mv_parser, fake_llm):
    """Parse -> AIClient.translate_batch (fake LLM) -> export -> verify."""
    from translator.ai_client import AIClient
    client = AIClient()
    entries = mv_parser.load_project(mv_game)
    payload = [(f"Line{i}", e.original, e.context, e.field)
               for i, e in enumerate(entries)]
    results = client.translate_batch(payload)
    for i, e in enumerate(entries):
        e.translation = results[f"Line{i}"]
        e.status = "translated"
    mv_parser.save_project(mv_game, entries)
    lines = texts(ev_list(load(mv_game, "Map001.json"), 1), 401)
    assert lines[0] == "\\C[2]\\N[2]\\C[0], Good morning!"
    assert lines[1].endswith("\\.") and "\\V[3]" in lines[1]
    assert lines[3].startswith("\\N<Villager>")
