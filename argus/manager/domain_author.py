"""The Manager's routing decision: the route form's check and what it decides.

``Manager.decide_vertical`` makes one bounded, repository-grounded routing
call (after an optional tool-free fast pass). The Manager hands its decision
over with ``hand_off`` on the route form (``core.handoff``); :class:`RouteContext`
checks that call against the menus and the persisted route contract and turns
it into a :class:`VerticalDecision`, or refuses it with one sentence the
Manager corrects in the same turn. A proposed new domain is persisted as
project-local DATA by :func:`argus.verticals._data_domain.write_data_domain`.
There is NO silent fallback to the research default.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from ..core.research_contract import normalize_research_direction_mode
from ..core.session import topic_name
from ..roles.prompts.manager import (
    build_research_target_prompt,
)
from ..skills.stage_machine import normalize_stage_for_project
from ..verticals._data_domain import CANDIDATE_DOMAIN_STAGES

_NAME_SANITIZE_RE = re.compile(r"[^a-z0-9_]+")


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


@dataclass
class DomainProposal:
    """A Manager-routed new domain with runtime-owned candidate stages."""

    name: str
    stages: list[str]
    rationale: str = ""
    execution_task: str = ""
    capability_brief: str = ""


def _sluggify_name(raw: object) -> str:
    s = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    s = _NAME_SANITIZE_RE.sub("_", s).strip("_")
    return s


def _canonical_existing_vertical(value: object) -> tuple[str, bool]:
    raw_name = _sluggify_name(value)
    legacy_direct = raw_name == "direct"
    return ("software" if legacy_direct else raw_name, legacy_direct)


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
    obj: Mapping[str, Any],
    *,
    known_verticals: Sequence[str] = (),
    existing_data_domains: Sequence[str] = (),
) -> DomainProposal | None:
    """A new domain named by ``obj["name"]`` (or ``obj["vertical"]``), or ``None`` without a usable slug.

    The slug must not collide with a preset vertical or existing data domain
    (a numeric suffix is appended on collision). Candidate stages are always
    runtime-owned.
    """
    name = _sluggify_name(obj.get("name") or obj.get("vertical"))
    taken = {str(v).strip().lower() for v in known_verticals}
    taken |= {str(v).strip().lower() for v in existing_data_domains}
    unique = _dedupe_name(name, taken) if name else None
    if unique is None:
        return None
    return DomainProposal(
        name=unique,
        stages=list(CANDIDATE_DOMAIN_STAGES),
        rationale=str(obj.get("rationale") or "").strip()[:600],
        execution_task=str(obj.get("execution_task") or "").strip(),
        capability_brief=str(obj.get("capability_brief") or "").strip()[:6000],
    )


@dataclass
class VerticalDecision:
    """The Manager's committable choice of vertical for a task.

    ``choice`` is ``"existing"`` (reuse a known built-in vertical or an existing
    project data domain) or ``"new"`` (author a fresh data domain). ``vertical``
    is the chosen/authored name in both cases; ``proposal`` carries the authored
    domain only when ``choice == "new"``.
    """

    choice: str
    vertical: str
    # Optional built-in domain overlay. Only the research workflow accepts one.
    domain: str = ""
    # Orthogonal execution topology chosen by Manager; never encoded as a vertical.
    workflow_mode: str = "staged"
    proposal: DomainProposal | None = None
    # Why this route, in the operator's words: the handoff's summary, shown beside the plan.
    adaptation_reason: str = ""
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
    # The operator asked to take part in stage decisions; otherwise the Manager makes them.
    operator_decides_stages: bool = False
    # Requirements the operator actually stated, split by how they can be
    # checked. `precise_constraints` are mechanically checkable things the
    # operator chose (a number, a baseline, a budget) and are recorded verbatim;
    # the Manager must never invent one, because a constraint nobody asked for
    # becomes a goal nobody agreed to.
    precise_constraints: tuple[str, ...] = ()
    # Readings of the operator's words the Manager chose without asking:
    # ``{"chose": ..., "instead_of": ...}`` each (``core.autonomy.record_assumptions``).
    assumptions: tuple[dict[str, str], ...] = ()
    # Initial stage for direct work; never resets an existing stage.
    start_stage: str = ""
    session_title: str = ""
    workflow_profile: str = ""
    workflow_requested_stages: tuple[str, ...] = ()
    # The plan file the Manager wrote for the ticket: ``{"path", "summary"}``, or empty.
    plan: dict[str, str] = field(default_factory=dict)


def _listed(payload: Mapping[str, Any], key: str) -> tuple[str, ...]:
    """The operator's own words, de-duplicated but otherwise verbatim: a paraphrase is already a revision."""
    return tuple(dict.fromkeys(" ".join(text.split()) for text in payload.get(key, ()) if text.strip()))


