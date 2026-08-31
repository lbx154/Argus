"""Manager vertical decision + domain authoring: prompts and strict parsers.

``Manager.decide_vertical`` always makes one bounded, repository-grounded
routing request before any vertical can commit. This module owns the
fail-closed parsers for that decision.

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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Sequence

from ..roles.prompts.manager import (
    build_research_target_prompt,
    build_vertical_decision_prompt,
)
from .live_view import LiveViewDecision, parse_live_view

_NAME_SANITIZE_RE = re.compile(r"[^a-z0-9_]+")
_MIN_STAGES = 2
# Generated stage count is a persisted schema bound, not a work deadline.
_MAX_STAGES = 10


class VerticalDecisionError(RuntimeError):
    """Raised when the Manager cannot decide a vertical for a task.

    Fail-hard: no backend/runner, or a model reply that is missing or not a
    valid choice. There is NO silent fallback to the research default — the
    Manager must produce a real decision or the mission fails loudly.
    """

    def __init__(
        self,
        cause: str,
        *,
        phase: Literal["backend", "parse", "contract", "timeout"] = "contract",
        contract_field: str = "",
        attempts: int = 1,
        model_reply_snippet: str = "",
        backend_error: str = "",
        task: str = "",
    ) -> None:
        self.phase = phase
        self.cause = str(cause or "unknown failure").strip()
        self.contract_field = str(contract_field or "").strip()
        self.attempts = max(1, int(attempts or 1))
        self.model_reply_snippet = sanitize_model_reply_snippet(
            model_reply_snippet
        )
        self.backend_error = str(backend_error or "").strip()
        self.task_excerpt = _bounded_task_excerpt(task)
        message = f"routing failed [{self.phase}]: {self.cause}"
        if self.attempts > 1:
            message += f" (attempts={self.attempts})"
        if self.task_excerpt:
            message += f"; task={self.task_excerpt!r}"
        super().__init__(message)


class ManagerClassificationContractError(VerticalDecisionError):
    """A model reply violated a Manager classification capability clause.

    This is intentionally narrower than :class:`VerticalDecisionError`:
    provider timeouts, 429/5xx responses, auth failures, and configuration
    errors must never be counted as evidence that a model lacks the role's
    structured-decision or repository-grounding capability.
    """

    def __init__(self, cause: str, *, clause: str, **details: Any) -> None:
        super().__init__(cause, **details)
        self.clause = clause
        self.model_id = ""
        self.consecutive_count = 0

    def attach_streak(self, *, model_id: str, consecutive_count: int) -> None:
        self.model_id = model_id
        self.consecutive_count = consecutive_count


def _bounded_task_excerpt(task: object, *, limit: int = 80) -> str:
    value = " ".join(str(task or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def sanitize_model_reply_snippet(reply: object, *, limit: int = 300) -> str:
    """Return a secret-redacted, single-line and strictly bounded reply excerpt."""
    from ..core.secret_guard import known_secret_values, redact_secrets_text

    if isinstance(reply, Mapping):
        try:
            value = json.dumps(reply, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            value = str(reply)
    else:
        value = str(reply or "")
    value = redact_secrets_text(
        value,
        known_values=known_secret_values(),
    )
    value = " ".join(value.split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


@dataclass(frozen=True)
class ContractViolation:
    """The first concrete field-level reason a routing reply was rejected."""

    phase: Literal["parse", "contract"]
    field: str
    value: object
    expected: str

    @property
    def cause(self) -> str:
        if self.value is _MISSING:
            seen = "was missing"
        else:
            rendered = sanitize_model_reply_snippet(self.value, limit=100)
            seen = f"got {json.dumps(rendered, ensure_ascii=False)}"
        return f"{self.field} {seen}, expected {self.expected}"


_MISSING = object()


@dataclass
class DomainProposal:
    """A Manager-authored new domain (validated + sluggified)."""

    name: str
    stages: list[str]
    rationale: str = ""
    confidence: float = 0.0
    execution_task: str = ""


_DECISION_KEYS = (
    "CHOICE",
    "VERTICAL",
    "NAME",
    "DOMAIN",
    "WORKFLOW_MODE",
    "CONFIDENCE",
    "RESEARCH_TARGET_LEVEL",
    "RESEARCH_DIRECTION_MODE",
    "TARGET_VENUE",
    "RATIONALE",
    "EXECUTION_TASK",
    "REQUIRE_INDEPENDENT_REVIEW",
    "STAGES",
    "PRECISE_CONSTRAINTS",
    "EXCLUSIONS",
    "AMBIGUITIES",
    "LIVE_VIEW_PATHS",
    "LIVE_VIEW_TITLE",
    "LIVE_VIEW_REASON",
)


def _decision_fields(
    raw_text: str | Mapping[str, Any],
) -> dict[str, Any] | None:
    """The Manager's decision, read from named lines in whatever it wrote.

    Operator directive: no role is forced to emit a JSON Schema. The Manager
    reasons and explains in prose and states its conclusions on named lines;
    this lifts those lines into the same field names the validation below
    already uses, so every check that guarded the JSON path still runs.

    A volunteered JSON object is still accepted. That is a compatibility door
    for daemons mid-flight on an older prompt, not a second contract — nothing
    asks for it.
    """
    if isinstance(raw_text, Mapping):
        return dict(raw_text)

    from ..core.role_reply import (
        legacy_json_object,
        read_bool,
        read_key_values,
        read_list,
        read_list_semicolon,
        read_optional,
    )

    values = read_key_values(raw_text, _DECISION_KEYS)
    if not values:
        return legacy_json_object(raw_text)

    fields: dict[str, Any] = {}
    for key in (
        "CHOICE",
        "VERTICAL",
        "NAME",
        "WORKFLOW_MODE",
        "RESEARCH_TARGET_LEVEL",
        "RESEARCH_DIRECTION_MODE",
    ):
        if key in values:
            fields[key.lower()] = read_optional(values, key)
    for key in ("DOMAIN", "TARGET_VENUE", "RATIONALE", "EXECUTION_TASK"):
        if key in values:
            fields[key.lower()] = read_optional(values, key)
    if "CONFIDENCE" in values:
        try:
            fields["confidence"] = float(values["CONFIDENCE"])
        except ValueError:
            # Left absent rather than defaulted: the callers treat a missing
            # confidence as "not a usable answer" and escalate, which is the
            # correct response to a number we could not read.
            pass
    if "REQUIRE_INDEPENDENT_REVIEW" in values:
        fields["require_independent_review"] = read_bool(
            values,
            "REQUIRE_INDEPENDENT_REVIEW",
        )
    if "STAGES" in values:
        fields["stages"] = list(read_list(values, "STAGES"))
    # The three requirement lines. `_stated_requirements` reads them off this
    # dict and wants real lists, the same shape a volunteered JSON object
    # supplies, so one reader serves both doors. Absent stays absent: "the
    # Manager did not answer" and "the Manager answered none" reach the
    # contract differently, and only the second may clear a standing clause.
    #
    # `;` only, not `read_list`'s `;|`. These lines carry the operator's own
    # words, and `|` is absolute value: run 17 stated the constraint
    # `sum_{i=1}^5 |z_i|^2 = 5` and the contract recorded three clauses reading
    # `constraint sum_{i=1}^5`, `z_i` and `^2 = 5`.
    for key in ("PRECISE_CONSTRAINTS", "EXCLUSIONS", "AMBIGUITIES"):
        if key in values:
            fields[key.lower()] = list(read_list_semicolon(values, key))
    paths = read_list(values, "LIVE_VIEW_PATHS")
    if paths:
        fields["live_view"] = {
            "paths": list(paths),
            "title": read_optional(values, "LIVE_VIEW_TITLE"),
            "reason": read_optional(values, "LIVE_VIEW_REASON"),
        }
    elif "LIVE_VIEW_PATHS" in values:
        # An explicit empty answer means "clear the panel", which callers
        # distinguish from never having been asked.
        fields["live_view"] = None
    return fields


def _sluggify_name(raw: object) -> str:
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    s = _NAME_SANITIZE_RE.sub("_", s).strip("_")
    return s


def _canonical_existing_vertical(value: object) -> tuple[str, bool]:
    raw_name = _sluggify_name(value)
    legacy_direct = raw_name == "direct"
    return ("software" if legacy_direct else raw_name, legacy_direct)


def _resolve_existing_identity(
    obj: dict,
    *,
    persisted_vertical: str = "",
    persisted_workflow_mode: str = "",
    allow_persisted_change: bool = False,
) -> tuple[str, str, bool] | None:
    name, legacy_direct = _canonical_existing_vertical(
        obj.get("vertical") or obj.get("name")
    )
    prior_name, prior_legacy_direct = _canonical_existing_vertical(
        persisted_vertical
    )
    same_identity = bool(name and prior_name and name == prior_name)
    prior_mode = str(persisted_workflow_mode or "").strip().lower()
    if not prior_mode and prior_legacy_direct:
        prior_mode = "direct"
    if prior_mode not in {"", "direct", "staged"}:
        prior_mode = ""

    raw_mode = str(obj.get("workflow_mode") or "").strip().lower()
    if raw_mode and raw_mode not in {"direct", "staged"}:
        return None
    if legacy_direct:
        if raw_mode and raw_mode != "direct":
            return None
        workflow_mode = "direct"
    elif raw_mode:
        workflow_mode = raw_mode
    elif same_identity and prior_mode:
        workflow_mode = prior_mode
    else:
        workflow_mode = "staged"
    if (
        same_identity
        and prior_mode
        and workflow_mode != prior_mode
        and not allow_persisted_change
    ):
        return None
    return name, workflow_mode, same_identity


def _resolve_research_target(
    obj: dict,
    *,
    name: str,
    targeted: set[str],
    same_persisted_identity: bool,
    persisted_research_target_level: str,
    allow_persisted_change: bool = False,
) -> str | None:
    target_level = str(obj.get("research_target_level") or "").strip().lower()
    prior_target = str(persisted_research_target_level or "").strip().lower()
    if name not in targeted:
        return ""
    if not target_level and same_persisted_identity:
        target_level = prior_target
    if target_level not in {"exploratory", "publishable", "doctoral"}:
        return None
    if (
        same_persisted_identity
        and prior_target
        and target_level != prior_target
        and not allow_persisted_change
    ):
        return None
    return target_level


def _resolve_research_direction(
    obj: dict,
    *,
    name: str,
    target_level: str,
    same_persisted_identity: bool,
    persisted_research_direction_mode: str,
) -> str | None:
    from ..core.research_contract import normalize_research_direction_mode

    if name != "research":
        return ""
    prior_direction = normalize_research_direction_mode(
        persisted_research_direction_mode
    )
    direction = normalize_research_direction_mode(
        obj.get("research_direction_mode")
    )
    if (
        same_persisted_identity
        and prior_direction == "broad"
        and direction == "locked"
    ):
        return None
    if direction is None and same_persisted_identity:
        direction = prior_direction
    if direction == "locked" and not prior_direction:
        direction = "broad"
    if direction is None:
        direction = "broad" if target_level in {"publishable", "doctoral"} else "locked"
    return direction


def _resolve_existing_domain(
    obj: dict,
    *,
    name: str,
    same_persisted_identity: bool,
    persisted_domain: str,
    allow_persisted_change: bool = False,
) -> str | None:
    domain = _sluggify_name(obj.get("domain"))
    prior_domain = _sluggify_name(persisted_domain)
    if name != "research":
        return None if domain else ""
    if not domain and same_persisted_identity:
        domain = prior_domain
    if (
        same_persisted_identity
        and prior_domain
        and domain != prior_domain
        and not allow_persisted_change
    ):
        return None
    return domain


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
    raw_text: str | Mapping[str, Any],
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
    obj = _decision_fields(raw_text)
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
    "ContractViolation",
    "DomainProposal",
    "FastVerticalRoute",
    "VerticalDecision",
    "VerticalDecisionError",
    "build_research_target_prompt",
    "build_vertical_decision_prompt",
    "parse_domain_proposal",
    "parse_fast_vertical_decision",
    "parse_research_target_level",
    "parse_vertical_decision",
    "research_target_contract_violation",
    "sanitize_model_reply_snippet",
    "vertical_decision_contract_violation",
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
    # Optional built-in domain overlay. Only the research workflow accepts one.
    domain: str = ""
    # Orthogonal execution topology chosen by Manager; never encoded as a vertical.
    workflow_mode: str = "staged"
    proposal: DomainProposal | None = None
    # Existing project data domains may be refined in place when their stage
    # skeleton is materially too weak for the matching recurring capability.
    adapted_stages: tuple[str, ...] = ()
    adaptation_reason: str = ""
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
    research_direction_mode: str = ""
    # Publication venue explicitly named by the operator for research work.
    # Empty means "not explicitly selected"; venue discovery remains a separate
    # bounded research operation rather than a keyword guess in the harness.
    target_venue: str = ""
    require_independent_review: bool = True
    # Requirements the operator actually stated, split by how they can be
    # checked. `precise_constraints` are mechanically checkable things the
    # operator chose (a number, a baseline, a budget) and are recorded verbatim;
    # the Manager must never invent one, because a constraint nobody asked for
    # becomes a goal nobody agreed to. Where a number is clearly needed but was
    # not given, it belongs in `ambiguities` — a question for the operator, not
    # a guess.
    precise_constraints: tuple[str, ...] = ()
    # What the operator ruled out. Kept beside the constraints rather than
    # folded into them: `render_contract` gives exclusions their own heading,
    # and "do not do X" read as a requirement is the opposite instruction.
    exclusions: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()
    # Raw validated Manager response, applied only when the decision commits.
    rendering_response: str = ""


@dataclass(frozen=True)
class FastVerticalRoute:
    """Legacy tool-free first-pass route retained for compatibility.

    Formal Manager routing no longer consumes this shape: every project task
    uses the grounded decision path before persistence.
    """

    needs_grounding: bool
    vertical: str = ""
    domain: str = ""
    workflow_mode: str = "staged"
    confidence: float = 0.0
    rationale: str = ""
    research_target_level: str = ""
    research_direction_mode: str = ""
    target_venue: str = ""
    require_independent_review: bool = True
    precise_constraints: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()


def parse_fast_vertical_decision(
    raw_text: str | Mapping[str, Any],
    *,
    known_verticals: Sequence[str] = (),
    known_domains: Sequence[str] = (),
    existing_data_domains: Sequence[str] = (),
    research_target_verticals: Sequence[str] = (),
    persisted_vertical: str = "",
    persisted_workflow_mode: str = "",
    persisted_domain: str = "",
    persisted_research_target_level: str = "",
    persisted_research_direction_mode: str = "",
    allow_persisted_change: bool = False,
) -> FastVerticalRoute | None:
    """Parse a tool-free route; invalid output fails closed to grounding."""
    obj = _decision_fields(raw_text)
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
    identity = _resolve_existing_identity(
        obj,
        persisted_vertical=persisted_vertical,
        persisted_workflow_mode=persisted_workflow_mode,
        allow_persisted_change=allow_persisted_change,
    )
    if identity is None:
        return None
    name, workflow_mode, same_persisted_identity = identity
    known = {str(v).strip().lower() for v in known_verticals}
    known |= {str(v).strip().lower() for v in existing_data_domains}
    if not name or name not in known:
        return None
    domain = _resolve_existing_domain(
        obj,
        name=name,
        same_persisted_identity=same_persisted_identity,
        persisted_domain=persisted_domain,
        allow_persisted_change=allow_persisted_change,
    )
    if domain is None:
        return None
    allowed_domains = {
        str(value or "").strip().lower() for value in known_domains
    }
    if name == "research" and domain and domain not in allowed_domains:
        return None
    targeted = {
        str(value or "").strip().lower()
        for value in research_target_verticals
    }
    target_level = _resolve_research_target(
        obj,
        name=name,
        targeted=targeted,
        same_persisted_identity=same_persisted_identity,
        persisted_research_target_level=persisted_research_target_level,
        allow_persisted_change=allow_persisted_change,
    )
    if target_level is None:
        return None
    direction_mode = _resolve_research_direction(
        obj,
        name=name,
        target_level=target_level,
        same_persisted_identity=same_persisted_identity,
        persisted_research_direction_mode=persisted_research_direction_mode,
    )
    if direction_mode is None:
        return None
    target_venue = " ".join(
        str(obj.get("target_venue") or "").strip().split()
    )[:100]
    if name != "research":
        target_venue = ""
    stated, exclusions, ambiguities = _stated_requirements(obj)
    return FastVerticalRoute(
        needs_grounding=False,
        vertical=name,
        domain=domain,
        workflow_mode=workflow_mode,
        confidence=confidence,
        rationale=rationale,
        research_target_level=target_level,
        research_direction_mode=direction_mode,
        target_venue=target_venue,
        require_independent_review=(
            obj.get("require_independent_review", True) is not False
        ),
        precise_constraints=stated,
        exclusions=exclusions,
        ambiguities=ambiguities,
    )


def _stated_requirements(
    obj: dict,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """The operator-stated constraints, exclusions and open questions.

    Bounded and de-duplicated but otherwise passed through verbatim. The harness
    must not reword a constraint: the operator's phrasing is the thing that was
    agreed to, and a paraphrase is already a revision.
    """

    def _clean(key: str) -> tuple[str, ...]:
        raw = obj.get(key)
        if not isinstance(raw, list):
            return ()
        return tuple(
            dict.fromkeys(
                " ".join(str(value).split())[:400]
                for value in raw
                if isinstance(value, str) and str(value).strip()
            )
        )[:12]

    return _clean("precise_constraints"), _clean("exclusions"), _clean("ambiguities")


def parse_research_target_level(
    raw_text: str | Mapping[str, Any],
    *,
    supported_levels: Sequence[str] = (
        "exploratory",
        "publishable",
        "doctoral",
    ),
) -> str | None:
    """Parse the Manager's explicit research-target verdict, fail-closed."""
    obj = _decision_fields(raw_text)
    if not isinstance(obj, dict):
        return None
    level = str(obj.get("research_target_level") or "").strip().lower()
    allowed = {str(value or "").strip().lower() for value in supported_levels}
    return level if level in allowed else None


