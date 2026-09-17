from __future__ import annotations

import hashlib
import os
import re
import unicodedata

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
    """Encode a logical identifier as one bounded, portable path component."""
    text = str(value)
    raw = text.encode("utf-8")
    if len(raw) > max_bytes:
        digest = hashlib.sha256(raw).hexdigest()
        return f"argus-id-{digest}"
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


def legacy_hashed_filename_components(value: str) -> tuple[str, ...]:
    text = str(value)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    components = [f"argus-id-{digest}", f"id-{digest}"]
    if (
        (text.startswith("~") or _LEGACY_HASHED_COMPONENT.fullmatch(text))
        and not any(char in text for char in "/\\\0")
    ):
        components.append(text)
    return tuple(components)

__all__ = [
    "legacy_hashed_filename_components",
    "normalized_logical_identifier",
    "portable_filename_component",
]
