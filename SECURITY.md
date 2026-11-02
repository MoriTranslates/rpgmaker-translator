# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest  | Yes       |

## What the app sends and stores

RPG Maker Translator is a desktop application. There is no account, no
telemetry and no update check. What leaves your machine depends on the
translation provider you choose in Settings:

- **Ollama (Local)** (the default): game text is sent only to the Ollama
  server at the URL you set (`http://localhost:11434` by default). If you
  point that URL at another machine, the text goes there instead.
- **Cloud providers** (OpenAI, Google Gemini, DeepSeek, Anthropic, Custom):
  game text, the prompt, glossary terms and character names are sent to
  that provider's API and handled under its terms. Don't pick a cloud
  provider for text you don't want to share with that company. With
  **Custom**, your API key is sent to whatever URL you enter, so use an
  `https://` URL you trust.
- **EasyRPG Player** (RPG Maker 2000/2003 only): after an export the app
  offers to add EasyRPG Player to the game folder. Only if you agree does it
  download a fixed release from `easyrpg.org`. The archive and the
  executable are checked against pinned SHA-256 hashes before use.

**API keys** are stored in plain text in `_settings.json` next to the
application (`_settings.json.bak` too, if the settings file was ever found
corrupt). Anyone who can read that folder can read your keys. Don't share
or upload the app folder with that file in it, and use keys with spending
limits where the provider supports them.

## Your game files

- Game files are only read and written on your disk.
- The first export backs up the original files (for example `data_original/`)
  before anything is changed. Every later export rebuilds the game files from
  that backup, and **Restore Originals** puts the backup back.
- If the game is updated after your first export, the app notices the
  changed files and refreshes the backup from them instead of rolling the
  update back.
- Project state and autosaves (`_translation_state.json`,
  `_translation_autosave.json`) are written into the game folder.

## Local processes

- If the local Ollama isn't answering, the app can start one for you. It only
  stops or restarts an Ollama that it didn't start itself after asking you,
  because that also stops Ollama for any other program using it.
- "Open in RPG Maker" creates directory junctions through the Windows API.
  It doesn't run a shell command.

## Reporting a Vulnerability

If you discover a security issue, please report it by:

1. **Opening a GitHub issue** if the vulnerability is not sensitive
2. **Emailing the maintainer** if it involves sensitive details (check profile for contact)

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

We will respond within a reasonable timeframe and provide updates as the issue is investigated.

## Scope

Security concerns for this project are primarily:
- Path traversal when reading/writing game files or extracting archives
- Injection via malformed game data (JSON, binary formats, scripts)
- Commands or programs run on the user's machine
- Handling of API keys and of text sent to cloud providers
- Dependencies with known vulnerabilities
