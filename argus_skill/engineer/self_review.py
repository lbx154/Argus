"""Helpers for preserving verbatim Engineer verification output."""

from __future__ import annotations

import re

_VERIFICATION_RE = re.compile(
    r"^## Verification \(verbatim\)\s*$\s*"
    r"```[^\n]*\n(?P<body>[\s\S]*?)```",
    flags=re.MULTILINE,
)


def verbatim_verification_output(message: str) -> str:
    """Return the last non-empty fenced verification block, if present."""
    blocks = [
        match.group("body").strip()
        for match in _VERIFICATION_RE.finditer(str(message or ""))
        if match.group("body").strip()
    ]
    return blocks[-1] if blocks else ""


__all__ = ["verbatim_verification_output"]
