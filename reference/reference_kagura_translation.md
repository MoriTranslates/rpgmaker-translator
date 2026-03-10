# Professional Translation Reference: Kagura Games — Hustle Battle Card Gamers

Source: `e:/Hgames/Hustle Battle Card Gamers/www/` (RPG Maker MV, 1280x720)
Team: 12 translators + 6 testers (Sinflower & Candiru tech)

## Display Configuration

- Font: ZenMaruGothic-Bold.ttf @ 33px, outline 4px
- Message window: 1048px wide (1280 - 240 for standing pictures + 8)
- Word wrap: **OFF** — all line breaks are manual
- Max visible text: ~65-70 chars/line (78 absolute max)
- Most dialogue: 1-2 lines per message box (80.9% single-line)

## Control Code Patterns

Codes go at LINE START, before dialogue text:
```
\AA[F]\FF[Lumina_10]\F[Itsuki_20]\NW[Lumina]Hey, are you okay?
```

| Code | Plugin | Purpose |
|------|--------|---------|
| `\NW[name]` | MPP_MessageEX_Op1 | Name window (speaker) |
| `\F[id]` | LL_StandingPictureMV | Standing picture (left) |
| `\FF[id]` | LL_StandingPictureMV | Standing picture (right) |
| `\AA[F/FF/FFF]` | LL_StandingPictureMV | Animation speed |
| `\M[motion]` | LL_StandingPictureMV | Character motion (left) |
| `\MM[motion]` | LL_StandingPictureMV | Character motion (right) |
| `\C[n]` | Standard RPG Maker | Text color |
| `\I[n]` | Standard RPG Maker | Inline icon |
| `\FH[ON/OFF]` | Custom | Font height toggle |

Speaker always via `\NW[name]` inline — 101 headers all say `(narrator)`.
Our parser should detect `\NW[name]` pattern for speaker extraction.

## Dialogue Style

- **No honorifics** — -san, -chan etc. dropped entirely
- **Contractions**: "don't", "can't", "I'm", "you're" — natural spoken English
- **Ellipsis**: `...` (three dots, not unicode `…`)
- **Stuttering**: `R-Rio!?`, `Wh-What` — capitalize after stutter dash
- **Internal monologue**: `(I...)` in parentheses
- **Sound effects**: `*Grab*!`, `*Thud*!` in asterisks
- **Heart**: `♥` directly in text for flirty/sexual lines
- **Highlighted terms**: `\C[17]Magic Monsters\C[0]` for in-game proper nouns
- **Character voice**: Yomi uses dialect ("ya", "'em", "somethin'", "doin'")

## Adult Content

- Direct, explicit vocabulary: "dick", "cock", "cum", "pussy", "boobs", "tits"
- No euphemisms or censoring — faithful to source
- Character personality maintained through H-scenes
- `♥` as vocal marker for flirtatious/sexual delivery

## Database Translation (Selective)

**Translated**: Actor names, item names/descriptions, system menu terms, plugin display params
**Left in Japanese**: Weapons, armors, classes, enemies, states, battle messages, switch/variable names

Pragmatic approach: only translate what players actually see.

## Item Description Format

```
HP: 30   ATK: 3   Cost: 4
Personal Skill: Roar [Activations: 3] When ATK is 4+, deal 2 damage to enemy.
```
Uses `\n` newlines and `\I[n]` icons inline.

## Stats (18,020 dialogue lines)

| Metric | With codes | Visible only |
|--------|-----------|-------------|
| Mean | 54.5 | 34.3 |
| Median | 53 | 33 |
| P90 | 92 | 63 |
| P95 | 97 | 66 |
| P99 | 105 | 69 |
| Max | 131 | 78 |

## Lessons for Our Pipeline

1. **Manual wrap is pro standard** — no WordWrap plugin. Target 65-70 visible chars.
2. **`\NW[name]` speaker detection** — add to our parser alongside `\N<name>`.
3. **1-2 lines per message box** — our word wrapper should target this.
4. **Selective translation** — skip unused DB fields (save LLM calls).
5. **Character voice via prompting** — give LLM character personality descriptions.
6. **Control codes at line start** — our placeholder system handles this well.
7. **`\C[n]` for proper nouns** — could auto-inject for glossary terms.
8. **No `\N[n]` actor refs** — pro translations spell out names, don't use variable refs.