@dataclass(frozen=True)
class RouteContext:
    """What a routing call is checked against: the menus and the persisted route contract.

    ``decide`` is the ticket route's check. It refuses a decision the host
    cannot commit with one sentence the Manager can act on.
    """

    known_verticals: tuple[str, ...] = ()
    known_domains: tuple[str, ...] = ()
    existing_data_domains: tuple[str, ...] = ()
    research_target_verticals: tuple[str, ...] = ()
    default_execution_task: str = ""
    persisted_vertical: str = ""
    persisted_workflow_mode: str = ""
    persisted_domain: str = ""
    persisted_research_target_level: str = ""
    persisted_research_direction_mode: str = ""
    allow_persisted_change: bool = False
    project_root: object = None

    def _kept(self, field: str, value: str, prior: str, same: bool) -> None:
        """Refuse changing what the active route contract fixes."""
        if same and prior and value != prior and not self.allow_persisted_change:
            raise ValueError(f"{field} must stay {prior}: the active route contract fixes it for this vertical.")

    def decide(self, payload: dict[str, Any]) -> VerticalDecision:
        choice = payload["choice"]
        execution_task = str(payload.get("execution_task") or self.default_execution_task).strip()
        if not execution_task:
            raise ValueError(
                "execution_task: write the current message's work as a standalone handoff, "
                "without the conversation context markers."
            )
        decision = VerticalDecision(
            choice=choice, vertical="",
            adaptation_reason=payload["summary"],
            execution_task=execution_task,
            require_independent_review=payload.get("require_independent_review", True),
            operator_decides_stages=payload.get("operator_decides_stages", False),
            precise_constraints=_listed(payload, "precise_constraints"),
            assumptions=tuple(payload["assumptions"]),
            session_title=topic_name(payload.get("session_title")),
            plan=dict(payload.get("plan", {})),
        )
        if choice == "new":
            return self._new(payload, decision)
        return self._existing(payload, decision)

    def _new(self, payload: dict[str, Any], decision: VerticalDecision) -> VerticalDecision:
        if payload.get("workflow_profile") or payload.get("workflow_stages"):
            raise ValueError("A new domain has no workflow profiles: leave workflow_profile and workflow_stages empty.")
        proposal = parse_domain_proposal(
            {**payload, "rationale": payload["summary"]},
            known_verticals=self.known_verticals, existing_data_domains=self.existing_data_domains,
        )
        if proposal is None:
            raise ValueError("vertical: name the new domain with a reusable ASCII slug.")
        decision.vertical, decision.proposal = proposal.name, proposal
        decision.workflow_mode = payload.get("workflow_mode", "staged")
        start_stage = str(payload.get("start_stage") or "").strip().lower()
        if decision.workflow_mode == "direct" and start_stage in proposal.stages:
            decision.start_stage = start_stage
        return decision

    def _existing(self, payload: dict[str, Any], decision: VerticalDecision) -> VerticalDecision:
        name, legacy_direct = _canonical_existing_vertical(payload.get("vertical"))
        prior_name, prior_legacy_direct = _canonical_existing_vertical(self.persisted_vertical)
        same = bool(name and name == prior_name)
        known = {str(value).strip().lower() for value in (*self.known_verticals, *self.existing_data_domains)}
        if name not in known:
            raise ValueError("vertical must be one of: " + ", ".join(sorted(known)))
        prior_mode = str(self.persisted_workflow_mode or "").strip().lower() or (
            "direct" if prior_legacy_direct else ""
        )
        mode = payload.get("workflow_mode", "")
        if legacy_direct:
            if mode not in {"", "direct"}:
                raise ValueError("vertical=direct is software with workflow_mode=direct.")
            mode = "direct"
        mode = mode or (prior_mode if same else "") or "staged"
        self._kept("workflow_mode", mode, prior_mode, same)
        decision.vertical, decision.workflow_mode = name, mode
        decision.domain = self._domain(payload, name, same)
        decision.research_target_level = self._target(payload, name, same)
        decision.research_direction_mode = self._direction(payload, name, same, decision.research_target_level)
        if name == "research":
            decision.target_venue = " ".join(str(payload.get("target_venue") or "").split())
        decision.workflow_profile = str(payload.get("workflow_profile") or "").strip()
        stages = payload.get("workflow_stages", ())
        if not isinstance(stages, (list, tuple)) or not all(isinstance(stage, str) for stage in stages):
            raise ValueError("workflow_stages must be a list of stage names.")
        decision.workflow_requested_stages = tuple(stage.strip().lower() for stage in stages)
        if mode == "direct" and payload.get("start_stage"):
            decision.start_stage = normalize_stage_for_project(
                self.project_root, payload["start_stage"], vertical=name, require_known=True,
            )
        return decision

    def _domain(self, payload: dict[str, Any], name: str, same: bool) -> str:
        domain = _sluggify_name(payload.get("domain"))
        if name != "research":
            if domain:
                raise ValueError(
                    "domain names only an optional research domain with vertical=research; "
                    "put an existing project domain's slug in vertical."
                )
            return ""
        prior = _sluggify_name(self.persisted_domain)
        domain = domain or (prior if same else "")
        self._kept("domain", domain, prior, same)
        allowed = {str(value or "").strip().lower() for value in self.known_domains}
        if domain and domain not in allowed:
            raise ValueError("domain must be empty or one of: " + ", ".join(sorted(allowed)))
        return domain

    def _target(self, payload: dict[str, Any], name: str, same: bool) -> str:
        if name not in {str(value or "").strip().lower() for value in self.research_target_verticals}:
            return ""
        prior = str(self.persisted_research_target_level or "").strip().lower()
        level = payload.get("research_target_level") or (prior if same else "")
        if not level:
            raise ValueError(f"research_target_level is required for vertical={name}.")
        self._kept("research_target_level", level, prior, same)
        return level

    def _direction(self, payload: dict[str, Any], name: str, same: bool, level: str) -> str:
        if name != "research":
            return ""
        prior = normalize_research_direction_mode(self.persisted_research_direction_mode) or ""
        direction = payload.get("research_direction_mode", "")
        if direction:
            self._kept("research_direction_mode", direction, prior, same)
        return direction or (prior if same else "") or (
            "broad" if level in {"publishable", "doctoral"} else "locked"
        )


__all__ = [
    "DomainProposal",
    "RouteContext",
    "VerticalDecision",
    "VerticalDecisionError",
    "build_research_target_prompt",
    "parse_domain_proposal",
    "sanitize_model_reply_snippet",
]
