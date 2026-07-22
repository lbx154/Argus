"""Manager vertical decision + domain authoring: prompts and strict parsers.

``Manager.decide_vertical`` first makes one compact, tool-free routing request.
A clear existing vertical commits immediately; uncertainty or a potentially new
domain escalates once to a bounded, read-only repository investigation. This
Prompt bodies live in :mod:`argus_skill.roles.prompts.manager` and are
re-exported here for source compatibility; this module owns their fail-closed
parsers.

The proposed domain (when authored) is persisted as project-local DATA by
:func:`argus_skill.verticals._data_domain.write_data_domain`; the per-stage
checklist is authored later by the Planner. Parsing is fail-closed to ``None``
on any ambiguity (bad JSON, no usable stages, an un-sluggable/unknown name),
but the CALLER is FAIL-HARD: ``Manager.decide_vertical`` raises
``VerticalDecisionError`` on a ``None`` parse — there is NO silent fallback to
the research default.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Sequence

from ..roles.prompts.manager import (
    build_domain_author_prompt,
    build_fast_vertical_decision_prompt,
    build_research_target_prompt,
    build_vertical_decision_prompt,
)
from .live_view import LiveViewDecision, parse_live_view

_NAME_SANITIZE_RE = re.compile(r"[^a-z0-9_]+")
_MIN_STAGES = 2
_MAX_STAGES = 10


class VerticalDecisionError(RuntimeError):
    """Raised when the Manager cannot decide a vertical for a task.

    Fail-hard: no backend/runner, or a model reply that is missing or not a
    valid choice. There is NO silent fallback to the research default — the
    Manager must produce a real decision or the mission fails loudly.
    """


@dataclass
class DomainProposal:
    """A Manager-authored new domain (validated + sluggified)."""

    name: str
    stages: list[str]
    rationale: str = ""
    confidence: float = 0.0
    execution_task: str = ""


def _loads_first_json(text: str) -> Any:
    cleaned = (text or "").strip()
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


def _sluggify_name(raw: object) -> str:
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    s = _NAME_SANITIZE_RE.sub("_", s).strip("_")
    return s


def _dedupe_name(name: str, taken: set[str]) -> str | None:
    """Return ``name`` or a numeric-suffixed variant not in ``taken``; ``None`` if
    it cannot be made unique within a small bound."""
    if name not in taken:
        return name
    for i in range(2, 50):
        cand = f"{name}_{i}"
        if cand not in taken:
            return cand
    return None


def parse_domain_proposal(
    raw_text: str,
    *,
    known_verticals: Sequence[str] = (),
    existing_data_domains: Sequence[str] = (),
) -> DomainProposal | None:
    """Validate the Manager's JSON proposal; fail-closed to ``None`` on ambiguity.

    Rules: valid JSON object; ``stages`` is a list of ``_MIN_STAGES``..
    ``_MAX_STAGES`` slugs (deduped, order preserved); ``name`` sluggifies to
    a non-empty slug that does not collide with a preset vertical or an existing
    data domain (a numeric suffix is appended on collision). Anything else →
    ``None``.
    """
    obj = _loads_first_json(raw_text)
    if not isinstance(obj, dict):
        return None

    raw_stages = obj.get("stages")
    if not isinstance(raw_stages, list):
        return None
    stages: list[str] = []
    for s in raw_stages:
        slug = _sluggify_name(s)
        if slug and slug not in stages:
            stages.append(slug)
    if not (_MIN_STAGES <= len(stages) <= _MAX_STAGES):
        return None

    # Accept either "name" or "vertical" as the slug key — the two-shape
    # vertical-decision prompt uses "vertical", the standalone author prompt uses
    # "name"; taking both means a model that fills the wrong key never fails
    # closed (which would wedge the task with no fallback).
    name = _sluggify_name(obj.get("name") or obj.get("vertical"))
    if not name:
        return None
    taken = {str(v).strip().lower() for v in known_verticals}
    taken |= {str(v).strip().lower() for v in existing_data_domains}
    unique = _dedupe_name(name, taken)
    if unique is None:
        return None

    rationale = str(obj.get("rationale") or "").strip()[:600]
    raw_conf = obj.get("confidence")
    confidence = float(raw_conf) if isinstance(raw_conf, (int, float)) else 0.0
    raw_execution_task = obj.get("execution_task")
    execution_task = (
        raw_execution_task.strip()
        if isinstance(raw_execution_task, str)
        else ""
    )

    return DomainProposal(
        name=unique,
        stages=stages,
        rationale=rationale,
        confidence=confidence,
        execution_task=execution_task,
    )


__all__ = [
    "DomainProposal",
    "FastVerticalRoute",
    "VerticalDecision",
    "VerticalDecisionError",
    "build_domain_author_prompt",
    "build_fast_vertical_decision_prompt",
    "build_research_target_prompt",
    "build_vertical_decision_prompt",
    "parse_domain_proposal",
    "parse_fast_vertical_decision",
    "parse_research_target_level",
    "parse_vertical_decision",
]


@dataclass
class VerticalDecision:
    """The Manager's committable choice of vertical for a task.

    ``choice`` is ``"existing"`` (reuse a known built-in vertical or an existing
    project data domain) or ``"new"`` (author a fresh data domain). ``vertical``
    is the chosen/authored name in both cases; ``proposal`` carries the authored
    domain (stages + slug) only when ``choice == "new"``.
    """

    choice: str
    vertical: str
    # Orthogonal execution topology chosen by Manager; never encoded as a vertical.
    workflow_mode: str = "staged"
    proposal: DomainProposal | None = None
    # Optional, independently-grounded choice of which workspace files the Web
    # cockpit should keep beside the live event stream. ``live_view_decided``
    # distinguishes an explicit null (clear the panel) from an older backend
    # that returned the pre-live-view verdict shape (preserve current choice).
    live_view: LiveViewDecision | None = None
    live_view_decided: bool = False
    # Planner/Engineer handoff. Fast routing preserves the operator task verbatim;
    # grounded/legacy callers may still supply an explicit cleaned handoff.
    execution_task: str = ""
    # Optional research success bar, decided from the operator's requested
    # outcome rather than re-inferred by Planner/Reviewer/Life independently.
    research_target_level: str = ""
    # Publication venue explicitly named by the operator for research work.
    # Empty means "not explicitly selected"; venue discovery remains a separate
    # bounded research operation rather than a keyword guess in the harness.
    target_venue: str = ""
    # Raw validated Manager response, applied only when the decision commits.
    rendering_response: str = ""


@dataclass(frozen=True)
class FastVerticalRoute:
    """Tool-free first-pass route returned before any repository inspection.

    ``needs_grounding`` is true when the model cannot confidently reuse an
    existing vertical from the task text alone (including when it believes a
    new data domain may be required).  The grounded fallback remains the only
    path allowed to inspect repository files or author a domain.
    """

    needs_grounding: bool
    vertical: str = ""
    workflow_mode: str = "staged"
    confidence: float = 0.0
    rationale: str = ""
    research_target_level: str = ""
    target_venue: str = ""


def parse_fast_vertical_decision(
    raw_text: str,
    *,
    known_verticals: Sequence[str] = (),
    existing_data_domains: Sequence[str] = (),
    research_target_verticals: Sequence[str] = (),
) -> FastVerticalRoute | None:
    """Parse a tool-free route; invalid output fails closed to grounding."""
    obj = _loads_first_json(raw_text)
    if not isinstance(obj, dict):
        return None
    choice = str(obj.get("choice") or "").strip().lower()
    raw_confidence = obj.get("confidence")
    if not isinstance(raw_confidence, (int, float)):
        return None
    confidence = float(raw_confidence)
    if not 0.0 <= confidence <= 1.0:
        return None
    rationale = str(obj.get("rationale") or "").strip()[:300]
    if choice in {"grounded", "new", "uncertain"}:
        return FastVerticalRoute(
            needs_grounding=True,
            confidence=confidence,
            rationale=rationale,
        )
    if choice != "existing":
        return None
    raw_name = _sluggify_name(obj.get("vertical") or obj.get("name"))
    legacy_direct = raw_name == "direct"
    name = "software" if legacy_direct else raw_name
    known = {str(v).strip().lower() for v in known_verticals}
    known |= {str(v).strip().lower() for v in existing_data_domains}
    if not name or name not in known:
        return None
    workflow_mode = str(obj.get("workflow_mode") or "").strip().lower()
    if not workflow_mode:
        workflow_mode = "direct" if legacy_direct else "staged"
    if workflow_mode not in {"direct", "staged"}:
        return None
    targeted = {
        str(value or "").strip().lower()
        for value in research_target_verticals
    }
    target_level = str(obj.get("research_target_level") or "").strip().lower()
    if name in targeted and target_level not in {
        "exploratory",
        "publishable",
        "doctoral",
    }:
        return None
    if name not in targeted:
        target_level = ""
    target_venue = " ".join(
        str(obj.get("target_venue") or "").strip().split()
    )[:100]
    if name != "research":
        target_venue = ""
    return FastVerticalRoute(
        needs_grounding=False,
        vertical=name,
        workflow_mode=workflow_mode,
        confidence=confidence,
        rationale=rationale,
        research_target_level=target_level,
        target_venue=target_venue,
    )


def parse_research_target_level(
    raw_text: str,
    *,
    supported_levels: Sequence[str] = (
        "exploratory",
        "publishable",
        "doctoral",
    ),
) -> str | None:
    """Parse the Manager's explicit research-target verdict, fail-closed."""
    obj = _loads_first_json(raw_text)
    if not isinstance(obj, dict):
        return None
    level = str(obj.get("research_target_level") or "").strip().lower()
    allowed = {str(value or "").strip().lower() for value in supported_levels}
    return level if level in allowed else None