def research_target_contract_violation(
    raw_text: str | Mapping[str, Any],
    *,
    supported_levels: Sequence[str],
) -> ContractViolation:
    obj = _decision_fields(raw_text)
    expected = "|".join(str(value) for value in supported_levels)
    if not isinstance(obj, dict):
        return ContractViolation("parse", "model_reply", raw_text, "named decision fields")
    value = obj.get("research_target_level", _MISSING)
    return ContractViolation("contract", "research_target_level", value, expected)


def vertical_decision_contract_violation(
    raw_text: str | Mapping[str, Any],
    *,
    known_verticals: Sequence[str] = (),
    known_domains: Sequence[str] = (),
    existing_data_domains: Sequence[str] = (),
    research_target_verticals: Sequence[str] = (),
    default_execution_task: str = "",
    persisted_vertical: str = "",
    persisted_workflow_mode: str = "",
    persisted_domain: str = "",
    persisted_research_target_level: str = "",
    persisted_research_direction_mode: str = "",
    allow_persisted_change: bool = False,
) -> ContractViolation:
    """Explain a failed :func:`parse_vertical_decision` without changing it.

    The parser remains a fail-closed compatibility API.  Its caller invokes
    this only after receiving ``None``, so diagnostics cannot admit output that
    the established contract rejected.
    """
    obj = _decision_fields(raw_text)
    if not isinstance(obj, dict):
        return ContractViolation(
            "parse",
            "model_reply",
            raw_text,
            "named decision fields or a JSON object",
        )
    raw_execution_task = obj.get("execution_task", _MISSING)
    if not (
        isinstance(raw_execution_task, str) and raw_execution_task.strip()
    ) and not str(default_execution_task or "").strip():
        return ContractViolation(
            "contract", "execution_task", raw_execution_task, "non-empty text"
        )
    choice = str(obj.get("choice") or "").strip().lower()
    if choice not in {"existing", "new"}:
        return ContractViolation(
            "contract", "choice", obj.get("choice", _MISSING), "existing|new"
        )
    workflow_mode = str(obj.get("workflow_mode") or "").strip().lower()
    if workflow_mode and workflow_mode not in {"direct", "staged"}:
        return ContractViolation(
            "contract",
            "workflow_mode",
            obj.get("workflow_mode"),
            "direct|staged",
        )
    if choice == "new":
        raw_stages = obj.get("stages", _MISSING)
        if not isinstance(raw_stages, list):
            return ContractViolation(
                "contract", "stages", raw_stages, "a list of 2..10 unique stage names"
            )
        stages = tuple(
            dict.fromkeys(
                slug for value in raw_stages if (slug := _sluggify_name(value))
            )
        )
        if not (_MIN_STAGES <= len(stages) <= _MAX_STAGES):
            return ContractViolation(
                "contract", "stages", raw_stages, "2..10 unique stage names"
            )
        name = obj.get("name") or obj.get("vertical", _MISSING)
        if not _sluggify_name(name):
            return ContractViolation(
                "contract", "vertical", name, "a non-empty domain slug"
            )
        return ContractViolation(
            "contract", "vertical", name, "a unique domain slug"
        )

    identity = _resolve_existing_identity(
        obj,
        persisted_vertical=persisted_vertical,
        persisted_workflow_mode=persisted_workflow_mode,
        allow_persisted_change=allow_persisted_change,
    )
    if identity is None:
        resolved_name, legacy_direct = _canonical_existing_vertical(
            obj.get("vertical") or obj.get("name")
        )
        persisted_name, _ = _canonical_existing_vertical(persisted_vertical)
        mode_conflict = bool(
            legacy_direct
            or workflow_mode
            or (resolved_name and resolved_name == persisted_name)
        )
        field = "workflow_mode" if mode_conflict else "vertical"
        value = (
            "direct"
            if legacy_direct and not workflow_mode
            else obj.get(field, _MISSING)
        )
        return ContractViolation(
            "contract",
            field,
            value,
            "the persisted route contract",
        )
    name, _mode, same_persisted_identity = identity
    known = {str(value).strip().lower() for value in known_verticals}
    known |= {str(value).strip().lower() for value in existing_data_domains}
    if not name or name not in known:
        return ContractViolation(
            "contract",
            "vertical",
            obj.get("vertical", obj.get("name", _MISSING)),
            "one of " + "|".join(sorted(known)),
        )
    domain = _resolve_existing_domain(
        obj,
        name=name,
        same_persisted_identity=same_persisted_identity,
        persisted_domain=persisted_domain,
        allow_persisted_change=allow_persisted_change,
    )
    if domain is None:
        return ContractViolation(
            "contract",
            "domain",
            obj.get("domain", _MISSING),
            "empty outside research or unchanged from the persisted contract",
        )
    allowed_domains = {str(value or "").strip().lower() for value in known_domains}
    if name == "research" and domain and domain not in allowed_domains:
        return ContractViolation(
            "contract",
            "domain",
            obj.get("domain"),
            "one of " + "|".join(sorted(allowed_domains)),
        )
    targeted = {
        str(value or "").strip().lower() for value in research_target_verticals
    }
    target_level = _resolve_research_target(
        obj,
        name=name,
        targeted=targeted,
        same_persisted_identity=same_persisted_identity,
        persisted_research_target_level=persisted_research_target_level,
        allow_persisted_change=allow_persisted_change,
    )
    if target_level is None:
        return ContractViolation(
            "contract",
            "research_target_level",
            obj.get("research_target_level", _MISSING),
            "exploratory|publishable|doctoral and the persisted route contract",
        )
    direction = _resolve_research_direction(
        obj,
        name=name,
        target_level=target_level,
        same_persisted_identity=same_persisted_identity,
        persisted_research_direction_mode=persisted_research_direction_mode,
    )
    if direction is None:
        return ContractViolation(
            "contract",
            "research_direction_mode",
            obj.get("research_direction_mode", _MISSING),
            "broad|locked and the persisted route contract",
        )
    raw_stages = obj.get("stages")
    existing_names = {
        str(value).strip().lower() for value in existing_data_domains
    }
    if name in existing_names and isinstance(raw_stages, list):
        tokens = [
            str(value or "").strip().casefold()
            for value in raw_stages
            if str(value or "").strip()
        ]
        normalized = tuple(
            dict.fromkeys(
                slug for value in raw_stages if (slug := _sluggify_name(value))
            )
        )
        if tokens not in ([], ["none"]) and not (
            _MIN_STAGES <= len(normalized) <= _MAX_STAGES
        ):
            return ContractViolation(
                "contract", "stages", raw_stages, "2..10 unique stage names or NONE"
            )
    return ContractViolation(
        "contract",
        "route_contract",
        sanitize_model_reply_snippet(raw_text),
        "a valid existing/new Manager decision",
    )


