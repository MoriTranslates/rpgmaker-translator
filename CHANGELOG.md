# Changelog

All notable user-facing changes to RPG Maker Translator are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The release workflow publishes the section whose heading matches the pushed tag
(`## [1.2.3]` for tag `v1.2.3`) as the GitHub Release notes.

## [Unreleased]

## [1.0.0] - 2026-10-04

First numbered release. Earlier builds were run from source only; from this
version on, a ready-to-run Windows download is attached to each GitHub Release.

### Why this version — please upgrade

Every part of the app (translation engine, all game-format parsers, export,
interface and image translation) was reviewed line by line and roughly 200
bugs were fixed. A test suite of 400+ automated tests now runs a fake game
through the full translate → export pipeline on every change, so these bugs
can't quietly come back.

Several bugs in the old version could **lose your work or damage game files**.
If you used an earlier version, these are the ones that matter most:

- **Lost progress:** opening a second game could auto-save it *over the first
  game's save file*, wiping its translations.
- **Translations in the wrong game:** switching or closing a project while a
  batch was running could write the old game's translations into the new one.
- **Edits landing on the wrong line:** after filtering the table or switching
  events in the Event Viewer, typing could change a *different* entry than the
  one shown in the editor.
- **Crashes mid-batch:** the app could crash outright (losing anything since
  the last auto-save) when the glossary updated during a batch, or get stuck
  showing "translating" forever after an error.
- **Good English being "fixed" into bad English:** the clean-up step split
  correct words ("Understand" → "Under stand", "Nevertheless" → "Never the
  less") and erased valid translations of lines quoted with `"…"`.
- **Games that wouldn't start after Restore Originals** (MV/MZ with word wrap
  injected), and an `uninstall.bat` in exported patches that never restored
  anything.
- **Corrupted game files:** Wolf RPG database export was off by one byte per
  string and crashed partway; RPG Maker 2000/2003 treated its own backups as
  maps; Ren'Py, VX Ace, RPG Maker 2000/2003 and CSV exports could put
  translations on the wrong lines or drop the last line.
- **Image translation** placed text in the wrong spot on most images, could
  erase opaque title screens entirely, and overwrote original images without
  a backup when exporting more than one folder.
- **Security:** a malicious Kirikiri `.xp3` archive could write files outside
  the game folder when extracted.

The pre-overhaul code is preserved as the `legacy/v2` branch and the
`v2-pre-overhaul` tag if you ever need it, but it is no longer maintained.

### Upgrading from an older version
- Your saved projects (`_translation_state.json` / auto-saves) and
  `_settings.json` keep working — open them as usual. RPG Maker MV/MZ and
  Ren'Py projects are upgraded automatically the first time you load them
  (a backup of the old save is kept as `<name>.pre-v2.json`, and a message
  tells you how many entries were carried over).
- Lines that start with a character-name code (e.g. `\N[1]は剣を手に入れた！`)
  used to lose the name. They are now kept as one line; the upgrade message
  tells you how many to retranslate.
- Re-export ("Apply Translation to Game") once after upgrading so your game
  files get the corrected export.

### Added
- Welcome screen and Project › Open Recent for your last 10 games.
- Translate › Undo Last Bulk Change (Ctrl+Shift+Z) for Replace All, Apply
  Glossary, Word Wrap, Clean Up, Consistency Pass, Reset All and Mark Event
  Reviewed.
- A working light theme, clearer error messages, confirmation before
  destructive actions, and remembered window layout.
- Qwen 3.5 9B is now the recommended model in the model picker; Sugoi Ultra
  is listed as the alternative Japanese → English specialist.
- "Import from Game Folder" (pulling text from an English release of the same
  game) now matches lines reliably even when the English version adds or
  renames things.
- Help → About with the version number.
- Windows download: a portable `RPGMakerTranslator-<version>-win64.zip` on the
  Releases page. Extract it and run `RPGMakerTranslator.exe`, no Python install needed.
- Version number shown in the app and in the exe's Windows file properties.
- Unexpected errors are now caught: the project is auto-saved, a message box
  explains what happened, and the details are written to `_error.log`
  instead of the app closing without warning.

### Fixed
- **Translation quality and safety**
  - Leftover `«CODE»` placeholders and mangled variants (`<<CODE1>>`, `[CODE1]`,
    `« CODE 1 »`) are repaired or retried instead of leaking into the game.
  - Batch translation retries entries with leftover Japanese or malformed
    output one by one, and leaves them untranslated rather than writing bad text
    (for example `['a', 'b']`) into the game.
  - Ollama context size now fits the prompt, so long system prompts and large
    batches are no longer silently cut off.
  - Unfinished `<think>` reasoning blocks and trailing "Note:" commentary from
    the model are removed more reliably, while real dialogue that starts with
    "Note:" or contains separator lines is kept.
  - Quotes around a whole line are stripped only when the model added them, and
    correct pronoun hints are used when speaker names are already in English.
  - Stop/Cancel now also interrupts retries, variant generation and rate-limit waits.
  - Cloud providers: newer OpenAI models (gpt-5 / o-series) get the correct
    request parameters, and timeouts are reported clearly.
- **Export and game files**
  - Re-exporting after reverting entries restores the original text instead of
    leaving English from an earlier export.
  - Ren'Py, Kirikiri, TyranoScript, CSV and other text engines keep each file's
    original encoding, BOM and line endings, and multi-line entries are written
    back to the correct lines.
  - Kirikiri UTF-16 scripts and TyranoScript/Kirikiri engine detection fixed.
  - RPG Maker 2000/2003: dialogue longer than the original message box now
    flows into additional boxes instead of being cut off.
  - Crowd engine: characters the game's encoding can't display are replaced
    instead of breaking the export.
  - CSV games: the English column is only written when it is clearly empty, so
    real game data is never overwritten.
  - The translation splash screen works in encrypted MV/MZ games and in
    deployed `www/` folder layouts.
- **Image translation**: more reliable text detection on small images, no
  more rendering of untranslated or empty text, and the verify check no longer
  reports a pass when it could not check.
- **Post-processing**: fewer false "fixes" (word splitting no longer touches
  plugin commands or note tags in RPG Maker games; common phrases such as
  "every one of" are left alone).
- **Interface**: many stability fixes in the main window, translation table,
  event viewer, queue panel, settings, wizard and GPU monitor; background tasks
  no longer freeze or crash the window when they finish.
- A corrupt `_settings.json` is backed up to `_settings.json.bak` and the app
  starts with defaults instead of failing.
