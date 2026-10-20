"""Parse the Reviewer's minimal verdict."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from ..core.model_visible_text import (
    contains_integrity_judgment,
    has_material_blocker,
    sanitize_model_judgment_text,
    sanitize_model_visible_text,
)
from ..core.models import ReviewDecision
from ..core.operator_decision import (
    normalize_agent_options,
    parse_agent_operator_options,
)
from ..core.research_contract import normalize_research_result
from ..core.venue_review import normalize_venue_review

_STATUSES = {"done", "continue", "blocked", "replan_requested"}
_PLAN_SIGNALS = {"continue", "reconsider"}
_AUTHORITY_IMPACTS = {"technical", "manager_contract", "operator"}
_SESSION_SIGNALS = {
    "repeated_contradiction",
    "reviewer_confusion",
    "quality_degradation",
}
_SESSION_SIGNAL_ROLES = {"planner", "engineer", "reviewer"}


def _planner_report(
    *,
    plan_signal: Any = None,
    challenge: Any = None,
    alternative: Any = None,
    authority_impact: Any = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
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
    sources = (nested, parsed) if isinstance(nested, dict) else (parsed,)

    def field(*names: str) -> Any:
        # Fill only absent fields from the flat event. Explicit nested values,
        # including false, empty strings and None, remain authoritative.
        for source in sources:
            for name in names:
                if name in source:
                    return source[name]
        return None

    return _planner_report(
        plan_signal=field("plan_signal"),
        challenge=field("challenge", "plan_challenge"),
        alternative=field("alternative", "plan_alternative"),
        authority_impact=field("authority_impact"),
    )


def _tagged_values(raw: str) -> dict[str, str]:
    """Parse compact ``name::value|name::value`` handoff fields."""
    tagged: dict[str, str] = {}
    for part in str(raw or "").split("|"):
        name, separator, value = part.partition("::")
        if separator and name.strip() and value.strip():
            tagged[name.strip().lower()] = value.strip()
    return tagged


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
    """Ignore hash-only blockers without overruling substantive judgment."""
    original_reason = str(decision.reason or "")
    original_next_action = str(decision.next_action or "")
    original_judgment = original_reason + "\n" + original_next_action
    integrity_judgment = contains_integrity_judgment(original_judgment)
    material_blocker = has_material_blocker(original_judgment)

    def sanitize_without_gutting_material_paragraphs(value: str) -> str:
        kept: list[str] = []
        for paragraph in value.splitlines():
            sanitized = sanitize_model_judgment_text(paragraph)
            if not sanitized and paragraph.strip() and has_material_blocker(paragraph):
                # Redact opaque values, but retain a paragraph whose engineering
                # meaning would otherwise disappear with its integrity wording.
                sanitized = sanitize_model_visible_text(paragraph).strip()
            if sanitized:
                kept.append(sanitized)
        return "\n".join(kept)

    decision.reason = sanitize_without_gutting_material_paragraphs(original_reason)
    decision.next_action = sanitize_without_gutting_material_paragraphs(
        original_next_action
    )
    decision.operator_question = sanitize_model_judgment_text(decision.operator_question)
    if (
        decision.status == "blocked"
        and integrity_judgment
        and not decision.operator_question
        and not decision.next_action
        and not material_blocker
    ):
        decision.status = "continue"
        policy_note = (
            "Policy note: machine-only integrity metadata does not by itself "
            "justify blocking the project."
        )
        decision.reason = "\n".join(filter(None, (decision.reason, policy_note)))
    elif not decision.reason:
        # Preserve the Reviewer's text whenever possible. This fallback only
        # covers a non-blocked verdict whose entire rationale was an opaque
        # integrity assertion.
        decision.reason = sanitize_model_visible_text(original_reason).strip()
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
            research_result=normalize_research_result(
                payload.get("research_result")
            ),
            venue_review=normalize_venue_review(payload.get("venue_review")),
            planner_report=_planner_report_from_payload(payload),
            session_signal=_session_signal(payload.get("session_signal")),
        )
    )


# Compatibility reader, not a prompt schema. Older sessions also emitted
# FRONTIER_* and REGRESSION_ENVELOPE lines; they stay listed only so they end
# the block before them, and are otherwise ignored.
_VERDICT_KEYS = (
    "STATUS",
    "REASON",
    "NEXT_ACTION",
    "OPERATOR_QUESTION",
    "OPERATOR_OPTIONS",
    "RESEARCH_RESULT",
    "VENUE_REVIEW",
    # Retired; still keys so a legacy line ends the block before it.
    "CHECKPOINT_RECOMMENDED",
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


def _compact_research_result_blocks(text: str) -> str:
    """Keep JSON evidence strings out of the line-based control-field reader."""
    from ..core.role_reply import _line_pattern

    pattern = _line_pattern(("RESEARCH_RESULT", "VENUE_REVIEW"))
    decoder = json.JSONDecoder()
    chunks: list[str] = []
    consumed = offset = 0
    for line in text.splitlines(keepends=True):
        start = offset
        offset += len(line)
        if start < consumed:
            continue
        stripped = line.lstrip(" \t")
        match = pattern.match(stripped)
        if match is None:
            continue
        body_start = start + len(line) - len(stripped) + match.start("value")
        while body_start < len(text) and text[body_start].isspace():
            body_start += 1
        if text.startswith("```", body_start):
            fence_end = text.find("\n", body_start)
            if fence_end < 0:
                continue
            body_start = fence_end + 1
            while body_start < len(text) and text[body_start].isspace():
                body_start += 1
        try:
            payload, end = decoder.raw_decode(text, body_start)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue
        field = "VENUE_REVIEW" if "VENUE_REVIEW" in stripped.upper().split("=", 1)[0].split(":", 1)[0] else "RESEARCH_RESULT"
        chunks.extend((text[consumed:start], field + "=" + json.dumps(payload)))
        consumed = end
    return "".join((*chunks, text[consumed:])) if chunks else text


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

    text = _compact_research_result_blocks(text)
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
    signal = _tagged_values(read_optional(values, "SESSION_SIGNAL"))
    raw_research_result = _load_json(read_optional(values, "RESEARCH_RESULT"))
    if raw_research_result is None:
        raw_research_result = _load_json(read_block(text, "RESEARCH_RESULT", _VERDICT_KEYS))
    research_result = normalize_research_result(raw_research_result)
    raw_venue_review = _load_json(read_optional(values, "VENUE_REVIEW"))
    if raw_venue_review is None:
        raw_venue_review = _load_json(read_block(text, "VENUE_REVIEW", _VERDICT_KEYS))
    venue_review = normalize_venue_review(raw_venue_review)
    from ..core.venue_review import FINAL_REVIEW_FEEDBACK_CHARS

    return _apply_model_judgment_policy(
        ReviewDecision(
            status=status,
            reason=reason.strip()[:FINAL_REVIEW_FEEDBACK_CHARS if venue_review is not None else 5000],
            next_action=read_block(text, "NEXT_ACTION", _VERDICT_KEYS).strip()[:FINAL_REVIEW_FEEDBACK_CHARS if venue_review is not None else 1500],
            operator_question=read_optional(values, "OPERATOR_QUESTION")[:500],
            operator_options=parse_agent_operator_options(
                decision_footer_text(text)
            ),
            research_result=research_result,
            venue_review=venue_review,
            planner_report=_planner_report(
                plan_signal=read_optional(values, "PLAN_SIGNAL"),
                challenge=read_block(text, "PLAN_CHALLENGE", _VERDICT_KEYS),
                alternative=read_block(text, "PLAN_ALTERNATIVE", _VERDICT_KEYS),
                authority_impact=read_optional(values, "AUTHORITY_IMPACT"),
            ),
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
    :func:`argus.core.role_reply._split_glued_keys`. This is for whatever
    the next unreadable reply turns out to be.
    """
    from ..core.role_reply import read_block, read_key_values

    text = "\n".join(str(m or "") for m in messages).strip()
    if not text:
        return "Reviewer produced no output to read a judgment from."
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
        return f"Reviewer STATUS={status} carried no REASON; a judgment needs one."
    return "Reviewer output did not state a readable judgment on its named closing lines."


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
