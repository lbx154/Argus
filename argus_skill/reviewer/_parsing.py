"""Parse the Reviewer's minimal verdict."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from ..core.model_visible_text import (
    contains_integrity_judgment,
    has_material_blocker,
    sanitize_model_judgment_text,
)
from ..core.models import ReviewDecision
from ..core.operator_decision import (
    normalize_agent_options,
    parse_agent_operator_options,
)
from ..core.research_contract import normalize_research_result

_STATUSES = {"done", "continue", "blocked", "replan_requested"}
_PLAN_SIGNALS = {"continue", "reconsider"}
_AUTHORITY_IMPACTS = {"technical", "manager_contract", "operator"}
_FRONTIER_CHANGES = {
    "artifact_improved",
    "risk_reduced",
    "uncertainty_reduced",
    "information_gain",
    "bounded_regression",
    "recovered",
    "unchanged_failure",
    "expanding_regression",
    "unexplained_regression",
}
_SESSION_SIGNALS = {
    "repeated_contradiction",
    "reviewer_confusion",
    "quality_degradation",
}
_SESSION_SIGNAL_ROLES = {"planner", "engineer", "reviewer"}


def _planner_report(
    *,
    forward_progress: Any = None,
    plan_signal: Any = None,
    challenge: Any = None,
    alternative: Any = None,
    authority_impact: Any = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    if isinstance(forward_progress, bool):
        report["forward_progress"] = forward_progress
    signal = str(plan_signal or "").strip().lower()
    if signal in _PLAN_SIGNALS:
        report["plan_signal"] = signal
    challenge_text = str(challenge or "").strip()
    if challenge_text.casefold() not in {"", "none", "n/a", "null"}:
        report["challenge"] = challenge_text[:2000]
    alternative_text = str(alternative or "").strip()
    if alternative_text.casefold() not in {"", "none", "n/a", "null"}:
        report["alternative"] = alternative_text[:2000]
    authority = str(authority_impact or "").strip().lower()
    if authority in _AUTHORITY_IMPACTS:
        report["authority_impact"] = authority
    return report


def _planner_report_from_payload(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Read the plan fields from either shape the Reviewer has been shown.

    The decision event documented in the prompt is flat — ``plan_signal``,
    ``plan_challenge``, ``plan_alternative`` sit beside ``status`` — while
    older schema-constrained output nested the same fields under
    ``planner_report``. Reading only the nested shape silently discarded every
    plan challenge the current prompt asks the Reviewer to raise, so a campaign
    could close round after locally correct round with no way to say that the
    plan itself had become the obstacle.
    """
    nested = parsed.get("planner_report")
    source = nested if isinstance(nested, dict) else parsed
    return _planner_report(
        forward_progress=source.get("forward_progress"),
        plan_signal=source.get("plan_signal"),
        challenge=source.get("challenge", source.get("plan_challenge")),
        alternative=source.get("alternative", source.get("plan_alternative")),
        authority_impact=source.get("authority_impact"),
    )


