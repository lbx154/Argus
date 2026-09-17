"""Recognize HTTP status diagnostics without matching unrelated numeric logs."""

from __future__ import annotations

import re
from collections.abc import Iterable

# Require an HTTP/status field or a recognizable status reason. Digit boundaries
# alone still match timestamps (.401Z), ordinals (401) and metrics (mse=0.401).
_STATUS_FIELD = re.compile(
    r"\b(?:http(?:/\d+(?:\.\d+)?)?(?:[ \t]+(?:error|status(?:[ _-]+code)?))?"
    r"|status(?:[ _-]?code)?|response[ \t]+code)"
    r"[ \t]*[\"']?[ \t]*[:=]?[ \t]*[\"']?"
    r"(?P<code>[1-5]\d{2})(?!\w|\.\d)",
    re.IGNORECASE,
)
_STATUS_REASON = re.compile(
    r"(?<![\w.])(?:"
    r"(?P<auth>401)[ \t]+(?:unauthorized|missing[ \t]+bearer)"
    r"|(?P<forbidden>403)[ \t]+forbidden"
    r"|(?P<limited>429)[ \t]+too[ \t]+many[ \t]+requests"
    r"|(?P<gateway>502)[ \t]+bad[ \t]+gateway"
    r"|(?P<unavailable>503)[ \t]+service[ \t]+unavailable"
    r"|(?P<timeout>504)[ \t]+gateway[ \t]+timeout"
    r")\b",
    re.IGNORECASE,
)


def has_http_status(value: object, statuses: Iterable[int]) -> bool:
    """Whether ``value`` contains a contextual HTTP status in ``statuses``."""
    text = str(value or "")
    wanted = {str(code) for code in statuses}
    return any(match.group("code") in wanted for match in _STATUS_FIELD.finditer(text)) or any(
        match.lastgroup is not None and match.group(match.lastgroup) in wanted
        for match in _STATUS_REASON.finditer(text)
    )
