"""literary_editor EDIT-DISCIPLINE layer — the deterministic machine checks.

These do NOT judge whether an edit is good — they enforce that the edit RESPECTS
ITS MODE and its preserve list, which is mechanically decidable:

* **must_not_break** — every segment the operator/diagnosis marked must-keep must
  appear VERBATIM in the edited text; dropping one is a finding.
* **mode discipline** (by editing mode):
  * ``critique`` — a critique diagnoses, it does NOT rewrite: the edited text must
    equal the source (no silent edits under the guise of "just commenting");
  * ``proofread`` — fixes errors, does not rewrite: the edited text must stay highly
    similar to the source (>= threshold); a wholesale rewrite is a finding;
  * ``expand`` — must actually add material: the edited text must be longer.
* **non-empty** — the edited text is not empty (except critique, which mirrors source).

Whether the polish reads better, whether a fact was invented — live-reviewer.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

#: Machine-decidable edit-discipline finding types.
EDIT_FINDING_TYPES: frozenset[str] = frozenset({
    "must_not_break", "mode_discipline", "over_edit", "no_expansion", "empty",
})

#: Editing modes this vertical serves (all are Task Envelope modes that require a
#: source reference).
EDITOR_MODES: frozenset[str] = frozenset({
    "rewrite", "expand", "polish", "proofread", "critique",
})

#: proofread must preserve at least this similarity ratio to the source.
_PROOFREAD_MIN_SIMILARITY = 0.75


class EditError(ValueError):
    """Raised when edit inputs are malformed."""


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _finding(ftype: str, detail: str) -> dict[str, Any]:
    return {"type": ftype, "severity": "blocking", "location": None, "detail": detail}


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def check_edit(original: str, edited: str, mode: str,
               must_keep: list[str] | None = None) -> list[dict[str, Any]]:
    """Return blocking findings where ``edited`` violates edit discipline for ``mode``.

    ``must_keep`` are segments that must survive verbatim (whitespace-normalized).
    """
    if mode not in EDITOR_MODES:
        raise EditError(f"unknown editing mode {mode!r} (expected {sorted(EDITOR_MODES)})")
    findings: list[dict[str, Any]] = []

    if mode != "critique" and not (edited or "").strip():
        findings.append(_finding("empty", "edited text is empty"))
        return findings

    for seg in (must_keep or []):
        if _norm(seg) and _norm(seg) not in _norm(edited):
            findings.append(_finding(
                "must_not_break", f"must-keep segment dropped: {seg[:40]!r}"))

    if mode == "critique":
        if _norm(edited) != _norm(original):
            findings.append(_finding(
                "mode_discipline", "critique must not rewrite — edited text differs "
                "from the source (produce a diagnosis, not an edit)"))
    elif mode == "proofread":
        sim = similarity(original, edited)
        if sim < _PROOFREAD_MIN_SIMILARITY:
            findings.append(_finding(
                "over_edit", f"proofread became a rewrite (similarity {sim:.2f} < "
                f"{_PROOFREAD_MIN_SIMILARITY}) — fix errors, do not rewrite"))
    elif mode == "expand":
        if len(_norm(edited)) <= len(_norm(original)):
            findings.append(_finding(
                "no_expansion", "expand must add material — edited text is not longer "
                "than the source"))
    return findings


def is_disciplined(original: str, edited: str, mode: str,
                   must_keep: list[str] | None = None) -> bool:
    return not check_edit(original, edited, mode, must_keep)


__all__ = [
    "EDIT_FINDING_TYPES", "EDITOR_MODES", "EditError",
    "similarity", "check_edit", "is_disciplined",
]
