"""literary_editor EDIT-DISCIPLINE layer — the deterministic machine checks.

These do NOT judge whether an edit is good or within its semantic mandate. They
only enforce facts that are mechanically decidable:

* **must_not_break** — every segment the operator/diagnosis marked must-keep must
  appear VERBATIM in the edited text; dropping one is a finding.
* **non-empty** — the edited text is not empty.

Whether the change exceeded a proofread, expansion, critique, or other mandate is
for a Reviewer comparing meaning and intent, not character counts.
"""
from __future__ import annotations

from typing import Any

#: Machine-decidable edit-discipline finding types.
EDIT_FINDING_TYPES: frozenset[str] = frozenset({
    "must_not_break", "empty",
})

#: Editing modes this vertical serves (all are Task Envelope modes that require a
#: source reference).
EDITOR_MODES: frozenset[str] = frozenset({
    "rewrite", "expand", "polish", "proofread", "critique",
})

class EditError(ValueError):
    """Raised when edit inputs are malformed."""


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _finding(ftype: str, detail: str) -> dict[str, Any]:
    return {"type": ftype, "severity": "blocking", "location": None, "detail": detail}


def check_edit(original: str, edited: str, mode: str,
               must_keep: list[str] | None = None) -> list[dict[str, Any]]:
    """Return findings for empty output or dropped explicit preserve constraints.

    ``must_keep`` are segments that must survive verbatim (whitespace-normalized).
    """
    if mode not in EDITOR_MODES:
        raise EditError(f"unknown editing mode {mode!r} (expected {sorted(EDITOR_MODES)})")
    findings: list[dict[str, Any]] = []

    if not (edited or "").strip():
        findings.append(_finding("empty", "edited text is empty"))
        return findings

    for seg in (must_keep or []):
        if _norm(seg) and _norm(seg) not in _norm(edited):
            findings.append(_finding(
                "must_not_break", f"must-keep segment dropped: {seg[:40]!r}"))

    return findings


def is_disciplined(original: str, edited: str, mode: str,
                   must_keep: list[str] | None = None) -> bool:
    return not check_edit(original, edited, mode, must_keep)


__all__ = [
    "EDIT_FINDING_TYPES", "EDITOR_MODES", "EditError",
    "check_edit", "is_disciplined",
]
