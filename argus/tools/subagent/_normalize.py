"""Normalization helpers for model-supplied supervisor fields."""
from __future__ import annotations

import re

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

# Sentence/clause boundaries (English + CJK), plus newlines: a two-line note
# is two claims even without terminal punctuation. A dot BETWEEN two digits is
# a decimal point ("epoch 1.5", "reward 0.0"), never a sentence boundary.
_CLAUSE_DELIMITERS = re.compile(r"(?<!\d)\.|\.(?!\d)|[;。;!?!?\n\r]")


def _has_real_signal(low: str) -> bool:
    """True if a prefix-matched note still carries a real alarm — a contrast/
    alarm token. Prevents ``startswith()`` from swallowing "no anomaly ... but
    reward collapsed to zero". Fails SAFE: when unsure, treat as a real concern
    (stop the run)."""
    return any(t in low for t in _CONCERN_SIGNAL_TOKENS)


def _split_clauses(raw_low: str) -> list[str]:
    """Split a lowercased note into whitespace-normalized clauses."""
    return [
        " ".join(part.split())
        for part in _CLAUSE_DELIMITERS.split(raw_low)
    ]


def _is_reassuring_clause(clause: str) -> bool:
    """True only for a clause that IS a known "nothing to report" phrasing.

    Conservative on purpose: a clause that carries an alarm token, or that
    simply is not a recognized reassurance, counts as a REAL claim and keeps
    the whole note (fail-safe toward review, never toward silently swallowing
    an anomaly). Empty clauses (split artifacts like ``"!!"``) never veto.
    """
    if not clause:
        return True
    if _has_real_signal(clause):
        return False
    return clause in _EMPTY_CONCERNS or clause.startswith(_EMPTY_CONCERN_PREFIXES)


def _clean_concern(value: object) -> str:
    """Normalize a supervisor concern note; empty when nothing noteworthy.

    A non-empty concern now HALTS the run and opens a discussion, so the
    supervisor only fills it for a genuine stop-worthy anomaly. Treat the common
    "nothing to report" phrasings as empty so a healthy run is never stopped.
    """
    raw = str(value or "")
    text = " ".join(raw.split())
    low = text.lower().strip(".")
    if low in _EMPTY_CONCERNS:
        return ""
    # A reassuring opener earns a clause-by-clause review of the WHOLE note:
    # the note clears only when EVERY clause is itself a recognized
    # reassurance. "No anomalies. All good." clears; "No anomalies in the
    # harness; training is stable. Reward has stayed at 0.0 for 4000 steps"
    # keeps the pivot VERBATIM even without a contrast token, because
    # "reward has stayed ..." is not a recognized reassurance — fail-safe,
    # since the downstream LLM re-confirmation only runs on a non-empty
    # concern and owns the false positives. Splitting the RAW note preserves
    # newline boundaries the whitespace-collapse above would erase.
    if low.startswith(_EMPTY_CONCERN_PREFIXES):
        if all(_is_reassuring_clause(c) for c in _split_clauses(raw.lower())):
            return ""
    return text

