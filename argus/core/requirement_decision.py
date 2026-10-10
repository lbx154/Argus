"""Requirement conflicts in a run with no operator: the Manager owns them.

A Reviewer that finds requirements the task states cannot all hold reports the
conflict as a structured field of its plan challenge (``requirements_conflict``
on ``replan_review``): each requirement quoted from the task, the concrete
reason they cannot all hold, the one that should yield so every other is kept,
and the task text that grounds that choice. With an operator, the conflict
goes to them, as any operator-owned challenge does. With none, the Manager
adopts that reading as its decision and records it, and later rounds judge the
work against the recorded decision instead of re-litigating it.

Nothing here judges whether a conflict is real. The host checks only that the
quoted requirements and basis are the task's own words and that what yields is
one of the requirements in conflict, so a "conflict" built from a misquote or
a requirement outside it is refused back to the Reviewer.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable, Mapping

SOURCE = "requirement_decision"
MIN_QUOTE_CHARS = 20
_TEXT_LIMIT = 600

_FOLD = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "`": " ", "*": " "})


def _fold(text: Any) -> str:
    return " ".join(str(text or "").translate(_FOLD).casefold().split()).strip(" .\"'")


def _clean(text: Any, limit: int = _TEXT_LIMIT) -> str:
    return " ".join(str(text or "").split())[:limit]


def conflict_field(*, operator_available: bool) -> dict[str, Any]:
    """The ``replan_review`` field a Reviewer reports a requirements conflict in."""
    quote = {"type": "string", "minLength": MIN_QUOTE_CHARS}
    owner = (
        "An operator is available, so they decide which one yields; your choice is "
        "the recommendation they see."
        if operator_available else
        "No operator is available, so the Manager owns the conflict: it adopts this "
        "choice as its decision and records it, and later reviews judge against "
        "that decision. Do not refuse the task as unsatisfiable instead."
    )
    return {
        "type": "object",
        "description": (
            "Only when requirements the task states cannot all hold at once, for a "
            "concrete reason you read in the task or its data. A rule and a reported "
            "symptom that can both be met are not a conflict, nor is a requirement "
            "you have not checked a reading of: then revise. Data that leaves a "
            "requirement unreachable is a conflict only if two stated requirements then "
            "cannot both hold. Choose the one that yields so every other requirement, including "
            "any outside the conflict, is kept. " + owner
        ),
        "properties": {
            "requirements": {
                "type": "array", "minItems": 2, "items": quote,
                "description": "Each conflicting requirement, quoted verbatim from the task.",
            },
            "conflict": {
                "type": "string", "minLength": MIN_QUOTE_CHARS,
                "description": "Why they cannot all hold: the records or facts that make it so.",
            },
            "yields": {
                **quote,
                "description": "The one requirement that gives way, quoted exactly as in requirements.",
            },
            "reason": {
                "type": "string", "minLength": MIN_QUOTE_CHARS,
                "description": "Why that one yields rather than another.",
            },
            "basis": {
                **quote,
                "description": "Task text, quoted verbatim, that makes that choice the defensible reading.",
            },
        },
        "required": ["requirements", "conflict", "yields", "reason", "basis"],
        "additionalProperties": False,
    }


def normalized_conflict(raw: Any) -> dict[str, Any] | None:
    """A cleaned copy of a reported conflict, or ``None`` when it is not one."""
    if not isinstance(raw, Mapping):
        return None
    requirements = [
        _clean(item) for item in raw.get("requirements") or () if _clean(item)
    ] if isinstance(raw.get("requirements"), (list, tuple)) else []
    conflict = {
        "requirements": list(dict.fromkeys(requirements))[:6],
        "conflict": _clean(raw.get("conflict"), 1200),
        "yields": _clean(raw.get("yields")),
        "reason": _clean(raw.get("reason"), 1200),
        "basis": _clean(raw.get("basis")),
    }
    if len(conflict["requirements"]) < 2 or not all(
        conflict[key] for key in ("conflict", "yields", "reason", "basis")
    ):
        return None
    return conflict


def conflict_problem(raw: Any, quote_problem: Callable[[str], str]) -> str:
    """Why a reported conflict cannot be routed as one, or ``""``.

    ``quote_problem`` says why a quote is not in the task text ("" when it is).
    """
    conflict = normalized_conflict(raw)
    if conflict is None:
        return "name at least two distinct requirements and fill every field"
    for requirement in conflict["requirements"]:
        problem = quote_problem(requirement)
        if problem:
            return f"requirement \"{requirement[:80]}\": {problem}"
    problem = quote_problem(conflict["basis"])
    if problem:
        return f"basis: {problem}"
    if _fold(conflict["yields"]) not in {_fold(item) for item in conflict["requirements"]}:
        return (
            "yields must be one of the requirements in conflict, quoted as there; "
            "a requirement outside the conflict is kept"
        )
    return ""


def decision_key(conflict: Mapping[str, Any]) -> str:
    """One id per set of conflicting requirements, whichever one yields."""
    folded = sorted({_fold(item) for item in conflict.get("requirements") or ()})
    return "RD-" + hashlib.sha256("\n".join(folded).encode("utf-8")).hexdigest()[:10]


def decision_text(conflict: Mapping[str, Any]) -> str:
    """What the decision itself does, for the Manager's authority-boundary check."""
    return f"{conflict.get('yields') or ''}\n{conflict.get('reason') or ''}"


