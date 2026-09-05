"""Normalization helpers for model-supplied supervisor fields."""
from __future__ import annotations

_VALID_DECISIONS = {"continue", "early_stop", "save_checkpoint"}

_VALID_HEALTH = {"healthy", "degrading", "stuck", "diverging"}

_HEALTH_ALIASES = {
    "degraded": "degrading",
    "diverged": "diverging",
    "diverge": "diverging",
    "stalling": "stuck",
    "stalled": "stuck",
    "stall": "stuck",
    "ok": "healthy",
    "good": "healthy",
}

def _norm_decision(value: object) -> str:
    """Normalize a supervisor decision, defaulting to the safe ``continue``."""
    token = str(value).strip().lower().replace("-", "_")
    return token if token in _VALID_DECISIONS else "continue"

def _norm_health(value: object) -> str:
    """Normalize a health label, mapping common variants; else ``unknown``."""
    token = str(value).strip().lower().replace("-", "_")
    token = _HEALTH_ALIASES.get(token, token)
    return token if token in _VALID_HEALTH else "unknown"

def _coerce_bool(value: object, *, default: bool = False) -> bool:
    """Interpret a model-supplied JSON value as a boolean.

    ``bool("false")`` is ``True`` in Python, so a model that emits the *string*
    ``"false"`` would otherwise be read as true. Map the common textual forms
    explicitly; anything unrecognised falls back to ``default``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"true", "1", "yes", "y"}:
            return True
        if token in {"false", "0", "no", "n", ""}:
            return False
    return default

_EMPTY_CONCERNS = {
    "", "none", "n/a", "na", "null", "nil", "-", "no concern",
    "no concerns", "nothing", "no issues", "no issue",
}

_EMPTY_CONCERN_PREFIXES = (
    "no concern", "no issue", "nothing notewor", "nothing to report",
    "nothing of note", "all good", "all healthy", "looks healthy",
    "no anomal", "no problem",
)

# Contrast/alarm tokens that mark a "no anomaly ... BUT X" reassure-then-pivot
# note as a REAL concern despite the calm opener.
_CONCERN_SIGNAL_TOKENS = (
    "but ", "however", "though", "except", "warn", "fail", "error", "collaps",
    "crash", "regress", "degrad", "stuck", "diverg", "hack", "drop",
    "但", "不过", "然而", "却", "失败", "报错", "异常", "崩", "塌", "为零",
)


def _has_real_signal(low: str) -> bool:
    """True if a prefix-matched note still carries a real alarm — a contrast/
    alarm token. Prevents ``startswith()`` from swallowing "no anomaly ... but
    reward collapsed to zero". Fails SAFE: when unsure, treat as a real concern
    (stop the run)."""
    return any(t in low for t in _CONCERN_SIGNAL_TOKENS)


def _clean_concern(value: object) -> str:
    """Normalize a supervisor concern note; empty when nothing noteworthy.

    A non-empty concern now HALTS the run and opens a discussion, so the
    supervisor only fills it for a genuine stop-worthy anomaly. Treat the common
    "nothing to report" phrasings as empty so a healthy run is never stopped.
    """
    text = " ".join(str(value or "").split())
    low = text.lower().strip(".")
    if low in _EMPTY_CONCERNS:
        return ""
    # Prefix match clears ONLY when the whole note is that reassurance — NOT
    # "no anomaly ... but reward collapsed" (reassure-then-pivot real alarm),
    # which startswith() alone would have swallowed into "" and let the bad run
    # keep burning GPU.
    if low.startswith(_EMPTY_CONCERN_PREFIXES) and not _has_real_signal(low):
        return ""
    return text