def _frontier_report(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    change = str(value.get("change") or "").strip().lower()
    if change not in _FRONTIER_CHANGES:
        return {}
    report: dict[str, Any] = {
        "change": change,
        "summary": str(value.get("summary") or "").strip()[:2000],
    }
    for key in (
        "resolved_obligations",
        "new_obligations",
        "regressed_obligations",
        "remaining_work",
        "proxy_changes",
        "artifacts",
        "evidence",
    ):
        raw = value.get(key)
        if isinstance(raw, list):
            report[key] = [
                text for item in raw[:40] if (text := str(item or "").strip())
            ]
    for key in ("hypothesis", "uncertainty", "next_decision_point"):
        text = str(value.get(key) or "").strip()
        if text:
            report[key] = text[:2000]
    regression = value.get("regression")
    if isinstance(regression, dict):
        report["regression"] = {}
        for key in ("cause", "scope", "budget", "recovery_test", "exit_trigger"):
            text = str(regression.get(key) or "").strip()
            report["regression"][key] = (
                "" if text.casefold() in {"none", "null", "n/a", "na", "-"} else text[:1000]
            )
    return report


def _tagged_values(raw: str) -> dict[str, str]:
    """Parse compact ``name::value|name::value`` handoff fields."""
    tagged: dict[str, str] = {}
    for part in str(raw or "").split("|"):
        name, separator, value = part.partition("::")
        if separator and name.strip() and value.strip():
            tagged[name.strip().lower()] = value.strip()
    return tagged


def _tagged_items(value: str) -> list[str]:
    text = str(value or "").strip()
    if text.casefold() in {"", "none", "null", "n/a", "na", "-"}:
        return []
    return [item.strip() for item in text.split(";") if item.strip()]


def _session_signal(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    kind = str(value.get("kind") or "").strip().lower()
    target = str(value.get("target") or "").strip().lower()
    if kind not in _SESSION_SIGNALS or target not in _SESSION_SIGNAL_ROLES:
        return {}
    return {
        "kind": kind,
        "target": target,
        "detail": str(value.get("detail") or "").strip()[:1000],
    }


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
    frontier = decision.frontier_report
    if isinstance(frontier, dict):
        change = str(frontier.get("change") or "")
        regression = frontier.get("regression")
        regression = regression if isinstance(regression, dict) else {}
        envelope_complete = all(
            str(regression.get(key) or "").strip()
            for key in ("cause", "scope", "budget", "recovery_test", "exit_trigger")
        )
        if change == "bounded_regression" and not envelope_complete:
            decision.reason += (
                " Note: the bounded-regression report omitted one or more of cause, "
                "scope, budget, recovery test, and exit trigger."
            )
        elif change == "expanding_regression" and decision.status == "done":
            decision.reason += (
                " Note: the same verdict reports an expanding regression."
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
        decision = decision_from_payload(parsed)
        if decision is not None:
            return decision
    return None


def decision_from_payload(payload: Mapping[str, Any]) -> ReviewDecision | None:
    """Build a verdict from an already-structured Reviewer payload.

    This is what the Reviewer's decision event carries, so the runtime reads it
    here rather than serialising it back to JSON text and parsing that. The
    round trip was how the flat event shape came to be dropped: the reader on
    the far side only ever looked for a nested ``planner_report``.

    ``None`` when a control field is missing or invalid; the caller then treats
    the turn as having produced no verdict rather than inventing one.
    """
    status = str(payload.get("status") or "").strip().lower()
    reason = payload.get("reason")
    next_action = payload.get("next_action")
    operator_question = payload.get("operator_question")
    if status not in _STATUSES:
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    if not isinstance(next_action, str):
        return None
    if operator_question is not None and not isinstance(operator_question, str):
        return None
    raw_options = payload.get("operator_options")
    option_values = raw_options if isinstance(raw_options, (list, tuple)) else ()
    return _apply_model_judgment_policy(
        ReviewDecision(
            status=status,
            reason=reason.strip(),
            next_action=next_action.strip(),
            operator_question=str(operator_question or "").strip(),
            operator_options=normalize_agent_options(option_values),
            checkpoint_recommended=bool(payload.get("checkpoint_recommended", False)),
            research_result=normalize_research_result(
                payload.get("research_result")
            ),
            planner_report=_planner_report_from_payload(payload),
            frontier_report=_frontier_report(payload.get("frontier_report")),
            session_signal=_session_signal(payload.get("session_signal")),
        )
    )


# Compatibility reader, not a prompt schema. Several older sessions emitted
# frontier/session bookkeeping; accepting it is cheap, but the current prompt
# only asks for fields that affect settlement, operator routing, research
# certification, or Manager plan adjudication.
_VERDICT_KEYS = (
    "STATUS",
    "REASON",
    "NEXT_ACTION",
    "OPERATOR_QUESTION",
    "OPERATOR_OPTIONS",
    "CHECKPOINT_RECOMMENDED",
    "RESEARCH_RESULT",
    "FORWARD_PROGRESS",
    "PLAN_SIGNAL",
    "PLAN_CHALLENGE",
    "PLAN_ALTERNATIVE",
    "AUTHORITY_IMPACT",
    "FRONTIER_CHANGE",
    "FRONTIER_SUMMARY",
    "FRONTIER_OBLIGATIONS",
    "FRONTIER_EVIDENCE",
    "NEXT_DECISION_POINT",
    "REGRESSION_ENVELOPE",
    "SESSION_SIGNAL",
)


def _parse_named_verdict(text: str) -> ReviewDecision | None:
    """The verdict as stated on named lines, or ``None`` if it was not.

    Fails closed exactly like the JSON path did: a status outside the allowed
    set, or a verdict with no rationale, is not a verdict. The caller treats
    ``None`` as "the Reviewer did not rule", which is the safe reading of an
    answer we could not understand.
    """
    from ..core.role_reply import (
        decision_footer_text,
        read_block,
        read_key_values,
        read_optional,
    )

    values = read_key_values(text, _VERDICT_KEYS)
    status = str(values.get("STATUS") or "").strip().lower()
    if status not in _STATUSES:
        natural = re.search(
            r"(?im)^\s*(?:verdict|decision)\s*[:=]\s*`?"
            r"(done|continue|blocked|replan_requested)\b",
            text,
        )
        status = natural.group(1).lower() if natural else ""
    if status not in _STATUSES:
        return None
    reason = read_block(text, "REASON", _VERDICT_KEYS)
    if not reason.strip():
        return None
    obligations = _tagged_values(read_optional(values, "FRONTIER_OBLIGATIONS"))
    evidence = _tagged_values(read_optional(values, "FRONTIER_EVIDENCE"))
    regression = _tagged_values(read_optional(values, "REGRESSION_ENVELOPE"))
    signal = _tagged_values(read_optional(values, "SESSION_SIGNAL"))
    research_result = normalize_research_result(
        _load_json(read_optional(values, "RESEARCH_RESULT"))
    )
    return _apply_model_judgment_policy(
        ReviewDecision(
            status=status,
            reason=reason.strip()[:5000],
            next_action=read_block(text, "NEXT_ACTION", _VERDICT_KEYS).strip()[:1500],
            operator_question=read_optional(values, "OPERATOR_QUESTION")[:500],
            operator_options=parse_agent_operator_options(
                decision_footer_text(text)
            ),
            checkpoint_recommended=(
                read_optional(values, "CHECKPOINT_RECOMMENDED").casefold() == "true"
            ),
            research_result=research_result,
            planner_report=_planner_report(
                forward_progress=(
                    True
                    if read_optional(values, "FORWARD_PROGRESS").casefold() == "true"
                    else False
                    if read_optional(values, "FORWARD_PROGRESS").casefold() == "false"
                    else None
                ),
                plan_signal=read_optional(values, "PLAN_SIGNAL"),
                challenge=read_block(text, "PLAN_CHALLENGE", _VERDICT_KEYS),
                alternative=read_block(text, "PLAN_ALTERNATIVE", _VERDICT_KEYS),
                authority_impact=read_optional(values, "AUTHORITY_IMPACT"),
            ),
            frontier_report=_frontier_report({
                "change": read_optional(values, "FRONTIER_CHANGE"),
                "summary": read_block(text, "FRONTIER_SUMMARY", _VERDICT_KEYS),
                "hypothesis": evidence.get("hypothesis", ""),
                "resolved_obligations": _tagged_items(obligations.get("resolved", "")),
                "new_obligations": _tagged_items(obligations.get("new", "")),
                "regressed_obligations": _tagged_items(obligations.get("regressed", "")),
                "remaining_work": _tagged_items(obligations.get("remaining", "")),
                "artifacts": _tagged_items(evidence.get("artifacts", "")),
                "evidence": _tagged_items(evidence.get("evidence", "")),
                "proxy_changes": _tagged_items(evidence.get("proxies", "")),
                "uncertainty": evidence.get("uncertainty", ""),
                "next_decision_point": read_block(
                    text, "NEXT_DECISION_POINT", _VERDICT_KEYS
                ),
                "regression": {
                    "cause": regression.get("cause", ""),
                    "scope": regression.get("scope", ""),
                    "budget": regression.get("budget", ""),
                    "recovery_test": regression.get("recovery", ""),
                    "exit_trigger": regression.get("exit", ""),
                },
            }),
            session_signal=_session_signal(signal),
        )
    )


def describe_unparsed_verdict(messages: list[str]) -> str:
    """Say what was wrong with an unreadable verdict, in the operator's terms.

    ``_find_decision_in_messages`` returns ``None`` for three unrelated
    failures — no ``STATUS`` line, a ``STATUS`` outside the allowed set, and a
    verdict with no rationale — and the caller reported all three as "did not
    contain a valid named verdict footer". In testbed run 15 (``s-f0dbba19``)
    that sentence was printed against a reply whose footer was entirely there:
    eighteen named fields parsed, and only ``STATUS`` was missed, because the
    model had welded it to the end of the preceding sentence. The operator was
    told to ask for something they had already been given.

    The welding itself is handled upstream now, in
    :func:`argus_skill.core.role_reply._split_glued_keys`. This is for whatever
    the next unreadable reply turns out to be.
    """
    from ..core.role_reply import read_block, read_key_values

    text = "\n".join(str(m or "") for m in messages).strip()
    if not text:
        return "Reviewer produced no output to read a verdict from."
    values = read_key_values(text, _VERDICT_KEYS)
    status = str(values.get("STATUS") or "").strip().lower()
    if not status:
        seen = ", ".join(sorted(values)) or "none"
        return (
            "Reviewer output had no readable STATUS line "
            f"(named fields that did parse: {seen})."
        )
    if status not in _STATUSES:
        return (
            f"Reviewer STATUS={status!r} is not one of "
            f"{', '.join(sorted(_STATUSES))}."
        )
    if not read_block(text, "REASON", _VERDICT_KEYS).strip():
        return f"Reviewer STATUS={status} carried no REASON; a verdict needs one."
    return "Reviewer output did not contain a valid named verdict footer."


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


__all__ = [
    "_find_decision_in_messages",
    "decision_from_payload",
    "describe_unparsed_verdict",
    "parse_decision_text",
]
