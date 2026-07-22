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

    obj, load_diagnostic = _loads_first_json(raw_text)
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
    """Resolve persistent empty manager-stage output without wedging a stage.

    Empty output is not a Manager judgment. After the Manager core exhausts its
    retries, this fallback may advance only from a reviewer-certified current
    stage: latest reviewer status is ``done``, structured planner progress is
    explicitly true, and every reviewer-supplied checklist item is satisfied
    with evidence. Anything missing or ambiguous remains a HOLD.
    """
    cur = (current_stage or "").strip().lower()
    order = [str(s).strip().lower() for s in stage_order]

    def hold(
        diagnostic: str,
        reason: str = "manager held after empty output",
    ) -> StageDecision:
        return StageDecision("hold", cur, reason, diagnostic)

    if cur not in order:
        return hold("empty_output_unknown_current_stage")
    cur_idx = order.index(cur)
    if cur_idx >= len(order) - 1:
        return hold("empty_output_no_next_stage")

    status = str(getattr(review, "status", "") or "").strip().lower()
    if status != "done":
        return hold("empty_output_review_not_done")

    report = getattr(review, "planner_report", None)
    if not isinstance(report, dict) or report.get("forward_progress") is not True:
        return hold("empty_output_no_forward_progress")

    items = getattr(review, "checklist", None)
    if not isinstance(items, list) or not items:
        return hold("empty_output_missing_checklist")
    for item in items:
        if not isinstance(item, dict):
            return hold("empty_output_invalid_checklist")
        if not bool(item.get("satisfied")):
            return hold("empty_output_unsatisfied_checklist")
        if not str(item.get("evidence", "")).strip():
            return hold("empty_output_missing_checklist_evidence")
    if checklist_contract is not None:
        required_ids = {
            str(getattr(item, "id", "") or "").strip()
            for item in getattr(checklist_contract, "items", ())
            if str(getattr(item, "id", "") or "").strip()
        }
        reviewed_ids = {
            str(item.get("item") or item.get("id") or "").strip()
            for item in items
        }
        if required_ids - reviewed_ids:
            return hold("empty_output_missing_required_checklist_items")

    next_stage = order[cur_idx + 1]
    return StageDecision(
        "advance",
        next_stage,
        "reviewer certified current-stage checklist after empty manager output",
        "empty_output_certified_advance",
    )


def _review_certifies_completion(
    review: Any,
    *,
    vertical: str = "",
    mission_scope: str = "",
    research_target_level: str | None = None,
    checklist_contract: Any | None = None,
) -> str:
    status = str(getattr(review, "status", "") or "").strip().lower()
    if status != "done":
        return "review_not_done"
    report = getattr(review, "planner_report", None)
    if not isinstance(report, dict) or report.get("forward_progress") is not True:
        return "no_forward_progress"
    items = getattr(review, "checklist", None)
    review_scope = str(getattr(review, "scope", "") or "").strip().lower()
    scope = (mission_scope or "").strip().lower().replace("-", "_")
    checklist_required = (
        scope == "final_submission"
        or review_scope.replace("-", "_") == "final_submission"
    )
    required_item_ids: set[str] = set()
    if checklist_contract is not None:
        checklist_optional = bool(
            getattr(checklist_contract, "checklist_optional", False)
        )
        contract_state = str(
            getattr(getattr(checklist_contract, "state", ""), "value", "")
            or getattr(checklist_contract, "state", "")
        )
        if not checklist_optional and contract_state != "loaded":
            return f"required_checklist_{contract_state or 'not_loaded'}"
        checklist_required = checklist_required or not checklist_optional
        required_item_ids = {
            str(getattr(item, "id", "") or "").strip()
            for item in getattr(checklist_contract, "items", ())
            if str(getattr(item, "id", "") or "").strip()
        }
    if not isinstance(items, list):
        if checklist_required:
            return "missing_checklist"
        items = []
    if checklist_required and not items:
        return "missing_checklist"
    for item in items:
        if not isinstance(item, dict):
            return "invalid_checklist"
        if not bool(item.get("satisfied")):
            return "unsatisfied_checklist"
        if not str(item.get("evidence", "")).strip():
            return "missing_checklist_evidence"
    if required_item_ids:
        reviewed_item_ids = {
            str(item.get("item") or item.get("id") or "").strip()
            for item in items
            if isinstance(item, dict)
        }
        missing_item_ids = required_item_ids - reviewed_item_ids
        if missing_item_ids:
            return "missing_required_checklist_items"
    # ``research_target_level`` is presented to the Reviewer, which owns the
    # scientific completion judgment. The Manager checks the reviewed checklist
    # shape here but does not reinterpret result-class labels.
    _ = research_target_level
    return ""


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


__all__ = [
    "StageDecision",
    "extract_answer",
    "fallback_empty_stage_decision",
    "final_stage_completion_decision",
    "build_stage_decision_prompt",
    "parse_stage_decision",
]
