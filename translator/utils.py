"""Shared utility functions for the RPG Maker Translator."""


def event_prefix(entry_id: str) -> str:
    """Extract event prefix from entry ID (everything before the last segment).

    "CommonEvents.json/CE169(Name)/dialog_5" → "CommonEvents.json/CE169(Name)"
    "Map001.json/Ev3(EV003)/p0/dialog_5" → "Map001.json/Ev3(EV003)/p0"
    """
    idx = entry_id.rfind("/")
    return entry_id[:idx] if idx > 0 else ""


def extract_event_context(entry_id: str) -> str:
    """Extract the event context from an entry ID for display.

    Examples:
        "CommonEvents.json/CE169(リブパイズリ)/dialog_64" → "CE169"
        "Map001.json/Ev3(EV003)/p0/dialog_5"              → "Ev3/p0"
        "Troops.json/Troop5(ゴブリン)/p0/dialog_1"         → "Troop5/p0"
        "Actors.json/1/name"                                → "1"
        "Map001.json/displayName"                           → ""
    """
    parts = entry_id.split("/")
    if len(parts) < 3:
        return ""
    # Middle parts = event context (between filename and entry type)
    middle = parts[1:-1]
    # Strip event names in parentheses for brevity: CE169(リブパイズリ) → CE169
    cleaned = []
    for part in middle:
        paren = part.find("(")
        cleaned.append(part[:paren] if paren > 0 else part)
    return "/".join(cleaned)