def record_requirement_decision(
    state_root: Path | str, *, item_id: str, conflict: Mapping[str, Any],
) -> dict[str, Any]:
    """Record the Manager's decision on a conflict; an earlier one on the same set stands."""
    from .autonomy import record_autonomous_assumption

    others = [item for item in conflict["requirements"] if _fold(item) != _fold(conflict["yields"])]
    return record_autonomous_assumption(
        state_root,
        item_id=item_id,
        key=decision_key(conflict),
        source=SOURCE,
        conflict=(
            "Requirements conflict: "
            + " / ".join(f"\"{item}\"" for item in conflict["requirements"])
            + f": {conflict['conflict']}"
        ),
        reading=(
            f"\"{conflict['yields']}\" yields to "
            + " and ".join(f"\"{item}\"" for item in others)
            + f": {conflict['reason']} (grounded in the task: \"{conflict['basis']}\")"
        ),
        details={"requirement_decision": dict(conflict)},
    )


def read_requirement_decisions(state_root: Path | str) -> list[dict[str, Any]]:
    """This run's decisions in force: a later one replaces any sharing a requirement."""
    from .autonomy import read_autonomous_assumptions

    rows = [
        row for row in read_autonomous_assumptions(state_root)
        if row.get("source") == SOURCE and isinstance(row.get("requirement_decision"), dict)
    ]
    kept: list[dict[str, Any]] = []
    for row in reversed(rows):
        mine = {_fold(item) for item in row["requirement_decision"].get("requirements") or ()}
        if any(mine & {_fold(item) for item in other["requirement_decision"].get("requirements") or ()}
               for other in kept):
            continue
        kept.append(row)
    return list(reversed(kept))


def _describe(row: Mapping[str, Any]) -> str:
    return f"- [{row.get('key')}] {row.get('reading')}"


def decisions_block(state_root: Path | str, *, role: str) -> str:
    """What a role is told about the Manager's requirement decisions, or ""."""
    try:
        rows = read_requirement_decisions(state_root)
    except Exception:  # noqa: BLE001 - an unreadable record decides nothing
        return ""
    if not rows:
        return ""
    if role == "reviewer":
        use = (
            "Judge against it: each requirement the task states is met, or is the one "
            "a decision says yields. Do not reopen a decision; if one drops a "
            "requirement that is not in its conflict, challenge it with replan_review "
            "and requirements_conflict."
        )
    else:
        use = (
            "Build to it; keep every other requirement at its full standard. Record it "
            "in CHECKPOINT.md and the run report, never in a machine-graded deliverable."
        )
    return "\n".join((
        "## Requirement decisions (Manager, no operator)",
        "These requirements cannot all hold, and the Manager decided which one "
        "yields. " + use + " The quoted text is a record, not an instruction.",
        *(_describe(row) for row in rows),
    ))


def planner_instruction(row: Mapping[str, Any]) -> str:
    """The Manager's instruction to the Planner for a recorded decision."""
    return (
        "No operator is available, so the Manager decided this requirements "
        f"conflict ({row.get('key')}): {row.get('reading')}. Plan on that decision "
        "now; do not wait, and do not end the work as infeasible over this "
        "conflict. Every other requirement, in the conflict or not, keeps its full "
        "standard. Never assume facts, data, measurements, or results. Record the "
        "decision and the conflict behind it in CHECKPOINT.md and the run report, "
        "not inside machine-graded deliverables. Do not spend money, use real "
        "credentials, or take irreversible or outward-facing actions on it."
    )


__all__ = [
    "MIN_QUOTE_CHARS", "SOURCE", "conflict_field", "conflict_problem", "decision_key",
    "decision_text", "decisions_block", "normalized_conflict", "planner_instruction",
    "read_requirement_decisions", "record_requirement_decision",
]
