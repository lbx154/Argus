"""Manager vertical decision + domain authoring: prompts and strict parsers.

The Manager makes ONE grounded agent call (``Manager.decide_vertical``, see
``manager/_core.py``) to choose the vertical for a Task: an existing built-in
vertical, an existing project data domain, or a freshly AUTHORED domain — a
slug name + an ordered Stage list. This is a GROUNDED call (real shell/read
access, pinned to ``project_root``): the prompt tells the model to actually
inspect the repo before deciding, rather than guessing from the task sentence
alone. This module holds the prompts it sends and the strict, fail-closed JSON
parsers, mirroring :mod:`argus_skill.manager.stage_decider` (which keeps
``manager/_core`` thin).

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

    return DomainProposal(
        name=unique,
        stages=stages,
        rationale=rationale,
        confidence=confidence,
    )


__all__ = [
    "DomainProposal",
    "VerticalDecision",
    "VerticalDecisionError",
    "build_domain_author_prompt",
    "build_vertical_decision_prompt",
    "parse_domain_proposal",
    "parse_vertical_decision",
]


@dataclass
class VerticalDecision:
    """The Manager's grounded choice of vertical for a task.

    ``choice`` is ``"existing"`` (reuse a known built-in vertical or an existing
    project data domain) or ``"new"`` (author a fresh data domain). ``vertical``
    is the chosen/authored name in both cases; ``proposal`` carries the authored
    domain (stages + slug) only when ``choice == "new"``.
    """

    choice: str
    vertical: str
    proposal: DomainProposal | None = None


def build_vertical_decision_prompt(
    task: str,
    *,
    verticals_with_purpose: dict[str, str],
    existing_data_domains: Sequence[str] = (),
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
    return (
        "You are the MANAGER of an automated research/engineering pipeline. "
        "Decide which single VERTICAL should run the Task below. A vertical is a "
        "named pipeline with its own ordered Stages and, for built-ins, expert "
        "per-stage reviewer checklists.\n\n"
        "You have shell access in this repository. Before deciding, INVESTIGATE "
        "— do not guess from the task sentence alone. Read `AGENTS.md`/`README` "
        "if present and look at the project's structure, language, and tooling "
        "so your choice fits what this specific repo actually needs. This is a "
        "READ-ONLY investigation: do NOT edit, create, or delete any file.\n\n"
        "## Built-in verticals (PREFER one of these when it fits the Task)\n"
        f"{menu}\n\n"
        f"## Existing project data domains (also selectable): {existing}\n\n"
        "## How to choose (in this order)\n"
        "1. If a BUILT-IN vertical above fits the Task, choose it — built-ins "
        "carry expert reviewer checklists a fresh domain would lack. E.g. a "
        "GPU/CUDA/SOL-ExecBench kernel objective is `kernelbench`; a finance "
        "factor-research report is `quant`; a paper is `research`.\n"
        "2. Else if an existing project data domain fits, choose it.\n"
        "3. ONLY if nothing above fits, AUTHOR a new data domain: a slug name "
        "plus an ordered list of Stages (a phase of work each, lowercase slug, "
        f"{_MIN_STAGES}-{_MAX_STAGES} stages) grounded in what the repo needs to "
        "reach a verifiable deliverable. The per-stage checklist is authored "
        "later by the Planner; you define only the stage SKELETON.\n\n"
        "## Task\n"
        f"{(task or '').strip()}\n\n"
        "When your investigation is done, reply with ONE JSON object and "
        "NOTHING else (no prose before or after it), in ONE of these two shapes. "
        "In BOTH shapes the chosen name goes in the field named `vertical`:\n"
        '{"choice": "existing", "vertical": "<one of the names above>", '
        '"rationale": "<why it fits, citing what you found in the repo>"}\n'
        "OR\n"
        '{"choice": "new", "vertical": "<a new lowercase a-z0-9_ slug, distinct '
        'from every name above>", "stages": ["<stage1>", ...], '
        '"rationale": "<why no existing vertical fits + what you found>", '
        '"confidence": <0.0-1.0>}\n'
        "(If your new slug collides with an existing name it is auto-suffixed.)\n"
    )


def parse_vertical_decision(
    raw_text: str,
    *,
    known_verticals: Sequence[str] = (),
    existing_data_domains: Sequence[str] = (),
) -> VerticalDecision | None:
    """Validate the Manager's vertical-decision JSON; fail-closed to ``None``.

    ``choice == "existing"`` requires ``vertical`` to name a known built-in or an
    existing data domain (normalized). ``choice == "new"`` reuses
    :func:`parse_domain_proposal`. Any ambiguity → ``None`` (the caller raises).
    """
    obj = _loads_first_json(raw_text)
    if not isinstance(obj, dict):
        return None
    choice = str(obj.get("choice") or "").strip().lower()
    if choice == "existing":
        name = _sluggify_name(obj.get("vertical") or obj.get("name"))
        known = {str(v).strip().lower() for v in known_verticals}
        known |= {str(v).strip().lower() for v in existing_data_domains}
        if name and name in known:
            return VerticalDecision(choice="existing", vertical=name, proposal=None)
        return None
    if choice == "new":
        proposal = parse_domain_proposal(
            raw_text,
            known_verticals=known_verticals,
            existing_data_domains=existing_data_domains,
        )
        if proposal is None:
            return None
        return VerticalDecision(choice="new", vertical=proposal.name, proposal=proposal)
    return None
