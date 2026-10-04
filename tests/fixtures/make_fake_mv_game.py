"""Generate a tiny fake RPG Maker MV game used by the test suite.

Run:  python tests/fixtures/make_fake_mv_game.py

Writes ``tests/fixtures/fake_mv_game/`` (MV editor-project layout: data/ and
js/ at the project root, no www/).  The generated files are committed, but
this script is the source of truth -- regenerate after editing.

Every Japanese string used here is also listed in ``tests/fake_ai.py``'s
dictionary so the fake LLM can give a deterministic "translation".
"""

import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fake_mv_game")


# ── Event command helpers ────────────────────────────────────────────

def cmd(code, params, indent=0):
    return {"code": code, "indent": indent, "parameters": params}


def show_text_header(face="", index=0, speaker=None):
    params = [face, index, 0, 2]
    if speaker is not None:          # MZ-style header with speaker name
        params.append(speaker)
    return cmd(101, params)


def text(line, indent=0):
    return cmd(401, [line], indent)


def end():
    return cmd(0, [])


def page(cmds):
    return {
        "conditions": {
            "actorId": 1, "actorValid": False, "itemId": 1, "itemValid": False,
            "selfSwitchCh": "A", "selfSwitchValid": False,
            "switch1Id": 1, "switch1Valid": False,
            "switch2Id": 1, "switch2Valid": False,
            "variableId": 1, "variableValid": False, "variableValue": 0,
        },
        "directionFix": False,
        "image": {"characterIndex": 0, "characterName": "", "direction": 2,
                  "pattern": 0, "tileId": 0},
        "list": cmds,
        "moveFrequency": 3, "moveRoute": {"list": [end()], "repeat": True,
                                          "skippable": False, "wait": False},
        "moveSpeed": 3, "moveType": 0, "priorityType": 1,
        "stepAnime": False, "through": False, "trigger": 0, "walkAnime": True,
    }


def event(eid, name, pages, x=0, y=0):
    return {"id": eid, "name": name, "note": "", "pages": pages, "x": x, "y": y}


# ── Database ─────────────────────────────────────────────────────────

ACTORS = [
    None,
    {
        "id": 1, "battlerName": "Actor1_1", "characterIndex": 0,
        "characterName": "Actor1", "classId": 1, "equips": [1, 1, 0, 0, 0],
        "faceIndex": 0, "faceName": "Actor1", "traits": [],
        "initialLevel": 1, "maxLevel": 99,
        "name": "アリス", "nickname": "見習い魔法使い",
        "note": "",
        "profile": "明るい少女。\n魔法の勉強中。",
    },
    {
        "id": 2, "battlerName": "Actor1_2", "characterIndex": 1,
        "characterName": "Actor1", "classId": 1, "equips": [1, 1, 0, 0, 0],
        "faceIndex": 0, "faceName": "Actor2", "traits": [],
        "initialLevel": 1, "maxLevel": 99,
        "name": "ボブ", "nickname": "騎士",
        "note": "<title:剣士>",
        "profile": "勇敢な男の騎士。",
    },
    {
        "id": 3, "battlerName": "", "characterIndex": 0,
        "characterName": "", "classId": 1, "equips": [0, 0, 0, 0, 0],
        "faceIndex": 0, "faceName": "", "traits": [],
        "initialLevel": 1, "maxLevel": 99,
        "name": "謎の人物", "nickname": "", "note": "", "profile": "",
    },
]

CLASSES = [None, {"id": 1, "name": "勇者", "note": "", "traits": [],
                  "learnings": [], "expParams": [30, 20, 30, 30],
                  "params": []}]

ITEMS = [
    None,
    {"id": 1, "name": "ポーション", "description": "HPを50回復する。",
     "note": "", "iconIndex": 176, "price": 50, "consumable": True},
    {"id": 2, "name": "エーテル", "description": "MPを20回復する。",
     "note": "", "iconIndex": 176, "price": 100, "consumable": True},
]

