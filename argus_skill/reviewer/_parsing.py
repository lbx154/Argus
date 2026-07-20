"""Reviewer verdict parsing helpers — split out of :mod:`._core`.

Pure functions with no ``Reviewer`` / runner dependency, kept module-level so
verdict parsing can be unit-tested without spinning up a backend. Verbatim from
ArgusBot's ``agent_cli/reviewer.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from ..core.models import ReviewDecision, ReviewStatus
from ..core.research_contract import adapt_legacy_research_result_payload
from .failure_taxonomy import normalize_failure_layer

_BASE_REVIEW_STATUSES = {"done", "continue", "blocked", "replan_requested"}
_RESEARCH_PAUSE_STATUSES = {
    "research_incomplete",
    "paused_no_breakthrough",
    "exhausted_current_methods",
}


def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.split("\n")
    start = 1
    end = len(lines)
    if lines[-1].strip() == "```":
        end = len(lines) - 1
    return "\n".join(lines[start:end]).strip()


def _find_decision_in_messages(
    messages: list[str],
    *,
    allow_research_pause: bool = False,
) -> "ReviewDecision | None":
    for msg in reversed(messages):
        result = parse_decision_text(
            msg,
            allow_research_pause=allow_research_pause,
        )
        if result is not None:
            return result
    if len(messages) > 1:
        return parse_decision_text(
            "\n".join(messages),
            allow_research_pause=allow_research_pause,
        )
    return None


def parse_decision_text(
    text: str,
    *,
    allow_research_pause: bool = False,
) -> ReviewDecision | None:
    candidate = _strip_markdown_fences(text.strip())
    parsed = _load_json(candidate)
    if parsed is None:
        left = candidate.find("{")
        right = candidate.rfind("}")
        if left >= 0 and right > left:
            parsed = _load_json(candidate[left : right + 1])
    if parsed is None:
        return None
    status = _parse_status(
        parsed,
        allow_research_pause=allow_research_pause,
    )
    allowed_statuses = (
        _BASE_REVIEW_STATUSES | _RESEARCH_PAUSE_STATUSES
        if allow_research_pause
        else _BASE_REVIEW_STATUSES
    )
    if status not in allowed_statuses:
        return None
    round_summary_markdown = _parse_round_summary(parsed)
    reason = _parse_reason(parsed, round_summary_markdown=round_summary_markdown)
    next_action = _parse_next_action(parsed, status=status)
    completion_summary_markdown = _parse_optional_text(
        parsed.get("completion_summary_markdown")
    )
    if (
        reason is None
        or next_action is None
        or round_summary_markdown is None
        or completion_summary_markdown is None
    ):
        return None
    assert reason is not None
    assert next_action is not None
    assert round_summary_markdown is not None
    assert completion_summary_markdown is not None
    planner_report = _parse_planner_report(parsed, status=status, reason=reason)
    control_action, control_task_id = _parse_control(parsed)
    failure_source, failure_source_evidence, validator_id, repair_paths = (
        _parse_failure_source(parsed)
    )
    return ReviewDecision(
        status=status,
        reason=reason,
        next_action=next_action,
        operator_question=_parse_operator_question(
            parsed, status=status, next_action=next_action, reason=reason
        ),
        round_summary_markdown=round_summary_markdown,
        completion_summary_markdown=completion_summary_markdown,
        achievement=_parse_achievement(parsed, status=status),
        progress_class=_parse_progress_class(parsed, planner_report),
        control_action=control_action,
        control_task_id=control_task_id,
        scope=_parse_scope(parsed),
        checklist=_parse_checklist(parsed),
        research_result=_parse_research_result(parsed),
        planner_report=planner_report,
        checkpoint=_parse_checkpoint(parsed),
        failure_cause=_parse_failure_cause(parsed),
        failure_source=failure_source,
        failure_source_evidence=failure_source_evidence,
        validator_id=validator_id,
        repair_paths=repair_paths,
        scientific_decision=_parse_scientific_decision(parsed),
        failure_layer=normalize_failure_layer(parsed.get("failure_layer")),
        # Legacy replay compatibility only. Current Reviewer schemas no longer
        # expose these fields; live Reviewers edit the injected paths directly.
        skill_ops=_parse_skill_ops(parsed),
        wiki_ops=_parse_wiki_ops(parsed),
        checklist_feedback=_parse_checklist_feedback(parsed),
        step_back=_parse_step_back(parsed),
    )


_PROGRESS_CLASSES = frozenset({
    "decision",
    "evidence",
    "setup_only",
    "artifact_sync_only",
    "none",
})
_WAIT_FOR_SUBAGENT_CONTROL = "wait_for_subagent"


def _parse_control(parsed: dict[str, Any]) -> tuple[str, str]:
    raw = parsed.get("control")
    if not isinstance(raw, dict):
        return "", ""
    action = str(raw.get("action") or "").strip().lower()
    task_id = str(raw.get("task_id") or "").strip()[:200]
    if action != _WAIT_FOR_SUBAGENT_CONTROL or not task_id:
        return "", ""
    return action, task_id


def _parse_progress_class(
    parsed: dict[str, Any],
    planner_report: dict[str, Any],
) -> str:
    if "progress_class" in parsed:
        value = str(parsed.get("progress_class") or "").strip().lower()
        return value if value in _PROGRESS_CLASSES else "none"
    return "none" if planner_report.get("forward_progress") is False else "evidence"


def _parse_research_result(parsed: dict[str, Any]) -> dict[str, Any] | None:
    return adapt_legacy_research_result_payload(parsed)


def _parse_achievement(
    parsed: dict[str, Any], *, status: str
) -> dict[str, Any] | None:
    """Parse a reviewer-certified project achievement, fail-closed."""
    if status != "done":
        return None
    raw = parsed.get("achievement")
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()[:240]
    goal = str(raw.get("goal") or "").strip()[:2000]
    summary = str(raw.get("summary") or "").strip()[:2000]
    metric_id = str(raw.get("metric_id") or "").strip()[:240]
    evidence = [
        str(item).strip()[:500]
        for item in (raw.get("evidence") or [])[:12]
        if str(item).strip()
    ] if isinstance(raw.get("evidence"), list) else []
    if not title or not goal or not evidence:
        return None
    return {
        "title": title,
        "goal": goal,
        "metric_id": metric_id,
        "summary": summary,
        "evidence": evidence,
    }


def _parse_step_back(parsed: dict) -> dict[str, Any] | None:
    """Parse the reviewer's STEP-BACK reflection on this round's measured result
    (fail-soft → ``None``).

    This is the anti-plan-lock-in channel: a fresh-skeptic critique authored on
    EVERY round that produced a measured result — including a clean success — so
    the planner is forced to consider NEW questions / alternative directions even
    when the plan appears to be working. Returns ``None`` when absent / not a dict
    / carries no usable signal, so the planner simply sees nothing to triage on a
    pure wiring / run-wait round. Caps mirror the schema; a malformed
    ``alt_directions`` entry is dropped, never raised."""
    raw = parsed.get("step_back")
    if not isinstance(raw, dict):
        return None
    supported = str(raw.get("supported_by_results", "") or "").strip().lower()
    if supported not in {"yes", "partial", "no"}:
        supported = ""
    surprises = str(raw.get("surprises", "") or "").strip()[:1200]
    new_questions: list[str] = []
    raw_q = raw.get("new_questions")
    if isinstance(raw_q, list):
        for q in raw_q[:5]:
            text = str(q or "").strip()[:400]
            if text:
                new_questions.append(text)
    alt_directions: list[dict[str, Any]] = []
    raw_alt = raw.get("alt_directions")
    if isinstance(raw_alt, list):
        for entry in raw_alt[:4]:
            if not isinstance(entry, dict):
                continue
            direction = str(entry.get("direction", "") or "").strip()
            if not direction:
                continue
            alt_directions.append({
                "direction": direction[:500],
                "why": str(entry.get("why", "") or "").strip()[:500],
                "cheap_to_test": bool(entry.get("cheap_to_test")),
            })
    if not supported and not surprises and not new_questions and not alt_directions:
        return None
    return {
        "supported_by_results": supported,
        "surprises": surprises,
        "new_questions": new_questions,
        "alt_directions": alt_directions,
    }


def _parse_checklist_feedback(parsed: dict) -> dict[str, Any] | None:
    """Parse the reviewer's ADVISORY checklist feedback (fail-soft → ``None``).

    The reviewer is feedback-only: it never writes the checklist store. This
    structured complaint is surfaced to the Planner, who owns the edits. Returns
    ``None`` when absent/empty so the Planner sees nothing to act on. Capped."""
    raw = parsed.get("checklist_feedback")
    if not isinstance(raw, dict):
        return None
    stage = str(raw.get("stage", "") or "").strip().lower()
    summary = str(raw.get("summary", "") or "").strip()[:600]
    items: list[dict[str, str]] = []
    raw_items = raw.get("items")
    if isinstance(raw_items, list):
        for entry in raw_items[:20]:
            if not isinstance(entry, dict):
                continue
            problem = str(entry.get("problem", "") or "").strip()
            if not problem:
                continue
            items.append({
                "id": str(entry.get("id", "") or "").strip()[:200],
                "problem": problem[:600],
                "suggested_fix": str(entry.get("suggested_fix", "") or "").strip()[:600],
            })
    if not stage and not summary and not items:
        return None
    return {"stage": stage, "summary": summary, "items": items}


def _parse_checkpoint(parsed: dict) -> dict[str, Any]:
    """Parse the reviewer-authored curated working-memory checkpoint.

    Fail-soft: returns ``{}`` when absent/malformed so the runner keeps the
    prior checkpoint rather than wiping memory on a noisy verdict. The live
    runtime no longer consumes this field; it remains for old event parsing.
    """
    raw = parsed.get("checkpoint")
    if not isinstance(raw, dict):
        return {}
    return raw


def _parse_planner_report(parsed: dict, *, status: str, reason: str) -> dict[str, Any]:
    """Parse the reviewer's structured, planner-facing briefing (fail-soft).

    The reviewer authors this so the planner routes from a clean structured
    report. Missing/partial fields are tolerated: we fill sensible defaults
    derived from the verdict rather than rejecting the whole decision.
    """
    raw = parsed.get("planner_report")
    raw = raw if isinstance(raw, dict) else {}
    headline = str(raw.get("headline", "") or "").strip()
    blocker = str(raw.get("blocker", "") or "").strip()
    recommended_next = str(raw.get("recommended_next", "") or "").strip()
    fp = raw.get("forward_progress")
    if isinstance(fp, bool):
        forward_progress = fp
    elif status == "done":
        # A clean ``done`` mission made progress by definition.
        forward_progress = True
    else:
        # Omitted on a NON-done round is UNKNOWN, not auto-False: the stall guard
        # counts only EXPLICIT ``False`` (runner.py raw_forward_progress is False),
        # so None correctly does not stall or trigger the planner's pivot-away rule.
        # (Auto-False here punished honest no-report rounds and bold-but-regressing
        # optimization rounds at the exact moment a structural line is co-tuning.)
        forward_progress = None
    if not headline:
        headline = (reason or "").strip()[:600]
    # Concrete artifacts the planner should OPEN to diagnose what happened
    # (source files, data provenance, NO_GO docs, metric series). Parsed
    # fail-soft: a malformed list/entry is dropped, never rejected.
    evidence_files: list[dict[str, str]] = []
    raw_ev = raw.get("evidence_files")
    if isinstance(raw_ev, list):
        for entry in raw_ev:
            if not isinstance(entry, dict):
                continue
            path = str(entry.get("path", "") or "").strip()
            if not path:
                continue
            evidence_files.append({
                "path": path[:400],
                "why": str(entry.get("why", "") or "").strip()[:600],
            })
            if len(evidence_files) >= 8:
                break
    plan_signal = str(raw.get("plan_signal", "") or "").strip().lower()
    plan_signal_reason = str(raw.get("plan_signal_reason", "") or "").strip()
    if plan_signal != "reconsider" or not plan_signal_reason:
        plan_signal = "continue"
        plan_signal_reason = ""
    return {
        "forward_progress": forward_progress,
        "headline": headline,
        "blocker": blocker,
        "recommended_next": recommended_next,
        "plan_signal": plan_signal,
        "plan_signal_reason": plan_signal_reason[:1200],
        "evidence_files": evidence_files,
    }


def _parse_scope(parsed: dict) -> str:
    value = parsed.get("scope")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"bounded", "final_submission"}:
            return normalized
    return ""


_VALID_FAILURE_CAUSES = frozenset({
    "skill_gap",
    "execution_mistake",
    "ambiguous_objective",
    "environmental",
    "method_failure",
    "unknown",
})
_VALID_FAILURE_SOURCES = frozenset({
    "validator_defect",
    "provenance_binding_defect",
    "infrastructure_failure",
    "scientific_evidence_failure",
})
_VALID_SCIENTIFIC_DECISIONS = frozenset({"go", "pivot", "no_go", "undecided"})


def _parse_failure_source(
    parsed: dict[str, Any],
) -> tuple[str, list[dict[str, str]], str, list[str]]:
    raw = parsed.get("failure_source")
    if not isinstance(raw, dict):
        return "", [], "", []
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in _VALID_FAILURE_SOURCES:
        return "", [], "", []
    evidence: list[dict[str, str]] = []
    rows = raw.get("evidence")
    if isinstance(rows, list):
        for row in rows[:12]:
            if not isinstance(row, dict):
                continue
            artifact = str(row.get("artifact") or "").strip()[:500]
            observation = str(row.get("observation") or "").strip()[:1000]
            if artifact and observation:
                evidence.append({
                    "artifact": artifact,
                    "observation": observation,
                })
    validator_id = str(raw.get("validator_id") or "").strip()[:200]
    repair_paths: list[str] = []
    raw_repair_paths = raw.get("repair_paths")
    if isinstance(raw_repair_paths, list):
        for value in raw_repair_paths[:16]:
            path = str(value or "").strip().replace("\\", "/")[:500]
            if (
                path
                and not path.startswith("/")
                and ".." not in Path(path).parts
                and path not in repair_paths
            ):
                repair_paths.append(path[2:] if path.startswith("./") else path)
    if not evidence or (
        kind == "validator_defect"
        and (not validator_id or not repair_paths)
    ):
        return "", [], "", []
    if kind not in {"validator_defect", "provenance_binding_defect"}:
        repair_paths = []
    return kind, evidence, validator_id, repair_paths


def _parse_scientific_decision(parsed: dict[str, Any]) -> str:
    value = str(parsed.get("scientific_decision") or "").strip().lower()
    return value if value in _VALID_SCIENTIFIC_DECISIONS else ""


def _parse_failure_cause(parsed: dict) -> str:
    """Reviewer's classification of *why* a round failed. Fail-soft: any
    missing/null/unrecognized value normalizes to ``""`` so the skill
    evolution layer simply does nothing rather than acting on noise."""
    value = parsed.get("failure_cause")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _VALID_FAILURE_CAUSES:
            return normalized
    return ""


def _parse_checklist(parsed: dict) -> list[dict[str, Any]]:
    raw = parsed.get("checklist")
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        items.append({
            "item": str(entry.get("item", "")).strip(),
            "satisfied": bool(entry.get("satisfied")),
            "evidence": str(entry.get("evidence", "")).strip(),
        })
    return items


_VALID_SKILL_OPS = frozenset({"create", "update", "delete", "archive"})


def _parse_skill_ops(parsed: dict) -> list[dict[str, Any]]:
    """Reviewer-requested skill-memory operations for this round. Fail-soft:
    any malformed entry is dropped, an unknown ``op`` is dropped, and a
    non-list value yields ``[]`` so the loop simply applies nothing.

    ``create``/``update`` MUST carry ``content`` (the playbook markdown);
    ``delete``/``archive``/``update`` MUST carry ``name``. Entries missing the
    field their op needs are dropped here so downstream never half-applies."""
    raw = parsed.get("skill_ops")
    if not isinstance(raw, list):
        return []
    ops: list[dict[str, Any]] = []
    for entry in raw[:8]:
        if not isinstance(entry, dict):
            continue
        op = str(entry.get("op", "")).strip().lower()
        if op not in _VALID_SKILL_OPS:
            continue
        name = str(entry.get("name", "")).strip()[:200]
        content = str(entry.get("content", "")).strip()[:12000]
        why = str(entry.get("why", "")).strip()[:1000]
        if op in {"create", "update"} and not content:
            continue
        if op in {"update", "delete", "archive"} and not name:
            continue
        ops.append({"op": op, "name": name, "content": content, "why": why})
    return ops


_VALID_WIKI_OPS = frozenset({"create_page", "update_page", "retire_page"})
_VALID_WIKI_CARD_TYPES = frozenset({"technique", "conflict", "pattern"})
_MAX_WIKI_EVIDENCE_SPANS = 6


def _parse_wiki_evidence(raw: Any) -> list[dict[str, str]]:
    """Evidence spans cited by a ``create_page``/``update_page`` wiki op.

    Fail-soft, like ``_parse_wiki_ops`` itself: a non-list value or a
    malformed span yields ``[]``/drops that span rather than raising — the
    WikiRouter's anti-fabrication check (``verify_evidence``) is the real
    gate; this parser only needs to shape the data for it."""
    if not isinstance(raw, list):
        return []
    spans: list[dict[str, str]] = []
    for span in raw[:_MAX_WIKI_EVIDENCE_SPANS]:
        if not isinstance(span, dict):
            continue
        source_id = str(span.get("source_id", "")).strip()[:200]
        quote = str(span.get("quote", "")).strip()[:600]
        if not source_id or not quote:
            continue
        locator = str(span.get("locator", "") or "").strip()[:200]
        spans.append({"source_id": source_id, "quote": quote, "locator": locator})
    return spans


def _parse_wiki_ops(parsed: dict) -> list[dict[str, Any]]:
    """Reviewer-requested project-wiki operations for this round — the wiki's
    structured counterpart to ``_parse_skill_ops`` above, applied by the
    harness with NO Manager gate. Fail-soft: any malformed entry is dropped,
    an unknown ``op`` is dropped, and a non-list value yields ``[]`` so the
    loop simply applies nothing.

    ``create_page``/``update_page`` MUST carry ``id`` and ``body``;
    ``retire_page`` MUST carry ``id``. Entries missing the field their op
    needs are dropped here so downstream never half-applies. The evidence
    spans' anti-fabrication check itself runs later, in the WikiRouter — this
    parser only shapes the data, it never verifies quotes."""
    raw = parsed.get("wiki_ops")
    if not isinstance(raw, list):
        return []
    ops: list[dict[str, Any]] = []
    for entry in raw[:6]:
        if not isinstance(entry, dict):
            continue
        op = str(entry.get("op", "")).strip().lower()
        if op not in _VALID_WIKI_OPS:
            continue
        page_id = str(entry.get("id", "")).strip()[:200]
        if not page_id:
            continue
        card_type = str(entry.get("card_type", "") or "").strip().lower()
        if card_type not in _VALID_WIKI_CARD_TYPES:
            card_type = "technique"
        title = str(entry.get("title", "") or "").strip()[:200]
        status = str(entry.get("status", "") or "").strip().lower()
        body = str(entry.get("body", "") or "").strip()[:8000]
        why = str(entry.get("why", "") or "").strip()[:1000]
        if op in {"create_page", "update_page"} and not body:
            continue
        op_dict: dict[str, Any] = {
            "op": op, "id": page_id, "card_type": card_type,
            "title": title, "why": why,
        }
        if op in {"create_page", "update_page"}:
            op_dict["status"] = status or "scratch"
            op_dict["body"] = body
            op_dict["evidence"] = _parse_wiki_evidence(entry.get("evidence"))
        ops.append(op_dict)
    return ops


def _load_json(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _parse_status(
    parsed: dict,
    *,
    allow_research_pause: bool = False,
) -> ReviewStatus | None:
    allowed_statuses = (
        _BASE_REVIEW_STATUSES | _RESEARCH_PAUSE_STATUSES
        if allow_research_pause
        else _BASE_REVIEW_STATUSES
    )
    for key in ("status", "decision", "action"):
        value = parsed.get(key)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if normalized in allowed_statuses:
            return cast(ReviewStatus, normalized)
    return None


def _parse_required_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _parse_reason(parsed: dict, *, round_summary_markdown: str | None) -> str | None:
    for key in ("reason", "message"):
        text = _parse_required_text(parsed.get(key))
        if text is not None:
            return text
    derived = _derive_reason_from_markdown(
        _parse_optional_text(parsed.get("completion_summary_markdown"))
        or round_summary_markdown
        or ""
    )
    return derived


def _parse_next_action(parsed: dict, *, status: str) -> str | None:
    direct = _parse_required_text(parsed.get("next_action"))
    if direct is not None:
        return direct
    if status == "done":
        return "No further action needed. Objective complete."
    if status == "blocked":
        return "Need additional user input before continuing."
    if status == "replan_requested":
        return "Return control to the Planner/Manager for a replacement plan."
    if status == "continue":
        return "Continue implementation and include clear completion evidence."
    return None


def _parse_operator_question(
    parsed: dict, *, status: str, next_action: str | None, reason: str | None
) -> str:
    """ONE plain-language question for the operator, surfaced verbatim by the
    cockpit when the agent is blocked on an operator decision.

    Prefer the reviewer's own ``operator_question`` (it should phrase it in the
    operator's language). Only emit on ``blocked`` — done/continue never ask. If
    blocked but the reviewer omitted it, fall back to the first sentence of
    ``next_action`` (else ``reason``) so a block is still a human question, never
    a silent dead-end. Capped to the schema's 500 chars. Empty otherwise."""
    if status != "blocked":
        return ""
    direct = _parse_required_text(parsed.get("operator_question"))
    if direct is not None:
        return direct[:500]
    fallback = (next_action or "").strip() or (reason or "").strip()
    if not fallback:
        return ""
    first = fallback.replace("\n", " ").split("。")[0].split(". ")[0].strip()
    return (first or fallback)[:500]


def _parse_round_summary(parsed: dict) -> str | None:
    direct = _parse_required_text(parsed.get("round_summary_markdown"))
    if direct is not None:
        return direct
    summary = _parse_required_text(parsed.get("summary")) or _parse_required_text(parsed.get("message"))
    if summary is None:
        return None
    return f"# Review Summary\n\n- {summary}\n"


def _parse_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()


def _derive_reason_from_markdown(text: str) -> str | None:
    normalized_lines: list[str] = []
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("- "):
            line = line[2:].strip()
        if line.startswith("**") and line.endswith("**") and len(line) > 4:
            line = line[2:-2].strip()
        normalized_lines.append(line)
    if not normalized_lines:
        return None
    candidate = normalized_lines[0]
    return candidate[:300].strip() or None
