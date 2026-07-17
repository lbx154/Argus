"""Manager vertical decision + domain authoring: prompts and strict parsers.

``Manager.decide_vertical`` first makes one compact, tool-free routing request.
A clear existing vertical commits immediately; uncertainty or a potentially new
domain escalates once to a bounded, read-only repository investigation. This
module holds both prompts and their fail-closed parsers, mirroring
:mod:`argus_skill.manager.stage_decider` (which keeps ``manager/_core`` thin).

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


def build_domain_author_prompt(
    task: str,
    *,
    known_verticals: Sequence[str],
    existing_data_domains: Sequence[str] = (),
) -> str:
    """Render the prompt asking the Manager to author a new domain for ``task``."""
    known = ", ".join(f"`{v}`" for v in known_verticals) or "(none)"
    existing = ", ".join(f"`{v}`" for v in existing_data_domains) or "(none)"
    return (
        "You are the MANAGER of an automated research/engineering pipeline. The "
        "Task below does NOT fit any preset vertical, so you must DEFINE a new "
        "domain for it: a domain slug and an ordered list of Stages the "
        "pipeline will advance through (research → ... → final deliverable).\n\n"
        "You have shell access in this repository. Before proposing anything, "
        "INVESTIGATE — do not guess a generic stage template from the task "
        "sentence alone. Read `AGENTS.md`/`README` if present, look at the "
        "project's actual structure, language, and existing tooling (tests, "
        "build, profiling, benchmarks — whatever is relevant to this task), and "
        "ground the stage skeleton in what this specific repo actually needs to "
        "go from the current state to a verifiable deliverable. This is a "
        "READ-ONLY investigation: do NOT edit, create, or delete any file — "
        "you are only gathering context to inform your classification.\n\n"
        f"Preset verticals (do NOT reuse these names): {known}\n"
        f"Existing project domains (do NOT reuse these names): {existing}\n\n"
        "## Task\n"
        f"{(task or '').strip()}\n\n"
        "## Rules\n"
        f"- Propose {_MIN_STAGES}-{_MAX_STAGES} Stages, ordered from first to "
        "last. Each Stage is a lowercase slug naming a PHASE OF WORK you move "
        "through (e.g. `scope`, `simulate`, `measure`, `report`) — NOT a "
        "checklist item, and NOT a metric, target number, outcome, or benchmark "
        "name (a stage is something you DO, not a score you hit or an artifact "
        "you emit). The per-stage checklist is authored later by the Planner; "
        "you only define the stage SKELETON.\n"
        "- The domain `name` is a lowercase slug (letters/digits/"
        "underscore), distinct from every name above (if it collides it is "
        "auto-suffixed).\n"
        "- Prefer a small, coherent stage set a domain expert would recognize, "
        "grounded in what you actually found in the repo — do not pad with "
        "ceremony stages.\n\n"
        "When your investigation is done, reply with ONE JSON object and "
        "NOTHING else (no prose before or after it) — ONLY these four fields:\n"
        '{"name": "<slug>", "stages": ["<stage1>", "<stage2>", ...], '
        '"rationale": "<clear explanation citing what you found in the repo>", '
        '"confidence": <0.0-1.0>}\n'
    )


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


def build_fast_vertical_decision_prompt(
    task: str,
    *,
    verticals_with_purpose: dict[str, str],
    existing_data_domains: Sequence[str] = (),
    research_target_verticals: Sequence[str] = (),
) -> str:
    """Render the compact, tool-free first-pass vertical router prompt."""
    menu = "\n".join(
        f"  - `{name}`: {purpose}" for name, purpose in verticals_with_purpose.items()
    ) or "  (none)"
    existing = ", ".join(f"`{v}`" for v in existing_data_domains) or "(none)"
    target_verticals = (
        ", ".join(f"`{name}`" for name in research_target_verticals)
        or "(none)"
    )
    return (
        "You are the MANAGER performing your fast, tool-free classification pass. "
        "Choose an existing vertical only when the operator's task text makes the "
        "fit clear. You have NO tools in this call: do not inspect files, infer "
        "repository facts that were not stated, expand the task, choose Live View "
        "artifacts, or design a new domain. If more repository context is needed "
        "or a new domain may be appropriate, request `grounded` instead.\n\n"
        "## Existing built-in verticals\n"
        f"{menu}\n\n"
        f"## Existing project data domains: {existing}\n\n"
        "## Classification rules\n"
        "- `vertical` is the capability/domain (software, research, math, etc.). "
        "Never use an execution topology such as direct/full/staged as a vertical.\n"
        "- Independently choose `workflow_mode=direct` when one Engineer mission "
        "can finish the bounded request. Choose `workflow_mode=staged` when the "
        "Manager should invoke planning and stage progression.\n"
        "- Never invent a task-specific alias for an existing capability.\n"
        "- If the task is ambiguous, depends on unstated repository structure, "
        "or appears to require a new domain, choose `grounded`.\n\n"
        "The following existing verticals require a research target level: "
        f"{target_verticals}. For one of those, use `exploratory`, `publishable`, "
        "or `doctoral` according to the operator's requested success bar. For "
        "all other verticals use null. If and only if the operator explicitly "
        "names a publication venue for a `research` task, copy it into "
        "`target_venue`; otherwise use null. Never infer a venue from topic.\n\n"
        "## Task\n"
        f"{(task or '').strip()}\n\n"
        "Reply with exactly one compact JSON object and nothing else:\n"
        '{"choice":"existing","vertical":"<existing name>",'
        '"workflow_mode":"direct|staged",'
        '"confidence":<0.0-1.0>,"research_target_level":'
        '"<exploratory|publishable|doctoral>"|null,'
        '"target_venue":"<explicit venue>"|null,"rationale":"<brief>"}\n'
        "OR\n"
        '{"choice":"grounded","confidence":<0.0-1.0>,'
        '"rationale":"<what additional context is needed>"}\n'
    )


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


def build_vertical_decision_prompt(
    task: str,
    *,
    verticals_with_purpose: dict[str, str],
    existing_data_domains: Sequence[str] = (),
    research_target_verticals: Sequence[str] = (),
) -> str:
    """Render the prompt asking the Manager to CHOOSE a vertical for ``task``.

    The Manager picks an existing built-in vertical when one fits, else an
    existing project data domain, else authors a NEW data domain. Built-ins are
    listed with a one-line purpose so the model can prefer them (they ship
    expert per-stage reviewer checklists; a freshly-authored domain starts with
    none). This is a GROUNDED, read-only call: investigate the repo first.
    """
    menu = "\n".join(
        f"  - `{name}`: {purpose}" for name, purpose in verticals_with_purpose.items()
    ) or "  (none)"
    existing = ", ".join(f"`{v}`" for v in existing_data_domains) or "(none)"
    target_verticals = (
        ", ".join(f"`{name}`" for name in research_target_verticals)
        or "(none)"
    )
    return (
        "You are the MANAGER of an automated research/engineering pipeline. "
        "Decide which capability VERTICAL and execution WORKFLOW should run the "
        "Task below. A vertical is a "
        "stable, reusable capability contract with its own ordered Stages and, "
        "for built-ins, expert per-stage reviewer checklists. It is NOT the "
        "task-specific route or DAG of literature, experiment, proof, and review "
        "work that the Planner may create inside one mission.\n\n"
        "Your tool-free classification pass requested grounded context. INVESTIGATE with "
        "read-only shell access in this repository. Use ONE focused inspection batch of at "
        "most four file/search operations, then decide. Avoid broad recursive "
        "searches and do not read unrelated UI, generated, vendor, or build-output "
        "trees. Read `AGENTS.md`/`README` only when they are directly useful, and "
        "look only at the minimum project structure, language, or tooling needed "
        "to resolve the routing uncertainty. "
        "Treat project/task artifacts as READ-ONLY: do NOT edit, create, or delete "
        "files with tools. This call decides routing/domain structure only; do not "
        "choose Live View artifacts or expand the Engineer task.\n\n"
        "## Built-in verticals (PREFER one of these when it fits the Task)\n"
        f"{menu}\n\n"
        f"## Existing project data domains (also selectable): {existing}\n\n"
        "## How to choose (in this order)\n"
        "1. If a BUILT-IN vertical above fits the Task, choose it — built-ins "
        "carry expert reviewer checklists a fresh domain would lack. E.g. a "
        "GPU/CUDA/SOL-ExecBench kernel objective is `kernelbench`; a finance "
        "factor-research report is `quant`; a paper is `research`. Mathematical "
        "conjectures, proofs, and open mathematical research problems are `math`. "
        "Within `math`, literature retrieval, computational experiments, proof "
        "construction, Lean work, and independent review remain dynamic Planner "
        "backlog/DAG tasks; they are not competing verticals. Never author a "
        "task-specific alias such as `math_conjecture` for work already covered "
        "by `math`.\n"
        "2. Else if an existing project data domain fits, choose it.\n"
        "3. ONLY if nothing above provides the stable capability the Task needs, "
        "AUTHOR a new data domain. Do not author one merely to encode this "
        "mission's route, deliverable subtype, or task DAG. A new domain is a slug "
        "name plus an ordered list of Stages (a phase of work each, lowercase slug, "
        f"{_MIN_STAGES}-{_MAX_STAGES} stages) grounded in what the repo needs to "
        "reach a verifiable deliverable. The per-stage checklist is authored "
        "later by the Planner; you define only the stage SKELETON.\n\n"
        "Independently choose `workflow_mode`: `direct` when one Engineer mission "
        "can finish the bounded task; `staged` when planning/stage progression is "
        "needed. This topology is never a vertical.\n\n"
        "## Task\n"
        f"{(task or '').strip()}\n\n"
        "The following built-ins declare a project-level research target contract: "
        f"{target_verticals}. If you choose one of them, set "
        "`research_target_level` from "
        "the operator's requested success bar (not from how hard you think the "
        "problem is): `exploratory` when a bounded investigation, known proof, "
        "finite computation, local Lean check, or honest negative report can "
        "satisfy the request; `publishable` when success requires a verified "
        "original result of publication significance; `doctoral` when success "
        "explicitly requires doctoral/thesis-level original research. For every "
        "vertical outside that declared set, set it to null. For a `research` "
        "vertical, copy an explicitly operator-named publication venue into "
        "`target_venue`; otherwise use null. Do not infer one from the topic.\n\n"
        "When your investigation is done, reply with ONE JSON object and "
        "NOTHING else (no prose before or after it), in ONE of these two shapes. "
        "In BOTH shapes the chosen name goes in the field named `vertical`:\n"
        '{"choice": "existing", "vertical": "<one of the names above>", '
        '"workflow_mode": "<direct|staged>", '
        '"rationale": "<why it fits, citing what you found in the repo>", '
        '"research_target_level": "<exploratory|publishable|doctoral when the '
        'vertical declares a target contract, otherwise null>", '
        '"target_venue": "<explicit venue for research>"|null}\n'
        "OR\n"
        '{"choice": "new", "vertical": "<a new lowercase a-z0-9_ slug, distinct '
        'from every name above>", "stages": ["<stage1>", ...], '
        '"workflow_mode": "<direct|staged>", '
        '"rationale": "<why no existing vertical fits + what you found>", '
        '"research_target_level": null, '
        '"confidence": <0.0-1.0>}\n'
        "(If your new slug collides with an existing name it is auto-suffixed.)\n"
    )


def build_research_target_prompt(
    task: str,
    *,
    supported_levels: Sequence[str] = (
        "exploratory",
        "publishable",
        "doctoral",
    ),
) -> str:
    """Ask the Manager for a success bar when research routing is fixed."""
    return (
        "You are the MANAGER of a targeted research pipeline. The operator has "
        "already fixed the vertical; do not revisit routing. Decide only the "
        "requested research success bar from the task below. Judge what outcome "
        "the operator requires, not the problem's apparent difficulty.\n\n"
        "- exploratory: a bounded investigation, known result, finite computation, "
        "domain-specific local verification, or honest negative report may satisfy "
        "the task.\n"
        "- publishable: success requires a correctness-verified, novelty-verified "
        "original result with publishable significance.\n"
        "- doctoral: success explicitly requires doctoral/thesis-level original "
        "research. Reports, literature review, finite checks, and local validation "
        "alone are not success.\n\n"
        "Task:\n"
        f"{(task or '').strip()}\n\n"
        "Allowed levels for this vertical: "
        f"{', '.join(supported_levels)}.\n\n"
        "Reply with one JSON object and nothing else:\n"
        '{"research_target_level":"one allowed level",'
        '"rationale":"brief reason tied to the requested success bar"}'
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
