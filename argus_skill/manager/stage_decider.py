"""Manager stage-transition decision: prompt + strict parser.

The Manager is the SOLE authority over pipeline stage transitions. After the
reviewer (and planner) produce their structured feedback, the Manager
independently judges whether to ADVANCE to a later stage, HOLD on the current
one, or ROLL BACK to an earlier stage, then writes ``PIPELINE_STATE.json``. The prompt body lives in ``roles.prompts.manager`` and is re-exported here for
source compatibility; this module owns the strict parser for its JSON verdict.

Fail-closed everywhere: any ambiguity in the model's answer (bad JSON, unknown
action, an advance target that is not a later stage, a rollback
target that is not strictly earlier) parses to HOLD. The Manager therefore never
silently advances on a malformed verdict.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

from ..roles.prompts.manager import build_stage_decision_prompt


@dataclass
class StageDecision:
    """The parsed + validated stage verdict from the Manager's model call."""

    action: str       # "advance" | "hold" | "rollback" | "complete"
    target_stage: str
    reason: str
    diagnostic: str = ""
    # Planner-wait reconciliation only: an authoritative HOLD may keep the
    # current stage while surfacing pre-existing operator authority or changed
    # evidence that satisfies the Planner's declared recheck condition. Manager
    # cannot create or expand operator authorization.
    resolves_wait: bool = False


_VALID_ACTIONS = ("advance", "hold", "rollback", "complete")

#: Opening of the "you are not at the last stage" refusal. Kept as a constant so
#: ``stage_position_is_the_only_completion_blocker`` can recognise its own
#: message without the sentence leaking into another module.
_STAGE_POSITION_BLOCKER = "completion is only legal at the final stage"


def extract_answer(result: Any) -> str:
    """Pull the model's reply text out of a RunnerResult-shaped object.

    Mirrors ``life.router._extract_answer`` (``last_agent_message`` then the last
    of ``agent_messages``).
    """
    msg = getattr(result, "last_agent_message", None)
    if not msg:
        msgs = getattr(result, "agent_messages", None) or []
        msg = msgs[-1] if msgs else ""
    return str(msg or "")


def _loads_first_json(text: str) -> tuple[Any, str]:
    cleaned = (text or "").strip()
    if not cleaned:
        return None, "empty_output"
    # Strip a leading/trailing markdown code fence if present.
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned), "json"
    except Exception:  # noqa: BLE001 — fall through to brace extraction
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None, "no_json_object"
    try:
        return json.loads(cleaned[start : end + 1]), "json_extracted"
    except Exception:  # noqa: BLE001
        return None, "malformed_json"


