"""Manager skill-library tidy helpers."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from ..roles.prompts.manager import (
    build_skill_placement_prompt,
    build_skill_placements_prompt,
)

log = logging.getLogger(__name__)


_PLACEMENT_KEYS = ("CANDIDATE_ID", "PLACEMENT", "VERTICAL", "WHY")


def _named_placement(text: str) -> dict | None:
    """One placement verdict from named lines, or ``None`` when absent."""
    from ..core.role_reply import read_key_values, read_optional

    values = read_key_values(text, _PLACEMENT_KEYS)
    if "PLACEMENT" not in values:
        return None
    return {
        "placement": read_optional(values, "PLACEMENT"),
        "vertical": read_optional(values, "VERTICAL"),
        "why": read_optional(values, "WHY"),
    }


def _named_placements(text: str) -> dict | None:
    """Several placement verdicts, one repeated block each.

    Returns the same ``{"placements": [...]}`` shape the JSON reader produced,
    so every downstream check — one row per input, candidate id must match, an
    unknown vertical falls back to `stay` — runs unchanged.
    """
    from ..core.role_reply import read_records

    records = read_records(text, _PLACEMENT_KEYS, start_key="CANDIDATE_ID")
    if not records:
        return None
    return {
        "placements": [
            {
                "candidate_id": row.get("CANDIDATE_ID", ""),
                "placement": row.get("PLACEMENT", ""),
                "vertical": row.get("VERTICAL", ""),
                "why": row.get("WHY", ""),
            }
            for row in records
        ]
    }


def _extract_json(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Strip a ```json ... ``` fence if present.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1)
    else:
        brace = re.search(r"\{.*\}", raw, re.DOTALL)
        if brace:
            raw = brace.group(0)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


@dataclass
class PlacementVerdict:
    """Where a project-distilled skill should be tidied to.

    ``placement`` is ``"global"`` (cross-domain → global layer), ``"vertical"``
    (domain-specific → that vertical's layer; ``vertical`` names it), or
    ``"stay"`` (leave it in the project layer — too specific, or unsure).
    """

    placement: str
    vertical: str
    why: str


def classify_skill_placement(
    *,
    content: str,
    task: str,
    candidate_verticals: list[str],
    runner: Any,
    model: str = "",
    reasoning_effort: str = "low",
) -> PlacementVerdict:
    """Decide whether a project-distilled skill should be tidied up to the GLOBAL
    layer, to a specific VERTICAL layer, or STAY in the project layer.

    One focused LLM judge, used by the Manager's end-of-mission "tidy-up". This
    only ROUTES an already-stored project skill. Fail-soft and CONSERVATIVE: any error,
    empty/unparseable output, missing runner, or a vertical not in the candidate
    list → ``stay`` (never mis-file)."""
    if not (content or "").strip():
        return PlacementVerdict("stay", "", "empty content")
    if runner is None:
        return PlacementVerdict("stay", "", "no manager runner available")
    candidates = [v for v in (candidate_verticals or []) if isinstance(v, str) and v]

    prompt = build_skill_placement_prompt(
        content=content,
        task=task,
        candidate_verticals=candidates,
    )
    try:
        result = gateway_run_exec(
            runner,
            prompt=prompt,
            options=RunnerOptions(
                model=model or None,
                reasoning_effort=reasoning_effort,
                skip_git_repo_check=True,
                full_auto=True,
            ),
            run_label="manager.skill_placement",
        )
    except Exception as exc:  # noqa: BLE001 — tidy-up must never break the loop
        log.warning("manager skill placement failed (%s: %s)", type(exc).__name__, exc)
        return PlacementVerdict("stay", "", f"placement error: {type(exc).__name__}")

    reply = getattr(result, "last_agent_message", "") or ""
    parsed = _named_placement(reply) or _extract_json(reply)
    if parsed is None:
        return PlacementVerdict("stay", "", "placement returned no JSON verdict")
    placement = str(parsed.get("placement", "")).strip().lower()
    vertical = str(parsed.get("vertical", "")).strip()
    why = str(parsed.get("why", "")).strip()[:500]
    if placement == "global":
        return PlacementVerdict("global", "", why or "general capability")
    if placement == "vertical" and vertical in candidates:
        return PlacementVerdict("vertical", vertical, why or f"belongs to {vertical}")
    # Unknown placement, or a vertical not in the candidate list → conservative
    # stay (never mis-file into a vertical the caller did not offer).
    return PlacementVerdict("stay", "", why or "unplaceable / unknown vertical")


def classify_skill_placements(
    *,
    skills: list[dict[str, str]],
    candidate_verticals: list[str],
    runner: Any,
    model: str = "",
    reasoning_effort: str = "low",
) -> dict[str, PlacementVerdict]:
    """Classify a batch of runtime skills with one metered Manager call."""
    rows = [
        {
            "candidate_id": str(
                item.get("candidate_id") or item.get("name") or ""
            ).strip(),
            "name": str(item.get("name") or "").strip(),
            "task": str(item.get("task") or "").strip()[:2000],
            "content": str(item.get("content") or "").strip()[:12000],
        }
        for item in skills
        if str(item.get("name") or "").strip()
    ]
    defaults = {
        row["candidate_id"]: PlacementVerdict(
            "stay",
            "",
            "batch placement unavailable",
        )
        for row in rows
    }
    if not rows or runner is None:
        return defaults
    candidates = [v for v in candidate_verticals if isinstance(v, str) and v]
    prompt = build_skill_placements_prompt(
        skills=rows,
        candidate_verticals=candidates,
    )
    try:
        result = gateway_run_exec(
            runner,
            prompt=prompt,
            options=RunnerOptions(
                model=model or None,
                reasoning_effort=reasoning_effort,
                skip_git_repo_check=True,
                full_auto=True,
            ),
            run_label="manager.skill_placement_batch",
        )
    except Exception as exc:  # noqa: BLE001 - promotion remains fail-soft
        log.warning("manager batch skill placement failed (%s: %s)", type(exc).__name__, exc)
        return defaults

    reply = getattr(result, "last_agent_message", "") or ""
    parsed = _named_placements(reply) or _extract_json(reply) or {}
    placements = parsed.get("placements")
    if not isinstance(placements, list):
        return defaults
    for item in placements:
        if not isinstance(item, dict):
            continue
        candidate_id = str(
            item.get("candidate_id") or item.get("name") or ""
        ).strip()
        if candidate_id not in defaults:
            continue
        placement = str(item.get("placement") or "").strip().lower()
        vertical = str(item.get("vertical") or "").strip()
        why = str(item.get("why") or "").strip()[:500]
        if placement == "global":
            defaults[candidate_id] = PlacementVerdict(
                "global",
                "",
                why or "general capability",
            )
        elif placement == "vertical" and vertical in candidates:
            defaults[candidate_id] = PlacementVerdict(
                "vertical", vertical, why or f"belongs to {vertical}"
            )
        else:
            defaults[candidate_id] = PlacementVerdict(
                "stay",
                "",
                why or "project-specific",
            )
    return defaults


__all__ = [
    "classify_skill_placement",
    "classify_skill_placements",
    "PlacementVerdict",
]