SKILLS = [
    None,
    {"id": 1, "name": "攻撃", "description": "", "message1": "の攻撃！",
     "message2": "", "note": "", "mpCost": 0},
    {"id": 2, "name": "ファイア", "description": "敵単体に炎のダメージ。",
     "message1": "はファイアを唱えた！", "message2": "", "note": "",
     "mpCost": 5},
]

WEAPONS = [None, {"id": 1, "name": "木の剣", "description": "ただの木の剣。",
                  "note": "", "wtypeId": 1, "price": 10}]
ARMORS = [None, {"id": 1, "name": "革の盾", "description": "軽い盾。",
                 "note": "", "atypeId": 1, "etypeId": 2, "price": 10}]
ENEMIES = [None, {"id": 1, "name": "スライム", "battlerName": "Slime",
                  "note": "", "exp": 1, "gold": 1}]
STATES = [None, {"id": 1, "name": "戦闘不能", "message1": "は倒れた！",
                 "message2": "を倒した！", "message3": "",
                 "message4": "は立ち上がった！", "note": ""}]

TROOPS = [
    None,
    {
        "id": 1, "name": "スライム×2",
        "members": [{"enemyId": 1, "x": 300, "y": 400, "hidden": False}],
        "pages": [{
            "conditions": {"turnEnding": False, "turnValid": False,
                           "enemyValid": False, "actorValid": False,
                           "switchValid": False},
            "span": 0,
            "list": [
                show_text_header(),
                text("スライムが現れた！"),
                end(),
            ],
        }],
    },
]

SYSTEM = {
    "gameTitle": "勇者と魔王の物語",
    "locale": "ja_JP",
    "currencyUnit": "G",
    "elements": ["", "物理", "炎"],
    "skillTypes": ["", "魔法"],
    "weaponTypes": ["", "剣"],
    "armorTypes": ["", "一般防具"],
    "equipTypes": ["", "武器", "盾"],
    "switches": ["", ""],
    "variables": ["", "", "", "所持金"],
    "terms": {
        "basic": ["レベル", "Lv", "ＨＰ", "HP"],
        "commands": ["戦う", "逃げる", None, "アイテム"],
        "params": ["最大ＨＰ", "最大ＭＰ"],
        "messages": {
            "actionFailure": "%1には効かなかった！",
            "obtainGold": "お金を %1\\G 手に入れた！",
            "victory": "%1の勝利！",
        },
    },
}

COMMON_EVENTS = [
    None,
    {
        "id": 1, "name": "村長の話", "switchId": 1, "trigger": 0,
        "list": [
            show_text_header("", 0, "村長"),
            text("よく来たな、若者よ。"),
            text("村を守ってくれ。"),
            end(),
        ],
    },
]

MAP_INFOS = [
    None,
    {"id": 1, "expanded": False, "name": "MAP001", "order": 1,
     "parentId": 0, "scrollX": 0, "scrollY": 0},
]

# Map001, event 1: the "kitchen sink" conversation
EV1_LIST = [
    # Face graphic matches Actor 1 (Alice) -> speaker resolved via face
    show_text_header("Actor1", 0),
    text("\\C[2]\\N[2]\\C[0]、おはよう！"),
    text("今日は\\V[3]ゴールドを持っているよ\\."),
    text("一緒に冒険に行こう。"),
    # No face; inline namebox (Lunatlazur_ActorNameWindow style)
    show_text_header("", 0),
    text("\\N<村人>ようこそ、旅の人。"),
    text("この村は平和です。"),
    # Choices with branches
    cmd(102, [["はい", "いいえ"], 1, 0, 2, 0]),
    cmd(402, [0, "はい"]),
    show_text_header("", 0, None),
    text("ありがとう！", 1),
    cmd(0, [], 1),
    cmd(402, [1, "いいえ"]),
    cmd(0, [], 1),
    cmd(404, []),
    # Scrolling text
    cmd(105, [2, False]),
    cmd(405, ["遠い昔、"]),
    cmd(405, ["世界は闇に包まれていた。"]),
    # Change actor name
    cmd(320, [1, "アリス姫"]),
    # MV plugin command (whitelisted D_TEXT)
    cmd(356, ["D_TEXT 宝箱 24"]),
    end(),
]

