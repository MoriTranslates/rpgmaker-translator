"""RPGMakerMVParser.load_project() against the fake MV fixture."""

import filecmp
import os

import pytest

from conftest import FAKE_MV_GAME


@pytest.fixture(scope="module")
def entries():
    from translator.rpgmaker_mv import RPGMakerMVParser
    return RPGMakerMVParser().load_project(FAKE_MV_GAME)


@pytest.fixture(scope="module")
def by_id(entries):
    return {e.id: e for e in entries}


def test_fixture_generator_is_reproducible(tmp_path):
    """The committed fixture matches what the generator script produces."""
    from fixtures.make_fake_mv_game import build
    out = build(str(tmp_path / "regen"))
    for root, _dirs, files in os.walk(out):
        for name in files:
            gen = os.path.join(root, name)
            rel = os.path.relpath(gen, out)
            committed = os.path.join(FAKE_MV_GAME, rel)
            assert os.path.isfile(committed), f"missing committed file {rel}"
            assert filecmp.cmp(gen, committed, shallow=False), \
                f"{rel} is stale - rerun tests/fixtures/make_fake_mv_game.py"


def test_engine_detection():
    from translator.rpgmaker_mv import RPGMakerMVParser
    from translator.engine_handler import detect_engine, RPGMakerMVHandler
    assert RPGMakerMVParser.detect_engine(FAKE_MV_GAME) == "mv"
    assert detect_engine(FAKE_MV_GAME) is RPGMakerMVHandler


def test_ids_are_unique(entries):
    ids = [e.id for e in entries]
    assert len(ids) == len(set(ids))


def test_game_title(mv_parser, by_id):
    assert mv_parser.get_game_title(FAKE_MV_GAME) == "勇者と魔王の物語"
    assert by_id["System.json/gameTitle"].original == "勇者と魔王の物語"


def test_database_entries(by_id):
    assert by_id["Actors.json/1/name"].original == "アリス"
    assert by_id["Actors.json/2/name"].original == "ボブ"
    assert by_id["Actors.json/1/profile"].original == "明るい少女。\n魔法の勉強中。"
    assert by_id["Items.json/1/description"].original == "HPを50回復する。"
    assert by_id["Skills.json/2/message1"].field == "message1"
    assert by_id["States.json/1/message4"].original == "は立ち上がった！"
    assert by_id["Enemies.json/1/name"].original == "スライム"
    assert by_id["Troops.json/1/name"].original == "スライム×2"
    # Empty strings are never extracted
    assert "Skills.json/1/description" not in by_id
    assert "States.json/1/message3" not in by_id


def test_note_tag_extracted(by_id):
    e = by_id["Actors.json/2/note/title/0"]
    assert e.original == "剣士"
    assert e.field == "note_tag:title"


def test_system_terms(by_id):
    # MV terms.messages is a dict -> keyed ids
    assert by_id["System.json/terms/messages/victory"].original == "%1の勝利！"
    # commands with a null hole keep their real index
    assert by_id["System.json/terms/commands/3"].original == "アイテム"
    assert "System.json/terms/commands/2" not in by_id
    # System terms are extracted even without Japanese
    assert by_id["System.json/terms/basic/3"].original == "HP"
    # Type arrays only with Japanese, empty slot 0 skipped
    assert by_id["System.json/elements/2"].original == "炎"
    assert "System.json/elements/0" not in by_id


def test_dialogue_grouped_with_face_and_speaker(by_id):
    e = by_id["Map001.json/Ev1(EV001)/p0/dialog_1"]
    assert e.field == "dialog"
    assert e.original.split("\n") == [
        "\\C[2]\\N[2]\\C[0]、おはよう！",
        "今日は\\V[3]ゴールドを持っているよ\\.",
        "一緒に冒険に行こう。",
    ]
    assert e.has_face is True
    assert e.namebox == ""
    # Face "Actor1"/0 resolves to actor 1 as the speaker
    assert e.context.startswith("[Speaker: アリス]")


def test_inline_namebox_stripped(by_id):
    e = by_id["Map001.json/Ev1(EV001)/p0/dialog_2"]
    assert e.namebox == "\\N<村人>"
    assert e.original == "ようこそ、旅の人。\nこの村は平和です。"
    assert "\\N<" not in e.original
    assert e.has_face is False
    assert e.context.startswith("[Speaker: 村人]")
    # Literal namebox name becomes a speaker_name entry
    sp = by_id["Map001.json/speaker/村人"]
    assert sp.field == "speaker_name"