def parse_vertical_decision(
    raw_text: str,
    *,
    known_verticals: Sequence[str] = (),
    existing_data_domains: Sequence[str] = (),
    research_target_verticals: Sequence[str] = (),
    default_execution_task: str = "",
) -> VerticalDecision | None:
    """Validate the Manager's vertical-decision JSON; fail-closed to ``None``.

    ``choice == "existing"`` requires ``vertical`` to name a known built-in or an
    existing data domain (normalized). ``choice == "new"`` reuses
    :func:`parse_domain_proposal`. Any ambiguity → ``None`` (the caller raises).
    """
    obj = _loads_first_json(raw_text)
    if not isinstance(obj, dict):
        return None
    parsed_live_view = parse_live_view(obj.get("live_view"))
    raw_execution_task = obj.get("execution_task")
    execution_task = (
        raw_execution_task.strip()
        if isinstance(raw_execution_task, str)
        else ""
    )
    if not execution_task:
        execution_task = (default_execution_task or "").strip()
    if not execution_task:
        return None
    live_view_decided = "live_view" in obj and (
        obj.get("live_view") is None or parsed_live_view is not None
    )
    choice = str(obj.get("choice") or "").strip().lower()
    raw_vertical_name = _sluggify_name(obj.get("vertical") or obj.get("name"))
    legacy_direct = raw_vertical_name == "direct"
    workflow_mode = str(obj.get("workflow_mode") or "").strip().lower()
    if not workflow_mode:
        workflow_mode = "direct" if legacy_direct else "staged"
    if workflow_mode not in {"direct", "staged"}:
        return None
    if choice == "existing":
        name = "software" if legacy_direct else raw_vertical_name
        target_level = str(obj.get("research_target_level") or "").strip().lower()
        known = {str(v).strip().lower() for v in known_verticals}
        known |= {str(v).strip().lower() for v in existing_data_domains}
        targeted = {
            str(value or "").strip().lower()
            for value in research_target_verticals
        }
        if name in targeted and target_level not in {
            "exploratory",
            "publishable",
            "doctoral",
        }:
            return None
        if name not in targeted:
            target_level = ""
        target_venue = " ".join(
            str(obj.get("target_venue") or "").strip().split()
        )[:100]
        if name != "research":
            target_venue = ""
        if name and name in known:
            return VerticalDecision(
                choice="existing",
                vertical=name,
                workflow_mode=workflow_mode,
                proposal=None,
                live_view=parsed_live_view,
                live_view_decided=live_view_decided,
                execution_task=execution_task,
                research_target_level=target_level,
                target_venue=target_venue,
            )
        return None
    if choice == "new":
        proposal = parse_domain_proposal(
            raw_text,
            known_verticals=known_verticals,
            existing_data_domains=existing_data_domains,
        )
        if proposal is None:
            return None
        return VerticalDecision(
            choice="new",
            vertical=proposal.name,
            workflow_mode=workflow_mode,
            proposal=proposal,
            live_view=parsed_live_view,
            live_view_decided=live_view_decided,
            execution_task=execution_task,
        )
    return None
