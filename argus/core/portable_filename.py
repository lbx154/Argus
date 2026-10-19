from __future__ import annotations

import os
import re
import unicodedata

# Names older releases gave overlong ids; a logical id spelled like one is
# encoded so it never aliases such a file.
_LEGACY_HASHED_COMPONENT = re.compile(r"(?:argus-)?id-[0-9a-f]{64}\Z", re.IGNORECASE)
_WINDOWS_RESERVED = frozenset({
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


def normalized_logical_identifier(value: object) -> str:
    """Canonical identity for durable logical ids across host boundaries."""
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def portable_filename_component(
    value: str,
    *,
    windows: bool | None = None,
    max_bytes: int = 120,
) -> str:
    """Encode a logical identifier as one bounded, portable path component.

    An identifier longer than ``max_bytes`` has no such component and is refused.
    """
    text = str(value)
    raw = text.encode("utf-8")
    if len(raw) > max_bytes:
        raise ValueError(f"identifier is longer than {max_bytes} bytes: {text[:40]!r}...")
    on_windows = os.name == "nt" if windows is None else windows
    stem = text.split(".", 1)[0].casefold()
    unsafe = (
        not text
        or text.startswith("~")
        or _LEGACY_HASHED_COMPONENT.fullmatch(text) is not None
        or any(char in text for char in "/\\\0")
        or (
            on_windows
            and (
                any(ord(char) < 32 or char in '<>:"|?*' for char in text)
                or text.endswith((" ", "."))
                or stem in _WINDOWS_RESERVED
            )
        )
    )
    if not unsafe:
        return text
    encoded = raw.hex()
    return f"~{encoded}"


def legacy_filename_components(value: str) -> tuple[str, ...]:
    """Raw names older releases stored an encoded-looking id under."""
    text = str(value)
    if (
        (text.startswith("~") or _LEGACY_HASHED_COMPONENT.fullmatch(text))
        and not any(char in text for char in "/\\\0")
    ):
        return (text,)
    return ()


__all__ = [
    "legacy_filename_components",
    "normalized_logical_identifier",
    "portable_filename_component",
]
