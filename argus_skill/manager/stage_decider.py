"""Manager stage-transition decision: prompt + strict parser.

The Manager is the SOLE authority over pipeline stage transitions. After the
reviewer (and planner) produce their structured feedback, the Manager
independently judges whether to ADVANCE to the next stage, HOLD on the current
one, or ROLL BACK to an earlier stage, then writes ``PIPELINE_STATE.json``. This
module holds the prompt it sends and the strict parser for its JSON verdict,
keeping ``manager/_core.py`` thin — mirroring how ``Manager.is_conversational``
delegates to ``life.router.classify_is_conversational``.

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


@dataclass
class StageDecision:
    """The parsed + validated stage verdict from the Manager's model call."""

    action: str       # "advance" | "hold" | "rollback" | "complete"
    target_stage: str
    reason: str
    diagnostic: str = ""


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


def _checklist_lines(review: Any) -> str:
    items = getattr(review, "checklist", None) or []
    lines: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        mark = "x" if it.get("satisfied") else " "
        evidence = str(it.get("evidence") or "").strip()
        line = f"- [{mark}] {it.get('item', '?')}"
        if evidence:
            line += f" — evidence: {evidence}"
        lines.append(line)
    return "\n".join(lines) or "(reviewer provided no per-item checklist)"


def _planner_report_lines(review: Any) -> str:
    report = getattr(review, "planner_report", None)
    if not isinstance(report, dict) or not report:
        return "(no structured planner report)"
    keys = ("forward_progress", "headline", "blocker", "recommended_next")
    lines = [f"{k}: {report.get(k)!r}" for k in keys if k in report]
    return "\n".join(lines) or "(no structured planner report)"


def _advisory_planner(planner_verdict: Any) -> str:
    if planner_verdict is None:
        return "(none)"
    for attr in ("reason", "headline"):
        val = getattr(planner_verdict, attr, None)
        if val:
            return str(val)
    if isinstance(planner_verdict, dict):
        return str(planner_verdict.get("reason") or planner_verdict.get("headline") or planner_verdict)
    return str(planner_verdict)


def build_stage_decision_prompt(
    *,
    current_stage: str,
    next_stage: str,
    earlier_stages: Sequence[str],
    checklist_md: str,
    review: Any,
    planner_verdict: Any = None,
) -> str:
    """Render the prompt asking the Manager to rule on the stage transition."""
    earlier = ", ".join(f"`{s}`" for s in earlier_stages) or "(none — already first)"
    advance_target = f"`{next_stage}`" if next_stage else "(none — already the final stage)"
    status = str(getattr(review, "status", "") or "")
    reason = str(getattr(review, "reason", "") or "")

    return (
        "You are the MANAGER of an automated research pipeline, and the SOLE "
        "authority over pipeline STAGE transitions. The reviewer and planner only "
        "ADVISE; YOU decide. Choose exactly one of: ADVANCE to the next stage, "
        "HOLD on the current stage, or ROLL BACK to an earlier stage — based only "
        "on the evidence below.\n\n"
        f"Current stage: `{current_stage}`\n"
        f"The ONLY legal ADVANCE target (the immediate next stage): {advance_target}\n"
        f"Legal ROLLBACK targets (earlier stages): {earlier}\n\n"
        "## Current-stage checklist (what \"done\" requires)\n"
        f"{checklist_md}\n\n"
        "## Reviewer verdict on the latest round\n"
        f"status: {status}\n"
        f"reason: {reason}\n"
        f"{_planner_report_lines(review)}\n\n"
        "### Reviewer per-item checklist\n"
        f"{_checklist_lines(review)}\n\n"
        "## Planner note (advisory)\n"
        f"{_advisory_planner(planner_verdict)}\n\n"
        "## Your decision\n"
        "- ADVANCE only when the current stage's checklist is genuinely satisfied "
        "with concrete evidence the reviewer confirmed.\n"
        "- HOLD when any checklist work remains, or the evidence is weak/unclear.\n"
        "- ROLL BACK only when an EARLIER stage's evidence is missing, stale, or "
        "unreliable (say which one and why).\n"
        "- When in doubt, HOLD. Never advance on weak evidence.\n\n"
        "Reply with ONE JSON object and NOTHING else:\n"
        '{"action": "advance|hold|rollback", "target_stage": "<stage name>", '
        '"reason": "<clear explanation>"}\n'
        "For HOLD, set target_stage to the current stage."
    )


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
    raw_target = obj.get("target_stage")
    target = _normalized_stage_label(raw_target)

    if action == "hold":
        return StageDecision("hold", cur, reason or "manager held", "intentional_hold")

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

    next_stage = order[cur_idx + 1]
    return StageDecision(
        "advance",
        next_stage,
        "reviewer certified current-stage checklist after empty manager output",
        "empty_output_certified_advance",
    )


def _review_certifies_completion(review: Any) -> str:
    status = str(getattr(review, "status", "") or "").strip().lower()
    if status != "done":
        return "review_not_done"
    report = getattr(review, "planner_report", None)
    if not isinstance(report, dict) or report.get("forward_progress") is not True:
        return "no_forward_progress"
    items = getattr(review, "checklist", None)
    if not isinstance(items, list) or not items:
        return "missing_checklist"
    for item in items:
        if not isinstance(item, dict):
            return "invalid_checklist"
        if not bool(item.get("satisfied")):
            return "unsatisfied_checklist"
        if not str(item.get("evidence", "")).strip():
            return "missing_checklist_evidence"
    return ""


def final_stage_completion_decision(
    review: Any,
    *,
    current_stage: str,
    stage_order: Sequence[str],
    trigger_diagnostic: str = "",
    trigger_reason: str = "",
) -> StageDecision | None:
    """Return a COMPLETE decision when the final stage is reviewer-certified."""
    cur = (current_stage or "").strip().lower()
    order = [str(s).strip().lower() for s in stage_order]
    if not order or cur != order[-1]:
        return None
    missing = _review_certifies_completion(review)
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
