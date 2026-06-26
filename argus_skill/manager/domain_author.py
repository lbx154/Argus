"""Manager domain authoring: prompt + strict parser for a NEW data domain.

When the Manager triages a Task that matches NO preset vertical, it AUTHORS a new
domain — a short name + an ordered Stage list — instead of falling back to the
research paper pipeline. This module holds the prompt it sends and the strict,
fail-closed parser for the JSON proposal, mirroring
:mod:`argus_skill.manager.stage_decider` (which keeps ``manager/_core`` thin).

The proposed domain is persisted as project-local DATA by
:func:`argus_skill.verticals._data_domain.write_data_domain`; the per-stage
checklist is authored later by the Planner. Fail-closed: any ambiguity (bad JSON,
no usable stages, an un-sluggable name) parses to ``None`` so the Manager falls
back to the existing safe default (``"research"``).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Sequence

_NAME_SANITIZE_RE = re.compile(r"[^a-z0-9_]+")
_MIN_STAGES = 2
_MAX_STAGES = 10


@dataclass
class DomainProposal:
    """A Manager-authored new domain (validated + sluggified)."""

    name: str
    stages: list[str]
    rationale: str = ""
    confidence: float = 0.0
    raw_name: str = field(default="", repr=False)


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
        "domain for it: a short domain name and an ordered list of Stages the "
        "pipeline will advance through (research → ... → final deliverable).\n\n"
        f"Preset verticals (do NOT reuse these names): {known}\n"
        f"Existing project domains (do NOT reuse these names): {existing}\n\n"
        "## Task\n"
        f"{(task or '').strip()}\n\n"
        "## Rules\n"
        f"- Propose {_MIN_STAGES}-{_MAX_STAGES} Stages, ordered from first to "
        "last. Each Stage is a short lowercase slug (a phase of work, e.g. "
        "`scope`, `simulate`, `measure`, `report`) — NOT a checklist item. The "
        "per-stage checklist is authored later by the Planner; you only define "
        "the stage SKELETON.\n"
        "- The domain `name` is a short lowercase slug (letters/digits/"
        "underscore), distinct from every name above.\n"
        "- Prefer a small, coherent stage set a domain expert would recognize; "
        "do not pad with ceremony stages.\n\n"
        "Reply with ONE JSON object and NOTHING else:\n"
        '{"name": "<slug>", "stages": ["<stage1>", "<stage2>", ...], '
        '"rationale": "<one sentence>", "confidence": <0.0-1.0>}\n'
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
    ``_MAX_STAGES`` short slugs (deduped, order preserved); ``name`` sluggifies to
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

    name = _sluggify_name(obj.get("name"))
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
        raw_name=str(obj.get("name") or "").strip(),
    )


__all__ = [
    "DomainProposal",
    "build_domain_author_prompt",
    "parse_domain_proposal",
]
