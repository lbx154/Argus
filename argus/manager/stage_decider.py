"""The Manager's stage decision: the ``stage`` form and its own rule.

The Manager is the SOLE authority over pipeline stage transitions. After the
Reviewer (and Planner) advise, it fills the ``stage`` form of ``hand_off``:
``to=team`` with a ``move`` (advance to a later stage, hold, or roll back to
an earlier one), ``to=done`` when the objective is complete, or
``to=manager`` to put a question the evidence cannot settle to the Manager's
own session, holding until it rules (:meth:`Manager.commit_stage_ruling`).
:func:`stage_check` refuses a move the stage machine cannot
make from here, so the model corrects it in the same turn; a turn that hands
nothing off holds.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass
class StageDecision:
    """The validated stage verdict from the Manager's model call."""

    action: str       # "advance" | "hold" | "rollback" | "complete"
    target_stage: str
    reason: str
    diagnostic: str = ""


def stage_check(
    payload: Mapping[str, Any], *, current_stage: str, stage_order: Sequence[str], allow_done: bool,
) -> StageDecision:
    """The stage form's own rule: advance only forward, roll back only backward, done only at the end."""
    cur, order, summary = current_stage, list(stage_order), payload["summary"]
    if payload["to"] == "manager":
        question = "\n\n".join(part for part in (summary, payload.get("note", "")) if part)
        return StageDecision("hold", cur, question, "manager_question")
    if payload["to"] == "done":
        if cur != order[-1] and not allow_done:
            raise ValueError(
                f"to=done only at the final stage `{order[-1]}`; to move on, use to=team with move=advance."
            )
        return StageDecision("complete", cur, summary, "valid_complete")
    move = payload.get("move")
    if move is None:
        raise ValueError("to=team needs move: advance, hold or rollback.")
    if move == "hold":
        return StageDecision("hold", cur, summary, "intentional_hold")
    at = order.index(cur) if cur in order else len(order)
    legal = order[at + 1:] if move == "advance" else order[:at]
    target = payload.get("target_stage") or (legal[0] if move == "advance" and legal else "")
    if target not in legal:
        raise ValueError(
            f"move={move}: target_stage must be one of: {', '.join(legal)}." if legal
            else f"move={move}: there is no stage to move to from `{cur}`."
        )
    return StageDecision(move, target, summary, "valid_target")


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


def completion_trigger_reason(action: str, reason: str) -> str:
    """Describe completion without preserving a contradictory hold reason."""
    text = str(reason or "").strip()
    if str(action or "").strip().lower() != "hold":
        return text
    return (
        "reviewer certified the final-stage checklist, overriding the Manager "
        f"hold ({text[:160] or 'no reason given'})"
    )


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
    """Validate a Manager COMPLETE decision against certified stage evidence."""
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
    reason = trigger_reason or "Manager completed the certified current stage"
    diagnostic = trigger_diagnostic or "manager_completion_certified"
    return StageDecision("complete", cur, reason, diagnostic)


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
    "stage_check",
    "completion_trigger_reason",
    "external_completion_gate_rework_decision",
    "external_completion_gate_stage_guard_decision",
    "final_stage_completion_blockers",
    "final_stage_completion_decision",
]
