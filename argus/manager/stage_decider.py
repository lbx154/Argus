"""The completion gate's rules: when a stage may close and the project be done.

One owner decides "stage ready" and "project done": the completion gate
(``Manager.decide_stage_transition`` in ``_stage_ops``). It reads evidence, never
a model's opinion: the accepted candidate, the vertical's stage completion
checks, the person's required steps (必经步骤) and the completion contract. The
Planner proposes; the gate's checks below decide.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass
class StageDecision:
    """The validated stage verdict from the Manager's model call."""

    action: str       # "advance" | "hold" | "rollback" | "complete"
    target_stage: str
    reason: str
    diagnostic: str = ""


def _review_certifies_completion(
    review: Any,
    *,
    vertical: str = "",
    mission_scope: str = "",
    research_target_level: str | None = None,
    checklist_contract: Any | None = None,
    research_result_scope: str = "",
) -> str:
    """Empty when this verdict may close the project; a reason otherwise.

    This used to discard every argument it took and return "" for any `done`
    review — a guard in shape only. Nothing enforced a vertical's research
    target here; what actually blocked a non-breakthrough "doctoral" result from
    completing was the unrelated `final_submission` scope check, which happened
    to reject every non-paper vertical for a different reason entirely.

    That accident was invisible until the scope check was corrected, at which
    point ten anti-fabrication tests in `test_math_vertical` went red. They were
    right: the protection they asserted was real, it just was not where anyone
    thought it was. The target is now checked on purpose.
    """
    _ = (mission_scope, checklist_contract)
    status = str(getattr(review, "status", "") or "").strip().lower()
    if status != "done":
        return "review_not_done"
    if str(vertical or "").strip().lower() == "research":
        # A paper's independent final review is the certification. The
        # structured grades the Reviewer attaches summarize that verdict; they
        # are not a second, mechanical judge of it. Formal verticals (math)
        # keep the grade check below because their evidence is machine-checked.
        return ""
    if research_target_level:
        from ..core.research_contract import research_completion_issue

        # Deliberately not forwarding ``mission_scope``. A ``bounded`` scope is
        # an escape in that checker — a bounded item certifies its own
        # acceptance criteria, not the project's research target — and that is
        # right for per-item checks. This call is the project-completion
        # decision, and every Goal Gate mission arrives here scoped ``bounded``
        # because the enqueue boundary downgrades it. Forwarding the scope would
        # therefore waive the target on exactly the decision it exists to guard.
        issue = research_completion_issue(
            getattr(review, "research_result", None),
            research_target_level=research_target_level,
            scope=research_result_scope,
        )
        if issue:
            return issue
    return ""


def final_stage_completion_decision(
    review: Any,
    *,
    current_stage: str,
    stage_order: Sequence[str],
    vertical: str = "",
    mission_scope: str = "",
    project_root: Any = None,
    research_target_level: str | None = None,
    checklist_contract: Any | None = None,
    completion_blocker: str = "",
    trigger_diagnostic: str = "",
    trigger_reason: str = "",
    allow_early_completion: bool = False,
) -> StageDecision | None:
    """The gate's completion, when certified stage evidence supports it; ``None`` otherwise."""
    cur = (current_stage or "").strip().lower()
    order = [str(s).strip().lower() for s in stage_order]
    if cur not in order or (cur != order[-1] and not allow_early_completion):
        return None
    if str(completion_blocker or "").strip():
        return None
    if not allow_early_completion and not _mission_scope_can_complete(
        mission_scope,
        vertical,
        project_root=project_root,
    ):
        return None
    missing = _review_certifies_completion(
        review,
        vertical=vertical,
        mission_scope=mission_scope,
        research_target_level=research_target_level,
        checklist_contract=checklist_contract,
        research_result_scope=(
            "idea_only"
            if allow_early_completion and cur == "idea" and vertical == "research"
            else ""
        ),
    )
    if missing:
        return None
    reason = trigger_reason or "the completion gate closed the certified current stage"
    diagnostic = trigger_diagnostic or "gate_completion_certified"
    return StageDecision("complete", cur, reason, diagnostic)


