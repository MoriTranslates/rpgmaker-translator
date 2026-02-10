"""Pre-built glossary of common Japanese terms for RPG/adult game translation.

Users can load these from the Settings > Glossary tab. Each category can be
loaded independently. Entries never overwrite user-defined glossary terms.
"""

# ── Body / anatomy ──────────────────────────────────────────────
BODY = {
    "おまんこ": "pussy",
    "マンコ": "pussy",
    "まんこ": "pussy",
    "おちんちん": "cock",
    "ちんちん": "cock",
    "チンポ": "cock",
    "ちんぽ": "cock",
    "チンコ": "cock",
    "ペニス": "penis",
    "おっぱい": "breasts",
    "オッパイ": "breasts",
    "おしり": "butt",
    "お尻": "butt",
    "クリトリス": "clit",
    "アナル": "anal",
    "子宮": "womb",
    "膣": "vagina",
    "乳首": "nipples",
    "乳房": "breasts",
    "陰茎": "penis",
    "睾丸": "testicles",
    "金玉": "balls",
    "太もも": "thighs",
    "恥丘": "pubic mound",
}

# ── Sexual acts / states ────────────────────────────────────────
ACTS = {
    "セックス": "sex",
    "フェラ": "blowjob",
    "フェラチオ": "fellatio",
    "パイズリ": "titjob",
    "手コキ": "handjob",
    "クンニ": "cunnilingus",
    "中出し": "creampie",
    "顔射": "facial",
    "射精": "ejaculation",
    "絶頂": "climax",
    "オーガズム": "orgasm",
    "イク": "cumming",
    "いく": "cumming",
    "レイプ": "rape",
    "輪姦": "gang rape",
    "和姦": "consensual sex",
    "処女": "virgin",
    "童貞": "virgin",
    "妊娠": "pregnancy",
    "種付け": "impregnation",
    "搾乳": "milking",
    "潮吹き": "squirting",
    "媚薬": "aphrodisiac",
    "催眠": "hypnosis",
    "触手": "tentacle",
    "孕ませ": "impregnation",
    "寝取り": "cuckolding",
    "寝取られ": "netorare",
    "痴漢": "groping",
    "露出": "exhibitionism",
    "調教": "training",
    "奴隷": "slave",
    "淫乱": "lewd",
    "変態": "pervert",
    "エッチ": "lewd",
    "おかず": "fap material",
}

# ── RPG / game terms ────────────────────────────────────────────
RPG = {
    "勇者": "Hero",
    "魔王": "Demon Lord",
    "魔物": "monster",
    "冒険者": "adventurer",
    "ギルド": "guild",
    "ダンジョン": "dungeon",
    "宿屋": "inn",
    "酒場": "tavern",
    "武器屋": "weapon shop",
    "道具屋": "item shop",
    "防具屋": "armor shop",
    "教会": "church",
    "城": "castle",
    "王国": "kingdom",
    "魔法": "magic",
    "スキル": "skill",
    "経験値": "EXP",
    "レベルアップ": "level up",
    "ゴールド": "gold",
    "ポーション": "potion",
    "エリクサー": "elixir",
    "仲間": "companion",
    "装備": "equipment",
}

# ── Common expressions ──────────────────────────────────────────
EXPRESSIONS = {
    "くっ": "Kuh...",
    "ふふ": "Fufu",
    "うふふ": "Ufufu",
    "あはは": "Ahaha",
    "えへへ": "Ehehe",
    "くすくす": "Hehe",
    "ぐぬぬ": "Grr...",
    "きゃー": "Kyaa!",
    "やだ": "No way",
    "ちょっと": "Hey",
    "すごい": "Amazing",
    "やめて": "Stop it",
    "ダメ": "No",
    "だめ": "No",
    "お願い": "Please",
    "ありがとう": "Thank you",
    "ごめんなさい": "I'm sorry",
    "ごめんね": "Sorry",
    "バカ": "Idiot",
    "馬鹿": "Idiot",
    "うるさい": "Shut up",
    "助けて": "Help me",
    "気持ちいい": "It feels good",
    "いやらしい": "Naughty",
}

# ── All categories with labels ──────────────────────────────────
CATEGORIES = {
    "Body / Anatomy": BODY,
    "Sexual Acts": ACTS,
    "RPG Terms": RPG,
    "Common Expressions": EXPRESSIONS,
}


def get_all_defaults() -> dict:
    """Return all default glossary entries merged into one dict."""
    merged = {}
    for cat_entries in CATEGORIES.values():
        merged.update(cat_entries)
    return merged
