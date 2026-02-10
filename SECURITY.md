# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest  | Yes       |

## Architecture

RPG Maker Translator is a **local desktop application**:
- All processing happens on your machine
- Translation uses a local Ollama server (no cloud API calls)
- No user data is collected, transmitted, or stored externally
- Game files are read from and written to local disk only

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
- Path traversal when reading/writing game files
- Injection via malformed game data (JSON parsing)
- Dependencies with known vulnerabilities

Since this is a local-only tool with no network authentication or remote data storage, the attack surface is limited to the local machine.
