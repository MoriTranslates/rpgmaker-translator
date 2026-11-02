# Changelog

All notable user-facing changes to RPG Maker Translator are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The release workflow publishes the section whose heading matches the pushed tag
(`## [1.2.3]` for tag `v1.2.3`) as the GitHub Release notes.

## [Unreleased]

## [1.1.1] - 2026-11-02

A safety release. Code reviews and tests on copies of real games found ways the app could damage
game folders, put translations in the wrong place or lose your review work. This release fixes the
problems listed below.

### Fixed

Game files and backups:

- **RPG Maker VX Ace: exporting no longer scrambles the game data.** The `.rvdata2` writer numbered
  objects differently from Ruby, so after any translated line the game's internal links pointed at
  the wrong objects (event commands turned into move routes or numbers). On a real game this hit
  most maps. Exports, the script plugins and the window scaler now write files the way Ruby does,
  and text the app can't decode is written back byte for byte.
- **Wolf RPG: Restore Originals could delete your save files.** For games that keep `BasicData/`
  and `MapData/` in the main folder, backup and restore now only touch those two folders, never
  the whole game folder.
- **No more half-written game files.** Every engine writes game files, backups and the VX Ace
  script patches safely. A crash, a full disk or a locked file leaves the old file in place
  instead of a broken one, and an interrupted first backup is never treated as complete.
- **Restore Originals is safe to interrupt.** A failed restore leaves the game as it was. If the
  app or PC stops in the middle of the final swap, the game folder is put back the next time the
  game is opened, exported or restored (RPG Maker MV/MZ, VX Ace, Kirikiri, TyranoScript; Wolf RPG
  on the next restore), and you are told. Leftover copies are kept aside, never deleted.
- **Restore Originals keeps files a game update added** instead of deleting them.
- **Game updates are no longer rolled back.** When you update a game after exporting with this
  version, the next export, or resuming the project, notices the changed files and puts them into
  the backup, including new files and TyranoScript's `lang.js` / `Config.tjs`. It used to copy the
  old files back over the update. (Backups made by 1.1.0 can't tell an update from the app's own
  output, so for those the old behaviour stays until the first export with this version.)

Translations in the right place:

- **RPG Maker MV/MZ: translations land on the right line.** Dialogue, scrolling text, comments
  and choices are written to the exact event and position they came from. Before, a line that
  appears in several events could get its translation in the wrong event and stay Japanese in
  the one you translated.
- **RPG Maker MV/MZ: messages kept in one Show Text command are exported.** Games translated or
  edited by other tools often keep a whole message, line breaks included, in one command. Those
  translations were silently skipped.
- **A translation is never written over text that changed.** Every engine that places translations
  by position (RPG Maker MV/MZ, VX Ace, 2000/2003, Wolf RPG, Ren'Py, Kirikiri, TyranoScript, CSV
  games, Crowd) now checks that the original is still there. After a game update or a hand edit,
  the game's new text is kept. Before, the old translation replaced the new line.
- **SRPG Studio:** two identical lines with different translations each keep their own
  translation (both used to get the last one).
- **Reverted lines no longer stay in English** in VX Ace, 2000/2003, Wolf RPG and Crowd games.

Honest reports:

- **"Export Complete" only when it really is**, for every engine. You now see "Export Incomplete"
  with the files and line counts when a file could not be written, or when translated lines
  could not be placed because the game's text changed or the line is gone. The advice matches
  the cause. The install package (patch zip) reports missing lines the same way.
- **Files that can't be read are listed** when you open or resume a project, instead of their text
  silently missing.

Your work is kept:

- **Re-opening a project after a game update picks up the changes**, for every engine. Lines are
  matched by their text, so lines that only moved keep their translation and Reviewed status. New
  lines are added, changed lines go back to untranslated, and lines that left the game are removed
  from the list. The old translations of changed and removed lines are kept in the save file. If
  most of your translated lines would change (probably the wrong game), you are asked first.
- **Ren'Py projects from earlier versions keep their work** after the dialogue numbering changed
  in this version (see below): saved projects are re-matched by text when they are opened.
- **Your reviewed lines are left alone.** Automatic clean-up, code repair and bulk grammar polish
  no longer change, clear or re-translate lines you marked Reviewed, and polish no longer turns
  Reviewed lines back into Translated. If you edit a line while the AI is still working on it,
  your edit wins.

Engines:

- **Wolf RPG Editor 3.x games are recognised (including `GamePro.exe`) and refused** with a clear
  message instead of being opened with garbled text and exported into broken files.