def test_actor_code_namebox_resolves_speaker(by_id):
    e = by_id["Map001.json/Ev2(EV002)/p0/dialog_1"]
    assert e.namebox == "\\n<\\n[1]>"
    assert e.original == "ねえ、聞いて。"
    assert e.context.startswith("[Speaker: アリス]")
    # Actor-code nameboxes don't create speaker entries
    assert not any(k.endswith("/speaker/\\n[1]") for k in by_id)


def test_mz_speaker_name_header(by_id):
    e = by_id["CommonEvents.json/CE1(村長の話)/dialog_1"]
    assert e.original == "よく来たな、若者よ。\n村を守ってくれ。"
    assert e.context.startswith("[Speaker: 村長]")
    assert by_id["CommonEvents.json/speaker/村長"].field == "speaker_name"


def test_choices(by_id):
    yes = by_id["Map001.json/Ev1(EV001)/p0/choice_3_0"]
    no = by_id["Map001.json/Ev1(EV001)/p0/choice_4_1"]
    assert (yes.field, yes.original) == ("choice", "はい")
    assert (no.field, no.original) == ("choice", "いいえ")


def test_nested_branch_dialogue(by_id):
    e = by_id["Map001.json/Ev1(EV001)/p0/dialog_5"]
    assert e.original == "ありがとう！"


def test_scroll_text(by_id):
    e = by_id["Map001.json/Ev1(EV001)/p0/scroll_6"]
    assert e.field == "scroll_text"
    assert e.original == "遠い昔、\n世界は闇に包まれていた。"


def test_change_name_and_plugin_command(by_id):
    e = by_id["Map001.json/Ev1(EV001)/p0/change_name_7"]
    assert (e.field, e.original) == ("name", "アリス姫")
    p = by_id["Map001.json/Ev1(EV001)/p0/plugin_mv_8"]
    assert p.original == "宝箱"
    assert p.context == "[PLUGIN_CMD:D_TEXT 宝箱 24]"


def test_shared_first_line_blocks(by_id):
    a = by_id["Map001.json/Ev2(EV002)/p0/dialog_2"]
    b = by_id["Map001.json/Ev2(EV002)/p0/dialog_3"]
    assert a.original == "……。\n誰もいない。"
    assert b.original == "……。\n静かだ。"


def test_troop_and_display_name(by_id):
    assert by_id["Troops.json/Troop1(スライム×2)/p0/dialog_1"].original == \
        "スライムが現れた！"
    assert by_id["Map001.json/displayName"].original == "はじまりの村"


def test_plugin_parameters(by_id):
    assert by_id["plugins.js/TestMessagePlugin/WelcomeText"].original == "ようこそ！"
    # JSON-encoded scalar string is decoded
    assert by_id["plugins.js/TestMessagePlugin/Title"].original == "冒険の書"
    # Nested JSON-in-JSON
    assert by_id["plugins.js/NestedPlugin/Messages/[0]/text"].original == "こんにちは"
    # Asset paths / numbers are not extracted
    assert "plugins.js/TestMessagePlugin/Picture" not in by_id
    assert "plugins.js/TestMessagePlugin/FontSize" not in by_id


def test_all_entries_start_untranslated(entries):
    assert {e.status for e in entries} == {"untranslated"}


def test_context_window_size(by_id):
    from translator.rpgmaker_mv import RPGMakerMVParser
    p = RPGMakerMVParser()
    p.context_size = 0
    e = {x.id: x for x in p.load_project(FAKE_MV_GAME)}[
        "Map001.json/Ev2(EV002)/p0/dialog_3"]
    assert e.context == ""
    # default (3) includes the previous blocks
    assert "誰もいない" in by_id["Map001.json/Ev2(EV002)/p0/dialog_3"].context


def test_actors_raw_gender_detection(mv_parser):
    actors = {a["id"]: a for a in mv_parser.load_actors_raw(FAKE_MV_GAME)}
    assert actors[1]["auto_gender"] == "female"
    assert actors[2]["auto_gender"] == "male"
    assert actors[3]["auto_gender"] == "unknown"
    ctx = mv_parser.build_actor_context(list(actors.values()), {3: "female"})
    assert "Actor 1: アリス [female - use she/her]" in ctx
    assert "Actor 2: ボブ [male - use he/him]" in ctx
    assert "Actor 3: 謎の人物 [female - use she/her]" in ctx


def test_missing_data_dir_raises(tmp_path, mv_parser):
    with pytest.raises(FileNotFoundError):
        mv_parser.load_project(str(tmp_path))


def test_www_layout_is_supported(tmp_path, mv_parser):
    import shutil
    dst = tmp_path / "game"
    shutil.copytree(FAKE_MV_GAME, dst / "www")
    ents = mv_parser.load_project(str(dst))
    assert any(e.id == "Map001.json/Ev1(EV001)/p0/dialog_1" for e in ents)
    assert any(e.file == "plugins.js" for e in ents)