# Map001, event 2: actor-code namebox + two blocks sharing a first line
EV2_LIST = [
    show_text_header("", 0),
    text("\\n<\\n[1]>ねえ、聞いて。"),
    show_text_header("", 0),
    text("……。"),
    text("誰もいない。"),
    show_text_header("", 0),
    text("……。"),
    text("静かだ。"),
    end(),
]

MAP001 = {
    "autoplayBgm": False, "autoplayBgs": False, "battleback1Name": "",
    "battleback2Name": "", "bgm": {"name": "", "pan": 0, "pitch": 100,
                                   "volume": 90},
    "bgs": {"name": "", "pan": 0, "pitch": 100, "volume": 90},
    "disableDashing": False, "displayName": "はじまりの村",
    "encounterList": [], "encounterStep": 30, "height": 13, "note": "",
    "parallaxLoopX": False, "parallaxLoopY": False, "parallaxName": "",
    "parallaxShow": True, "parallaxSx": 0, "parallaxSy": 0,
    "scrollType": 0, "specifyBattleback": False, "tilesetId": 1,
    "width": 17,
    "data": [0] * 8,
    "events": [
        None,
        event(1, "EV001", [page(EV1_LIST)], 3, 4),
        event(2, "EV002", [page(EV2_LIST)], 5, 6),
    ],
}

PLUGINS = [
    {
        "name": "TestMessagePlugin", "status": True,
        "description": "テスト用プラグイン",
        "parameters": {
            "WelcomeText": "ようこそ！",
            "FontSize": "28",
            "Picture": "img/pictures/立ち絵",
            "Title": json.dumps("冒険の書", ensure_ascii=False),
        },
    },
    {
        "name": "NestedPlugin", "status": True, "description": "",
        "parameters": {
            "Messages": json.dumps(
                [json.dumps({"text": "こんにちは", "id": "1"},
                            ensure_ascii=False)],
                ensure_ascii=False),
        },
    },
]


def _dump(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def build(out_dir: str = OUT) -> str:
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    data_dir = os.path.join(out_dir, "data")
    js_dir = os.path.join(out_dir, "js")
    fonts_dir = os.path.join(out_dir, "fonts")
    for d in (data_dir, js_dir, fonts_dir):
        os.makedirs(d)

    files = {
        "Actors.json": ACTORS, "Classes.json": CLASSES, "Items.json": ITEMS,
        "Skills.json": SKILLS, "Weapons.json": WEAPONS,
        "Armors.json": ARMORS, "Enemies.json": ENEMIES,
        "States.json": STATES, "Troops.json": TROOPS,
        "System.json": SYSTEM, "CommonEvents.json": COMMON_EVENTS,
        "MapInfos.json": MAP_INFOS, "Map001.json": MAP001,
    }
    for name, data in files.items():
        _dump(os.path.join(data_dir, name), data)

    with open(os.path.join(js_dir, "plugins.js"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write("// Generated by RPG Maker.\n"
                "// Do not edit this file directly.\n")
        f.write("var $plugins =\n")
        f.write(json.dumps(PLUGINS, ensure_ascii=False, indent=0))
        f.write(";\n")
    # Marker file so RPGMakerMVParser.detect_engine() reports "mv"
    with open(os.path.join(js_dir, "rpg_core.js"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write("// stub rpg_core.js for tests\n")
    with open(os.path.join(fonts_dir, "gamefont.css"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write('@font-face {\n    font-family: GameFont;\n'
                '    src: url("mplus-1m-regular.woff");\n}\n')
    _dump(os.path.join(out_dir, "package.json"),
          {"name": "fake-mv-game", "main": "index.html",
           "window": {"title": "勇者と魔王の物語", "width": 816,
                      "height": 624}})
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write("<!DOCTYPE html>\n<html><head><meta charset=\"UTF-8\">"
                "<title>勇者と魔王の物語</title></head>"
                "<body><script src=\"js/rpg_core.js\"></script>"
                "<script src=\"js/plugins.js\"></script></body></html>\n")
    return out_dir


if __name__ == "__main__":
    print("Wrote", build())
