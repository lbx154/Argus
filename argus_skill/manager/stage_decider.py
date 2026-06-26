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

    action: str       # "advance" | "hold" | "rollback"
    target_stage: str
    reason: str
    confidence: float


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
        '"reason": "<one sentence>", "confidence": <0.0-1.0>}\n'
        "For HOLD, set target_stage to the current stage."
    )


def _loads_first_json(text: str) -> Any:
    cleaned = (text or "").strip()
    # Strip a leading/trailing markdown code fence if present.
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001 — fall through to brace extraction
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:  # noqa: BLE001
            return None
    return None


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
    hold = StageDecision("hold", cur, "manager held (default)", 0.0)

    obj = _loads_first_json(raw_text)
    if not isinstance(obj, dict):
        return hold
    action = str(obj.get("action") or "").strip().lower()
    if action not in _VALID_ACTIONS:
        return hold
    reason = str(obj.get("reason") or "").strip()
    try:
        confidence = float(obj.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    target = str(obj.get("target_stage") or "").strip().lower()

    if action == "hold":
        return StageDecision("hold", cur, reason or "manager held", confidence)

    if cur not in order:
        return hold  # cannot validate ordering → safe HOLD

    cur_idx = order.index(cur)
    if action == "advance":
        nxt_idx = cur_idx + 1
        if nxt_idx >= len(order) or target != order[nxt_idx]:
            return hold  # must be the immediate next stage
        return StageDecision("advance", target, reason or "checklist satisfied", confidence)

    # rollback
    if target not in order or order.index(target) >= cur_idx:
        return hold  # must be strictly earlier
    return StageDecision("rollback", target, reason or "upstream evidence unreliable", confidence)


__all__ = [
    "StageDecision",
    "extract_answer",
    "build_stage_decision_prompt",
    "parse_stage_decision",
]