def required_steps_blocker(project_root: Any, judged_item_id: str = "") -> str:
    """The person's required steps (必经步骤) still open; "" with none, or no project root.

    The project is not done while a step the person laid on the canvas is not
    complete, unless they removed it (``core.waypoints``). ``judged_item_id``
    is the task whose accepted work is being judged: it is still running while
    the gate reads the backlog, and counts as delivered.
    """
    if not project_root:
        return ""
    from ..core.waypoints import CANVAS_FILENAME, open_steps_blocker

    root = Path(str(project_root))
    if not (root / CANVAS_FILENAME).is_file():
        return ""
    try:
        from ..life.memory import LifeMemory

        rows = LifeMemory.open(root).backlog.history()
    except Exception:  # noqa: BLE001 - unknown tasks leave every step open, which still blocks
        rows = []
    if judged_item_id:
        from dataclasses import replace

        rows = [replace(row, status="done") if row.id == judged_item_id else row for row in rows]
    return open_steps_blocker(root, rows)


def final_stage_completion_blockers(
    review: Any,
    *,
    current_stage: str,
    stage_order: Sequence[str],
    vertical: str = "",
    mission_scope: str = "",
    project_root: Any = None,
    research_target_level: str | None = None,
    checklist_contract: Any | None = None,
    completion_blocker: str = "",
    allow_early_completion: bool = False,
) -> tuple[str, ...]:
    """Why ``final_stage_completion_decision`` refused, in the operator's words.

    That function answers yes-or-no across four independent checks and returns
    a bare ``None`` for every no, so its caller could only report "Manager
    completion rejected by the project completion contract" — the same sentence
    whether the stage was simply not the last one, an external gate was open, a
    bounded mission had no standing to close the project, or the Reviewer had
    not certified. Testbed run 13 hit the first of those with the problem fully
    solved (search program, both-directions proof, Lean build with no ``sorry``,
    reviewer-certified) and its Planner responded by inventing gate-metadata
    busywork — "record the missing route/ledger state or equivalent gate
    metadata if required by the workflow" — because nothing told it the actual
    answer was "you are at ``scope``; advance".

    Deliberately a separate function rather than a changed return type: the
    decision is consumed as a truthy/``None`` value in several places, and a
    reporting improvement is not worth a signature migration across them. The
    checks are duplicated in the same order as above; the reviewer check
    already phrases its own reason, so that one passes through verbatim.

    Every failing check is reported, not just the first. The caller needs to
    distinguish "refused only because of where the pipeline is standing" — which
    is recoverable by advancing — from "refused for that *and* something else",
    which is not; a short-circuiting version cannot tell those apart.

    Returns an empty tuple when nothing blocks, which callers must treat as
    "no explanation available" rather than "completion is allowed" — the
    decision function remains the authority on that.
    """
    cur = (current_stage or "").strip().lower()
    order = [str(s).strip().lower() for s in stage_order]
    if cur not in order:
        return (f"stage {cur!r} is not part of this vertical's stage order",)
    blockers: list[str] = []
    if cur != order[-1] and not allow_early_completion:
        blockers.append(
            f"completion is only legal at the final stage ({order[-1]!r}); this "
            f"project is at {cur!r}. Advance through the remaining stages "
            f"({', '.join(order[order.index(cur) + 1:])}) instead — each one "
            "has its own completion bar on the way past"
        )
    blocker = str(completion_blocker or "").strip()
    if blocker:
        blockers.append(blocker)
    if not allow_early_completion and not _mission_scope_can_complete(
        mission_scope,
        vertical,
        project_root=project_root,
    ):
        blockers.append(
            f"a mission scoped {mission_scope or '(unset)'!r} cannot close a "
            f"{vertical or 'this'!r} project: its completion is certified, "
            "so only a 'final_submission' mission carries the authority to end it"
        )
    missing = str(
        _review_certifies_completion(
            review,
            vertical=vertical,
            mission_scope=mission_scope,
            research_target_level=research_target_level,
            checklist_contract=checklist_contract,
            research_result_scope=(
                "idea_only"
                if allow_early_completion and cur == "idea" and vertical == "research"
                else ""
            ),
        )
        or ""
    ).strip()
    if missing:
        blockers.append(missing)
    return tuple(blockers)


