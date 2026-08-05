"""Parse the Reviewer's minimal verdict."""

from __future__ import annotations

import json
from typing import Any

from ..core.model_visible_text import (
    contains_integrity_judgment,
    has_material_blocker,
    sanitize_model_judgment_text,
)
from ..core.models import ReviewDecision

_STATUSES = {"done", "continue", "blocked", "replan_requested"}
_PLAN_SIGNALS = {"continue", "reconsider"}


def _planner_report(
    *,
    forward_progress: Any = None,
    plan_signal: Any = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    if isinstance(forward_progress, bool):
        report["forward_progress"] = forward_progress
    signal = str(plan_signal or "").strip().lower()
    if signal in _PLAN_SIGNALS:
        report["plan_signal"] = signal
    return report


def _apply_model_judgment_policy(decision: ReviewDecision) -> ReviewDecision:
    """Ensure opaque integrity identifiers cannot become Reviewer blockers."""
    original_reason = str(decision.reason or "")
    original_next_action = str(decision.next_action or "")
    integrity_judgment = contains_integrity_judgment(original_reason + "\n" + original_next_action)
    decision.reason = sanitize_model_judgment_text(original_reason)
    decision.next_action = sanitize_model_judgment_text(original_next_action)
    decision.operator_question = sanitize_model_judgment_text(decision.operator_question)
    if (
        decision.status != "done"
        and integrity_judgment
        and not decision.operator_question
        and not decision.next_action
        and not has_material_blocker(decision.reason)
    ):
        decision.status = "done"
        decision.reason = decision.reason or (
            "No model-relevant blocker remains after ignoring machine-only integrity metadata."
        )
    elif not decision.reason:
        decision.reason = (
            "The Reviewer cited only machine-only integrity metadata and did not "
            "identify a semantic blocker."
        )
    return decision


def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _load_json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _candidate_json_objects(text: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    direct = _load_json(text)
    if direct is not None:
        candidates.append(direct)
    left = text.find("{")
    right = text.rfind("}")
    if left >= 0 and right > left:
        extracted = _load_json(text[left : right + 1])
        if extracted is not None and extracted not in candidates:
            candidates.append(extracted)
    return candidates


def parse_decision_text(
    text: str,
    *,
    allow_research_pause: bool = False,
) -> ReviewDecision | None:
    """Return a verdict only when all four control fields are valid.

    The Reviewer states its verdict on named lines inside an ordinary reply; it
    is not forced to serialise itself into a JSON object. `reason` is read as a
    block because a rationale that runs to several paragraphs is the Reviewer
    doing its job, and truncating at the first newline would discard exactly the
    part that explains the verdict.

    A JSON object is still read when one is present, so a run already in flight
    against the older schema-constrained prompt still parses.
    """
    _ = allow_research_pause
    cleaned = _strip_markdown_fences(text)
    named = _parse_named_verdict(cleaned)
    if named is not None:
        return named
    for parsed in _candidate_json_objects(cleaned):
        status = str(parsed.get("status") or "").strip().lower()
        reason = parsed.get("reason")
        next_action = parsed.get("next_action")
        operator_question = parsed.get("operator_question")
        raw_skill_ops = parsed.get("skill_ops")
        raw_planner_report = parsed.get("planner_report")
        if status not in _STATUSES:
            continue
        if not isinstance(reason, str) or not reason.strip():
            continue
        if not isinstance(next_action, str):
            continue
        if operator_question is not None and not isinstance(operator_question, str):
            continue
        skill_ops = (
            [dict(op) for op in raw_skill_ops if isinstance(op, dict)]
            if isinstance(raw_skill_ops, list)
            else []
        )
        return _apply_model_judgment_policy(
            ReviewDecision(
                status=status,
                reason=reason.strip(),
                next_action=next_action.strip(),
                operator_question=str(operator_question or "").strip(),
                checkpoint_recommended=bool(parsed.get("checkpoint_recommended", False)),
                planner_report=(
                    _planner_report(
                        forward_progress=raw_planner_report.get("forward_progress"),
                        plan_signal=raw_planner_report.get("plan_signal"),
                    )
                    if isinstance(raw_planner_report, dict)
                    else {}
                ),
                skill_ops=skill_ops,
            )
        )
    return None


_VERDICT_KEYS = (
    "STATUS",
    "REASON",
    "NEXT_ACTION",
    "OPERATOR_QUESTION",
    "CHECKPOINT_RECOMMENDED",
    "FORWARD_PROGRESS",
    "PLAN_SIGNAL",
)


def _parse_named_verdict(text: str) -> ReviewDecision | None:
    """The verdict as stated on named lines, or ``None`` if it was not.

    Fails closed exactly like the JSON path did: a status outside the allowed
    set, or a verdict with no rationale, is not a verdict. The caller treats
    ``None`` as "the Reviewer did not rule", which is the safe reading of an
    answer we could not understand.
    """
    from ..core.role_reply import read_block, read_key_values, read_optional

    values = read_key_values(text, _VERDICT_KEYS)
    status = str(values.get("STATUS") or "").strip().lower()
    if status not in _STATUSES:
        return None
    reason = read_block(text, "REASON", _VERDICT_KEYS)
    if not reason.strip():
        return None
    return _apply_model_judgment_policy(
        ReviewDecision(
            status=status,
            reason=reason.strip()[:5000],
            next_action=read_block(text, "NEXT_ACTION", _VERDICT_KEYS).strip()[:1500],
            operator_question=read_optional(values, "OPERATOR_QUESTION")[:500],
            checkpoint_recommended=(
                read_optional(values, "CHECKPOINT_RECOMMENDED").casefold() == "true"
            ),
            planner_report=_planner_report(
                forward_progress=(
                    True
                    if read_optional(values, "FORWARD_PROGRESS").casefold() == "true"
                    else False
                    if read_optional(values, "FORWARD_PROGRESS").casefold() == "false"
                    else None
                ),
                plan_signal=read_optional(values, "PLAN_SIGNAL"),
            ),
        )
    )


def _find_decision_in_messages(
    messages: list[str],
    *,
    allow_research_pause: bool = False,
) -> ReviewDecision | None:
    _ = allow_research_pause
    for message in reversed(messages):
        decision = parse_decision_text(message)
        if decision is not None:
            return decision
    if len(messages) > 1:
        return parse_decision_text("\n".join(messages))
    return None


__all__ = ["_find_decision_in_messages", "parse_decision_text"]
