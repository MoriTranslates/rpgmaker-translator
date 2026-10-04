# Changelog

All notable user-facing changes to RPG Maker Translator are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The release workflow publishes the section whose heading matches the pushed tag
(`## [1.2.3]` for tag `v1.2.3`) as the GitHub Release notes.

## [Unreleased]

## [1.0.0] - Unreleased

First numbered release. Earlier builds were run from source only; from this
version on, a ready-to-run Windows download is attached to each GitHub Release.

### Added
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