def external_completion_gate_rework_decision(
    review: Any,
    *,
    current_stage: str,
    stage_order: Sequence[str],
    project_root: Any,
) -> StageDecision | None:
    """Reopen an earlier stage when an external gate blocks final completion."""
    status = str(getattr(review, "status", "") or "").strip().lower()
    cur = (current_stage or "").strip().lower()
    order = [str(stage).strip().lower() for stage in stage_order]
    if status != "done" or not order or cur != order[-1]:
        return None
    from ..core.external_completion_gate import (
        external_completion_gate_issue,
        external_completion_rework_stage,
    )

    issue = external_completion_gate_issue(project_root)
    target = external_completion_rework_stage()
    if not issue or target not in order[:-1]:
        return None
    return StageDecision(
        "rollback",
        target,
        f"{issue}; reopen {target} for additional work",
        "external_completion_gate_rework",
    )


def external_completion_gate_stage_guard_decision(
    review: Any,
    proposed: StageDecision,
    *,
    current_stage: str,
    stage_order: Sequence[str],
    project_root: Any,
) -> StageDecision:
    """Keep work at/below the configured rework stage until the gate passes."""
    status = str(getattr(review, "status", "") or "").strip().lower()
    cur = (current_stage or "").strip().lower()
    order = [str(stage).strip().lower() for stage in stage_order]
    if status != "done" or cur not in order:
        return proposed
    from ..core.external_completion_gate import (
        external_completion_gate_issue,
        external_completion_rework_stage,
    )

    issue = external_completion_gate_issue(project_root)
    target = external_completion_rework_stage()
    if not issue or target not in order:
        return proposed
    cur_idx = order.index(cur)
    target_idx = order.index(target)
    if cur_idx > target_idx:
        return StageDecision(
            "rollback",
            target,
            f"{issue}; reopen {target} for additional work",
            "external_completion_gate_rework",
        )
    if cur_idx == target_idx and proposed.action in {"advance", "complete"}:
        return StageDecision(
            "hold",
            cur,
            f"{issue}; remain in {target} until the external outcome passes",
            "external_completion_gate_stage_ceiling",
        )
    return proposed


def _mission_scope_can_complete(
    mission_scope: str,
    vertical: str,
    *,
    project_root: Any = None,
) -> bool:
    """Whether a mission with this scope is allowed to close the project.

    ``final_submission`` is the *paper* transport scope and nothing else can
    complete a paper project: a bounded sub-mission must not end a submission
    just because its own Reviewer said `done`.

    Other verticals close through their declared completion gate, not the paper
    transport scope. The certification check still applies; this function only
    decides which mission envelope may carry the verdict.
    """
    normalized = (mission_scope or "").strip().lower().replace("-", "_")
    if normalized == "final_submission":
        return True
    try:
        from ..verticals._base import load_vertical_contract

        gate = load_vertical_contract(vertical or "", project_root=project_root).completion_gate
    except Exception:  # noqa: BLE001 — an unreadable vertical keeps the strict rule
        return False
    return gate != "certified"


__all__ = [
    "StageDecision",
    "external_completion_gate_rework_decision",
    "external_completion_gate_stage_guard_decision",
    "final_stage_completion_blockers",
    "required_steps_blocker",
    "final_stage_completion_decision",
]
