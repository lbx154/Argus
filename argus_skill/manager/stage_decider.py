"""Manager stage-transition decision: prompt + strict parser.

The Manager is the SOLE authority over pipeline stage transitions. After the
reviewer (and planner) produce their structured feedback, the Manager
independently judges whether to ADVANCE to the next stage, HOLD on the current
one, or ROLL BACK to an earlier stage, then writes ``PIPELINE_STATE.json``. The prompt body lives in ``roles.prompts.manager`` and is re-exported here for
source compatibility; this module owns the strict parser for its JSON verdict.

Fail-closed everywhere: any ambiguity in the model's answer (bad JSON, unknown
action, an advance target that is not the immediate next stage, a rollback
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


_VALID_ACTIONS = ("advance", "hold", "rollback")


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
      * ``action`` must be one of advance/hold/rollback (else HOLD);
      * ADVANCE ``target_stage`` must be the IMMEDIATE next stage in
        ``stage_order`` (no skipping; else HOLD);
      * ROLLBACK ``target_stage`` must be strictly EARLIER than ``current_stage``
        (else HOLD);
      * HOLD pins ``target_stage`` to the current stage.
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
        if target != next_stage:
            return StageDecision(
                "hold", cur, "manager held (default)", "illegal_advance_target"
            )  # must be the immediate next stage
        diagnostic = "normalized_target_stage" if raw_target != target else "valid_target"
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
        )
        if issue:
            return issue
    return ""


def completion_trigger_reason(action: str, reason: str) -> str:
    """What a `complete` transition should record when it overrode the trigger.

    A hold's reason attached to a `complete` transition is what gets persisted
    into ``stage_history``. Observed verbatim in a real run on 2026-07-26:

        {"direction": "complete", ..., "reason": "manager held (default)"}

    An operator reading that cannot tell whether the stage completed or was
    held, which is the one question stage_history exists to answer. When the
    trigger agreed, its own words are the most informative thing to keep.
    """
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
    research_target_level: str | None = None,
    checklist_contract: Any | None = None,
    trigger_diagnostic: str = "",
    trigger_reason: str = "",
) -> StageDecision | None:
    """Return a COMPLETE decision when the final stage is reviewer-certified."""
    cur = (current_stage or "").strip().lower()
    order = [str(s).strip().lower() for s in stage_order]
    if not order or cur != order[-1]:
        return None
    if not _mission_scope_can_complete(mission_scope, vertical):
        return None
    missing = _review_certifies_completion(
        review,
        vertical=vertical,
        mission_scope=mission_scope,
        research_target_level=research_target_level,
        checklist_contract=checklist_contract,
    )
    if missing:
        return None
    reason = trigger_reason or "reviewer certified final-stage checklist"
    diagnostic = trigger_diagnostic or "final_stage_certified_complete"
    return StageDecision("complete", cur, reason, diagnostic)


def _mission_scope_can_complete(mission_scope: str, vertical: str) -> bool:
    """Whether a mission with this scope is allowed to close the project.

    ``final_submission`` is the *paper* transport scope and nothing else can
    complete a paper project: a bounded sub-mission must not end a submission
    just because its own Reviewer said `done`.

    For every other vertical that requirement was unsatisfiable, and it produced
    a livelock observed in a real session on 2026-07-26. Three rules interlocked:
    ``_planner_task_tags`` downgrades ``final_submission`` to ``bounded`` for any
    vertical whose completion gate is not ``full_paper``; ``tick()`` retires a
    ``final_submission`` item under such a vertical as stale, which is why the
    downgrade exists; and this function accepted nothing but
    ``final_submission``. So the Goal Gate mission of twenty of the
    twenty-three verticals could never close its own gate — the Reviewer
    certified, the Manager held `not_certified`, and the Planner re-issued the
    identical task until the operator stopped it.

    The requirement now follows what the vertical declares about itself rather
    than a transport tag it can never carry. The certification check below is
    unchanged and still has to pass; this only decides which envelope the
    verdict may arrive in.
    """
    normalized = (mission_scope or "").strip().lower().replace("-", "_")
    if normalized == "final_submission":
        return True
    try:
        from ..verticals._base import load_vertical, vertical_completion_gate

        gate = vertical_completion_gate(load_vertical(vertical or ""))
    except Exception:  # noqa: BLE001 — an unreadable vertical keeps the strict rule
        return False
    return gate != "full_paper"


__all__ = [
    "StageDecision",
    "stage_decision_fields",
    "completion_trigger_reason",
    "extract_answer",
    "fallback_empty_stage_decision",
    "final_stage_completion_decision",
    "build_stage_decision_prompt",
    "parse_stage_decision",
]
