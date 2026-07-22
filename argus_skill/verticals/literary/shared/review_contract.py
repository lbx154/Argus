"""Shared literary-vertical REVIEW contract — the structured finding payload every literary
reviewer emits and the revise stage consumes.

Complementary to the framework reviewer ENVELOPE
(:mod:`argus_skill.reviewer`, ``reviewer_schema.json``), which carries the round
OUTCOME (status / next_action / checklist). This module carries the domain
FINDINGS: a ``verdict`` plus a typed, severity-tagged, evidence-located,
fix-carrying list. The existing framework Reviewer ROLE is reused unchanged —
this is ONLY the payload contract its literary output must satisfy, not a new
reviewer agent.

The full loop this module makes enforceable, at run time (via a vertical's
STAGE_CHECKS) and in tests:

    reviewer text -> extract_review (JSON, never silent) -> normalize_review
    (defaults + structural schema + semantic rules) -> revision_plan (ordered
    instructions) -> assert_plan_covers (the revise output must address every
    blocking finding and preserve its must_not_break invariants)

``severity`` (critical|major|minor|note) and ``blocking`` are DECOUPLED: severity
is the importance axis, blocking is the independent gate axis, so a serious but
non-gating craft note (critical, non-blocking) and a hard continuity
contradiction (blocking) are both expressible without one field being redundant.
``type`` is a free string; each vertical owns its finding VOCABULARY (fiction
continuity types, poetry prosody types) and passes it as ``type_vocabulary``.
Explicit extension policy: reject an unknown type when a vocabulary is supplied;
accept any non-empty type when none is.

Semantic rules the JSON schema cannot express, all enforced by
:func:`validate_review`:

* if ANY finding is ``blocking``, ``verdict`` MUST be ``"revise"`` — a blocking
  finding can never coexist with a ``"done"`` verdict;
* if there are NO findings, ``verdict`` MUST be ``"done"`` — you cannot request a
  revision with nothing to act on.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import jsonschema

_SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def _load_schema(name: str) -> dict[str, Any]:
    with (_SCHEMA_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


REVIEW_SCHEMA: dict[str, Any] = _load_schema("review.schema.json")

#: Importance order used to rank the revision plan within each blocking class.
SEVERITIES: tuple[str, ...] = ("critical", "major", "minor", "note")
_SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}

_JSON_FENCE = re.compile(r"```(?:json)?")


class ReviewError(ValueError):
    """Raised when a review payload is structurally or semantically invalid."""


def validate_review(review: dict[str, Any], *,
                    type_vocabulary: Iterable[str] | None = None) -> None:
    """Structural + semantic validation of a normalized review.

    Structural: JSON schema (verdict enum, required finding fields, severity
    enum, no stray keys). Semantic: blocking<->verdict coherence, empty<->done
    coherence, and (when supplied) the per-vertical type vocabulary.
    """
    try:
        jsonschema.validate(review, REVIEW_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ReviewError(f"invalid literary_review: {exc.message}") from exc

    findings = review["findings"]
    has_blocking = any(f["blocking"] for f in findings)
    if has_blocking and review["verdict"] != "revise":
        raise ReviewError(
            "a blocking finding stands but verdict is not 'revise' — a blocking "
            "finding can never coexist with a 'done' verdict"
        )
    if not findings and review["verdict"] != "done":
        raise ReviewError(
            "no findings but verdict is not 'done' — cannot request a revision "
            "with nothing to act on"
        )

    if type_vocabulary is not None:
        vocab = set(type_vocabulary)
        for f in findings:
            if f["type"] not in vocab:
                raise ReviewError(
                    f"finding {f['id']!r}: unknown type {f['type']!r} "
                    f"(vocabulary: {sorted(vocab)})"
                )


def normalize_review(raw: dict[str, Any], *,
                     type_vocabulary: Iterable[str] | None = None) -> dict[str, Any]:
    """Fill per-finding defaults (``must_not_break``, ``violated_constraint``),
    then validate. Returns a new dict; the input is not mutated."""
    if not isinstance(raw, dict):
        raise ReviewError("literary_review must be a JSON object")
    review = dict(raw)
    review.setdefault("findings", [])
    if not isinstance(review["findings"], list):
        raise ReviewError("literary_review.findings must be an array")
    norm: list[dict[str, Any]] = []
    for f in review["findings"]:
        if not isinstance(f, dict):
            raise ReviewError("each finding must be an object")
        g = dict(f)
        # Real models routinely emit an integer finding id (1, 2, ...) though the
        # contract types it as a string; coerce deterministically rather than
        # reject an otherwise-valid, evidence-bearing review.
        if isinstance(g.get("id"), (int, float)) and not isinstance(g.get("id"), bool):
            g["id"] = str(g["id"])
        g.setdefault("must_not_break", [])
        g.setdefault("violated_constraint", "")
        norm.append(g)
    review["findings"] = norm
    validate_review(review, type_vocabulary=type_vocabulary)
    return review


def extract_review(text: str, *,
                   type_vocabulary: Iterable[str] | None = None) -> dict[str, Any]:
    """Extract the JSON review object from reviewer output and validate it.

    A missing or malformed JSON object raises :class:`ReviewError` — it is NEVER
    silently accepted as an empty/passing review.
    """
    if not isinstance(text, str):
        raise ReviewError("reviewer output must be a string")
    cleaned = _JSON_FENCE.sub("", text)
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not m:
        raise ReviewError("no JSON object found in reviewer output")
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        raise ReviewError(f"reviewer output is not valid JSON: {exc}") from exc
    return normalize_review(obj, type_vocabulary=type_vocabulary)


def blocking_findings(review: dict[str, Any]) -> list[dict[str, Any]]:
    """The subset of findings that block the mission from finishing."""
    return [f for f in review["findings"] if f["blocking"]]


def revision_plan(review: dict[str, Any]) -> list[dict[str, Any]]:
    """Ordered revise instructions consumed by a vertical's revise stage.

    Blocking findings first, then by descending severity. Each instruction
    carries the location, the suggested fix, and the ``must_not_break``
    invariants the revise stage must preserve while fixing.
    """
    ordered = sorted(review["findings"],
                     key=lambda f: (not f["blocking"], _SEV_RANK.get(f["severity"], 99)))
    return [
        {
            "finding_id": f["id"],
            "type": f["type"],
            "severity": f["severity"],
            "blocking": f["blocking"],
            "location": f["location"],
            "suggested_action": f["suggested_action"],
            "must_not_break": list(f.get("must_not_break", [])),
        }
        for f in ordered
    ]


def assert_plan_covers(review: dict[str, Any], plan: Any) -> None:
    """Raise :class:`ReviewError` unless ``plan`` faithfully derives from ``review``.

    Every BLOCKING finding in the review must appear in the plan (by
    ``finding_id``) and the plan entry must preserve that finding's
    ``must_not_break`` invariants. This is the consumer-side gate: a revise step
    that silently drops a blocking finding — or loses an invariant it was told
    not to break — fails, rather than being accepted as "revised".
    """
    if not isinstance(plan, list):
        raise ReviewError("revision_plan must be a JSON array of instructions")
    by_id: dict[Any, dict[str, Any]] = {
        p.get("finding_id"): p for p in plan if isinstance(p, dict)
    }
    for f in review["findings"]:
        if not f["blocking"]:
            continue
        entry = by_id.get(f["id"])
        if entry is None:
            raise ReviewError(
                f"revision_plan drops blocking finding {f['id']!r} "
                f"({f['type']}) — every blocking finding must be addressed"
            )
        required = set(f.get("must_not_break", []))
        present = set(entry.get("must_not_break", []) or [])
        missing = required - present
        if missing:
            raise ReviewError(
                f"revision_plan for {f['id']!r} loses must_not_break invariants "
                f"{sorted(missing)}"
            )


__all__ = [
    "REVIEW_SCHEMA",
    "SEVERITIES",
    "ReviewError",
    "validate_review",
    "normalize_review",
    "extract_review",
    "blocking_findings",
    "revision_plan",
    "assert_plan_covers",
]