def _normalized_stage_label(value: Any) -> str:
    """Normalize harmless target-stage decoration without guessing semantics."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("`", "")
    text = text.strip(" \t\r\n'\"")
    text = re.sub(r"\s+", " ", text)
    if text.startswith("the "):
        text = text[4:].strip()
    if text.endswith(" stage"):
        text = text[: -len(" stage")].strip()
    return text


_STAGE_KEYS = (
    "ACTION",
    "TARGET_STAGE",
    "REASON",
    "RESOLVES_WAIT",
    "LIVE_VIEW_PATHS",
    "LIVE_VIEW_TITLE",
    "LIVE_VIEW_REASON",
)


def stage_decision_fields(raw_text: str) -> tuple[Any, str]:
    """The Manager's stage verdict, read from named lines in whatever it wrote.

    Operator directive: no role is forced to emit a JSON Schema. The Manager
    explains its reasoning in prose and states the verdict on named lines; every
    validation below is unchanged, only the step that obtains the fields moved.

    Returns ``(fields, diagnostic)`` with the same diagnostic vocabulary the
    JSON loader used, so the fail-closed HOLD path keeps reporting *why* a reply
    was unusable rather than collapsing every failure into one label.

    A volunteered JSON object is still read. Sixteen daemons are mid-flight on
    the older prompt and refusing theirs would have made this a breaking change
    for every run in progress.
    """
    from ..core.role_reply import (
        read_bool,
        read_key_values,
        read_list,
        read_optional,
    )

    values = read_key_values(raw_text, _STAGE_KEYS)
    if not values:
        obj, diagnostic = _loads_first_json(raw_text)
        return obj, diagnostic

    fields: dict[str, Any] = {}
    for key in ("ACTION", "TARGET_STAGE", "REASON"):
        if key in values:
            fields[key.lower()] = read_optional(values, key)
    if "RESOLVES_WAIT" in values:
        fields["resolves_wait"] = read_bool(values, "RESOLVES_WAIT")
    paths = read_list(values, "LIVE_VIEW_PATHS")
    if paths:
        fields["live_view"] = {
            "paths": list(paths),
            "title": read_optional(values, "LIVE_VIEW_TITLE"),
            "reason": read_optional(values, "LIVE_VIEW_REASON"),
        }
    elif "LIVE_VIEW_PATHS" in values:
        # An explicit empty answer means "clear the panel", which the caller
        # distinguishes from never having been asked.
        fields["live_view"] = None
    return fields, "named_lines"


def parse_stage_decision(
    raw_text: str,
    *,
    current_stage: str,
    stage_order: Sequence[str],
) -> StageDecision:
    """Validate the model's JSON verdict; fail-closed to HOLD on any ambiguity.

        Rules:
            * ``action`` must be one of advance/hold/rollback/complete (else HOLD);
            * ADVANCE ``target_stage`` must be strictly LATER in ``stage_order``;
      * ROLLBACK ``target_stage`` must be strictly EARLIER than ``current_stage``
        (else HOLD);
            * COMPLETE targets the current stage; a LATER stage becomes a
        one-step ADVANCE (completion is only legal at the final stage), an
        earlier or unknown one is rejected;
      * HOLD pins ``target_stage`` to the current stage.

    A COMPLETE that survives this function is a request to *close the project*,
    which is a policy question rather than a parsing one — see
    :func:`final_stage_completion_blockers` and
    :func:`stage_position_is_the_only_completion_blocker` for what happens when
    the only thing wrong with it is where the pipeline is standing.
    """
    cur = (current_stage or "").strip().lower()
    order = [str(s).strip().lower() for s in stage_order]
    hold = StageDecision("hold", cur, "manager held (default)", "default_hold")

    obj, load_diagnostic = stage_decision_fields(raw_text)
    if not isinstance(obj, dict):
        diagnostic = (
            "non_object_json"
            if load_diagnostic in {"json", "json_extracted"}
            else load_diagnostic
        )
        return StageDecision(hold.action, hold.target_stage, hold.reason, diagnostic)
    action = str(obj.get("action") or "").strip().lower()
    if action not in _VALID_ACTIONS:
        return StageDecision("hold", cur, "manager held (default)", "unknown_action")
    reason = str(obj.get("reason") or "").strip()
    resolves_wait = obj.get("resolves_wait") is True
    raw_target = obj.get("target_stage")
    target = _normalized_stage_label(raw_target)

    if action == "hold":
        return StageDecision(
            "hold",
            cur,
            reason or "manager held",
            "intentional_hold",
            resolves_wait,
        )

    if cur not in order:
        return StageDecision(
            "hold", cur, "manager held (default)", "unknown_current_stage"
        )  # cannot validate ordering → safe HOLD

    cur_idx = order.index(cur)
    if action == "complete":
        # A COMPLETE naming a LATER stage is the model saying "everything
        # through X is done". Testbed runs 11, 12 and 13 all emitted
        # ``ACTION=complete`` / ``TARGET_STAGE=review`` at ``current_stage=scope``
        # with correct reasoning behind it — run 13 had by then produced the
        # survey, a both-directions proof, and a Lean build with no ``sorry``,
        # all reviewer-certified.
        #
        # Pinning that to ``complete`` at ``cur`` does NOT rescue it. Completion
        # is only legal at the final stage: ``final_stage_completion_decision``
        # returns ``None`` for any earlier one (outside ``direct`` workflow
        # mode), and the caller turns that into a HOLD. So the earlier
        # normalization to ``complete@cur`` traded one HOLD for another and run
        # 13 sat at ``scope`` with the problem solved, its Planner inventing
        # gate-metadata busywork to explain the refusal.
        #
        # ADVANCE is the action that expresses what the model meant and that
        # the machine can execute. One step, not a jump to ``target``:
        # ``advance_stage`` validates the stage being *left*, so hopping
        # ``scope -> review`` would skip ``solve``'s gate entirely. Stepping
        # converges in as many ticks as there are stages and every gate still
        # runs — including the vertical's deterministic completion validator,
        # which is what raises ``StageCompletionError`` from ``_advance``.
        #
        # An EARLIER or unknown target stays fail-closed: that is a model
        # confusing completion with a rollback, not a wording slip.
        if target and target != cur:
            if target not in order or order.index(target) < cur_idx:
                return StageDecision(
                    "hold", cur, "manager held (default)", "illegal_complete_target"
                )
            return StageDecision(
                "advance",
                order[cur_idx + 1],
                reason or "operator objective complete",
                "complete_target_advanced",
            )
        # Same rescue, for the model that did as it was told. The stage prompt
        # ends with "For HOLD and for COMPLETE, set TARGET_STAGE to the current
        # stage" — added so runs 11 and 12 would stop losing correct verdicts to
        # ``illegal_complete_target``. A Manager that follows it lands here, and
        # completion from a non-final stage is refused downstream every time.
        #
        # It is NOT rewritten here. Naming a later stage says "the work through
        # X is done", which is a request to move and can be settled on shape
        # alone. Naming the current stage says "close the project", which is a
        # request the completion contract exists to answer. So it goes through,
        # and the caller turns it into a step forward only when the contract's
        # sole objection is the pipeline's position — see
        # ``stage_position_is_the_only_completion_blocker``.
        return StageDecision(
            "complete",
            cur,
            reason or "operator objective complete",
            "valid_complete",
        )

    if action == "advance":
        nxt_idx = cur_idx + 1
        if nxt_idx >= len(order):
            return StageDecision("hold", cur, "manager held (default)", "no_next_stage")
        next_stage = order[nxt_idx]
        if not target and order.count(next_stage) == 1:
            return StageDecision(
                "advance",
                next_stage,
                reason or "checklist satisfied",
                "inferred_next_stage",
            )
        if target not in order or order.index(target) <= cur_idx:
            return StageDecision(
                "hold", cur, "manager held (default)", "illegal_advance_target"
            )
        diagnostic = (
            "normalized_target_stage"
            if raw_target != target
            else "valid_skip_target"
            if target != next_stage
            else "valid_target"
        )
        return StageDecision(
            "advance", target, reason or "checklist satisfied", diagnostic
        )

    # rollback
    if not target:
        return StageDecision(
            "hold", cur, "manager held (default)", "missing_rollback_target"
        )
    if target not in order or order.index(target) >= cur_idx:
        return StageDecision(
            "hold", cur, "manager held (default)", "illegal_rollback_target"
        )  # must be strictly earlier
    diagnostic = "normalized_target_stage" if raw_target != target else "valid_target"
    return StageDecision(
        "rollback", target, reason or "upstream evidence unreliable", diagnostic
    )


def fallback_empty_stage_decision(
    review: Any,
    *,
    current_stage: str,
    stage_order: Sequence[str],
    checklist_contract: Any | None = None,
) -> StageDecision:
    """Fail closed when the Manager returned no stage judgment."""
    cur = (current_stage or "").strip().lower()
    order = [str(s).strip().lower() for s in stage_order]

    def hold(
        diagnostic: str,
        reason: str = "manager held after empty output",
    ) -> StageDecision:
        return StageDecision("hold", cur, reason, diagnostic)

    if cur not in order:
        return hold("empty_output_unknown_current_stage")
    _ = review, checklist_contract
    return hold("empty_output_no_manager_judgment")


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
    _ = (vertical, mission_scope, checklist_contract)
    status = str(getattr(review, "status", "") or "").strip().lower()
    if status != "done":
        return "review_not_done"
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
            f"{_STAGE_POSITION_BLOCKER} ({order[-1]!r}); this "
            f"project is at {cur!r}. Advance through the remaining stages "
            f"({', '.join(order[order.index(cur) + 1:])}) instead — each one "
            "runs its own completion gate on the way past"
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
            f"{vertical or 'this'!r} project: its completion gate is "
            "'certified', so only a 'final_submission' mission carries the "
            "authority to end it"
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


def stage_position_is_the_only_completion_blocker(
    blockers: Sequence[str],
) -> bool:
    """Is the pipeline's position the sole reason completion was refused?

    Run 15 (``s-f0dbba19``) is why this exists. Its Manager emitted
    ``ACTION=complete`` / ``TARGET_STAGE=scope`` — exactly what the stage prompt
    instructs — with "Reviewer certification establishes the scoped objective
    and all requested dependent phases", after a reproducible survey, a
    both-directions proof, and a Lean/Mathlib build this repository recompiled
    independently. The one objection was that ``scope`` is not the last stage.
    It held there, as runs 11, 12 and 13 had, and the only escape any agent ever
    found was to force the gate by hand.

    An action the prompt tells the model to emit and the machine can only ever
    refuse is not a guardrail. When position is the *sole* objection, stepping
    forward is what the verdict means and what the machine can execute, and no
    gate is skipped: ``advance_stage`` runs the vertical's completion validator
    against the stage being left. When anything else also refused — an open
    external gate, a bounded mission with no standing to close the project, an
    uncertified review — the completion was wrong on its merits and stepping
    forward would launder it into a transition nobody asked for.
    """
    return len(blockers) == 1 and blockers[0].startswith(_STAGE_POSITION_BLOCKER)


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
        from ..verticals._base import load_vertical, vertical_completion_gate

        gate = vertical_completion_gate(
            load_vertical(vertical or "", project_root=project_root)
        )
    except Exception:  # noqa: BLE001 — an unreadable vertical keeps the strict rule
        return False
    return gate != "certified"


__all__ = [
    "StageDecision",
    "stage_decision_fields",
    "completion_trigger_reason",
    "extract_answer",
    "fallback_empty_stage_decision",
    "external_completion_gate_rework_decision",
    "external_completion_gate_stage_guard_decision",
    "final_stage_completion_blockers",
    "final_stage_completion_decision",
    "build_stage_decision_prompt",
    "parse_stage_decision",
]