def parse_vertical_decision(
    raw_text: str | Mapping[str, Any],
    *,
    known_verticals: Sequence[str] = (),
    known_domains: Sequence[str] = (),
    existing_data_domains: Sequence[str] = (),
    research_target_verticals: Sequence[str] = (),
    default_execution_task: str = "",
    persisted_vertical: str = "",
    persisted_workflow_mode: str = "",
    persisted_domain: str = "",
    persisted_research_target_level: str = "",
    persisted_research_direction_mode: str = "",
    allow_persisted_change: bool = False,
) -> VerticalDecision | None:
    """Validate the Manager's vertical-decision JSON; fail-closed to ``None``.

    ``choice == "existing"`` requires ``vertical`` to name a known built-in or an
    existing data domain (normalized). ``choice == "new"`` reuses
    :func:`parse_domain_proposal`. Any ambiguity → ``None`` (the caller raises).
    """
    obj = _decision_fields(raw_text)
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
    if choice == "existing":
        identity = _resolve_existing_identity(
            obj,
            persisted_vertical=persisted_vertical,
            persisted_workflow_mode=persisted_workflow_mode,
            allow_persisted_change=allow_persisted_change,
        )
        if identity is None:
            return None
        name, workflow_mode, same_persisted_identity = identity
        domain = _resolve_existing_domain(
            obj,
            name=name,
            same_persisted_identity=same_persisted_identity,
            persisted_domain=persisted_domain,
            allow_persisted_change=allow_persisted_change,
        )
        if domain is None:
            return None
        known = {str(v).strip().lower() for v in known_verticals}
        known |= {str(v).strip().lower() for v in existing_data_domains}
        allowed_domains = {
            str(value or "").strip().lower() for value in known_domains
        }
        if name == "research" and domain and domain not in allowed_domains:
            return None
        targeted = {
            str(value or "").strip().lower()
            for value in research_target_verticals
        }
        target_level = _resolve_research_target(
            obj,
            name=name,
            targeted=targeted,
            same_persisted_identity=same_persisted_identity,
            persisted_research_target_level=persisted_research_target_level,
            allow_persisted_change=allow_persisted_change,
        )
        if target_level is None:
            return None
        direction_mode = _resolve_research_direction(
            obj,
            name=name,
            target_level=target_level,
            same_persisted_identity=same_persisted_identity,
            persisted_research_direction_mode=persisted_research_direction_mode,
        )
        if direction_mode is None:
            return None
        target_venue = " ".join(
            str(obj.get("target_venue") or "").strip().split()
        )[:100]
        if name != "research":
            target_venue = ""
        stated, exclusions, ambiguities = _stated_requirements(obj)
        if name and name in known:
            adapted_stages: tuple[str, ...] = ()
            raw_stages = obj.get("stages")
            existing_domains = {
                str(value).strip().lower()
                for value in existing_data_domains
            }
            if name in existing_domains and isinstance(raw_stages, list):
                raw_tokens = [
                    str(value or "").strip().casefold()
                    for value in raw_stages
                    if str(value or "").strip()
                ]
                if raw_tokens not in ([], ["none"]):
                    normalized = tuple(
                        dict.fromkeys(
                            slug
                            for value in raw_stages
                            if (slug := _sluggify_name(value))
                        )
                    )
                    if not (_MIN_STAGES <= len(normalized) <= _MAX_STAGES):
                        return None
                    adapted_stages = normalized
            return VerticalDecision(
                choice="existing",
                vertical=name,
                domain=domain,
                workflow_mode=workflow_mode,
                proposal=None,
                adapted_stages=adapted_stages,
                adaptation_reason=str(obj.get("rationale") or "").strip()[:600],
                live_view=parsed_live_view,
                live_view_decided=live_view_decided,
                execution_task=execution_task,
                research_target_level=target_level,
                research_direction_mode=direction_mode,
                target_venue=target_venue,
                require_independent_review=(
                    obj.get("require_independent_review", True) is not False
                ),
                precise_constraints=stated,
                exclusions=exclusions,
                ambiguities=ambiguities,
            )
        return None
    if choice == "new":
        workflow_mode = str(obj.get("workflow_mode") or "").strip().lower()
        if not workflow_mode:
            workflow_mode = "staged"
        if workflow_mode not in {"direct", "staged"}:
            return None
        proposal = parse_domain_proposal(
            raw_text,
            known_verticals=known_verticals,
            existing_data_domains=existing_data_domains,
        )
        if proposal is None:
            return None
        stated, exclusions, ambiguities = _stated_requirements(obj)
        return VerticalDecision(
            choice="new",
            vertical=proposal.name,
            domain="",
            workflow_mode=workflow_mode,
            proposal=proposal,
            live_view=parsed_live_view,
            live_view_decided=live_view_decided,
            execution_task=execution_task,
            require_independent_review=(
                obj.get("require_independent_review", True) is not False
            ),
            precise_constraints=stated,
            exclusions=exclusions,
            ambiguities=ambiguities,
        )
    return None
