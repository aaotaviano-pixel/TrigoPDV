"""Small text-safety helpers shared by external data and output adapters."""

from __future__ import annotations

import unicodedata
from typing import Any


_UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def replace_unsafe_controls(value: Any) -> str:
    """Replace controls and Unicode line separators while preserving accents.

    CR and LF therefore cannot create extra output lines when they come from a
    field.  The renderer remains responsible for adding its own line breaks.
    """

    text = "" if value is None else str(value)
    result: list[str] = []
    replacing_controls = False
    for character in text:
        if unicodedata.category(character) in _UNSAFE_CATEGORIES:
            if not replacing_controls:
                result.append(" ")
            replacing_controls = True
            continue
        result.append(character)
        replacing_controls = False
    return "".join(result)


def normalize_display_text(value: Any, limit: int | None = None) -> str:
    """Return NFC single-line text without unsafe Unicode separators."""

    text = unicodedata.normalize("NFC", replace_unsafe_controls(value)).strip()
    return text if limit is None else text[: max(0, int(limit))]