- **Ren'Py:** scripts in subfolders of `game/` are translated, and lines ending in `with …`,
  `nointeract` or `id …` are no longer skipped. `[player]`-style variables are protected from the
  AI, and stray `[` or `{` are escaped. A backslash or quote in a translation can no longer break
  the script line, and a character name containing `\"` is no longer cut short. Translations with
  a leftover `«CODE»` placeholder are rejected instead of written into the game.
- **RPG Maker MZ:** plugin settings that are fixed choices (`@type select`) or font names are no
  longer offered for translation or written back, including in existing projects. Translating them
  broke the plugin.
- **Wolf RPG:** file paths with spaces or brackets are no longer offered for translation, and
  export no longer fails when a `Data.wolf.bak` already exists.
- **Translator notes:** a scene-break line (`---`, `***`, `━━━`, `＊＊＊`…) no longer cuts off the
  rest of a translation.
- **Crowd:** curly quotes from the AI no longer turn into `"` characters that can break scripts.
- **CSV games:** a column of numbers or sound IDs next to the text is no longer mistaken for the
  English column and overwritten. The file is skipped and the export says so.
- **SRPG Studio:** numbers in the game data are no longer offered as text to translate.
- **Kirikiri:** bytes the app can't decode are kept as they are instead of becoming `?`.

### Changed

- Bulk grammar polish only polishes lines that aren't marked Reviewed.
- RPG Maker 2000/2003 export asks before adding EasyRPG Player to the game folder.
- Writing translations to the game no longer freezes the window on large games.
- Resuming a project checks the game for updates in the background, with a progress dialog.
- Ren'Py: lines ending in `with …`, `nointeract` or `id …` are now dialogue, so later lines of the
  same label are numbered differently. Saved projects are re-matched by text automatically.
- The "speaker processing" setting is saved with each project and used again when it is resumed.

### Security

- The app no longer force-kills every Ollama on your PC without asking. It only restarts the
  Ollama it started itself; restarting someone else's needs your OK. A remote Ollama that is down
  is reported instead of restarting a local one.
- EasyRPG Player is downloaded only with your consent, and checked against a pinned SHA-256 hash
  before it is used (the cached copy is re-checked too).
- "Open in RPG Maker" no longer runs a shell command, so folder names containing `&` can't run
  other commands.
- SECURITY.md now describes what is actually sent to cloud providers, that API keys are stored in
  plain text in `_settings.json` next to the app, and what the app downloads.

## [1.1.0] - 2026-11-01

A visual overhaul. Nothing about how translation works has changed; the app just looks and reads
like a modern tool now, with a palette taken from the logo ("candlelit study": ink navy, parchment
and candle amber) in both dark and light themes.

### Changed

- New look throughout: Lucide icons on menus, tabs and the file tree; flat tabs; no more boxed
  group frames; readable type and contrast (WCAG AA for text).
- One workflow bar: the old toolbar and pipeline strip are merged into a single stepper with a
  clear "Next" button. "Apply to Game" becomes the main button once export is the only step left.
- Main screen: the table now takes the full height, with the editor in a collapsible side pane.
  Ctrl+Enter in the English box saves and jumps to the next untranslated line.
- Rows show their status as a coloured stripe plus a shape (ring, dot, tick) instead of full-row
  colour fills, so they are calmer and readable for colour-blind users. English is shown larger
  than Japanese, File and Event are merged into one compact "Where" column, and dialogue lines
  show the speaker as a coloured name tag.
- One search box with filter pills replaces the row of dropdowns and checkboxes.
- New project header: game title, engine and translated / reviewed progress.
- Welcome page: logo, drop-a-folder area, step cards, engine badges and recent projects.
- Event Viewer and Translation Queue use the same side-editor layout and icons; empty tabs now
  explain themselves instead of showing a blank pane.
- Settings → Options is grouped into sections, each option with a one-line explanation.
- The GPU panel shows VRAM and GPU use as slim bars with the reading beside them.

### Fixed

- Large projects scroll and filter more smoothly (speaker names and display text are cached).

## [1.0.1] - 2026-10-30

A small maintenance release: a better default model for new installs, the new licence, and updated
dependencies.

### Changed

- Licence: now PolyForm Noncommercial 1.0.0 (free for any non-commercial use; no commercial use).
  v1.0.0 and earlier stay under the Business Source License 1.1. Added THIRD_PARTY_NOTICES.md with
  DazedMTLTool's MIT licence.

### Fixed

- A fresh install now defaults to the recommended model, Qwen 3.5 9B. It used to default to Qwen3 14B,
  which the model picker itself marks as too large for 12 GB cards.

### Updated

- Dependencies refreshed (openai, pydantic, certifi, PyQt6-sip, pyspellchecker and others) and
  CI actions updated.
- The README lists Wolf RPG Editor and CSV Game (both experimental) and has fresh screenshots.

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
