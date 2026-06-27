"""The Manager's skill-library approval gate (generality + correctness).

The Reviewer PROPOSES skill changes (``create`` / ``update``); SkillRouter runs
the cheap automatic checks (independence by similarity, mechanical structure)
and then calls THIS gate for the judgement that needs the most context. The
Manager is the top-level authority in the new architecture — it sees the most
and owns the two dimensions a stored skill must pass:

  * generality — a capability for a FAMILY of tasks, not the one in front of the
    reviewer (salvaged from the retired distiller's Generality/Coverage checks);
  * correctness — the playbook's logic is sound and would not mislead.

This replaces the author grading its own homework with an INDEPENDENT approver.
``delete`` / ``archive`` do NOT pass through here — retiring a wrong/harmful
skill is applied directly by SkillRouter on the reviewer's request.

Runs as one focused LLM judge call on the supplied runner; SkillRouter invokes
it only for a create/update proposal that already cleared the cheap checks, so
its cost is proportional to surviving proposals (rare).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from ..core.models import RunnerOptions

log = logging.getLogger(__name__)


@dataclass
class ApprovalVerdict:
    approved: bool
    why: str


# The judge rubric. The Manager is the top-level authority (it sees the most
# context), so it owns BOTH dimensions a stored skill must pass: generality
# (salvaged from the retired distiller's Generality/Coverage checks) and logical
# correctness. Independence (similarity dedup) and mechanical structure are
# checked by SkillRouter BEFORE this call, so the judge focuses on judgement.
_RUBRIC = (
    "GENERALITY — the playbook must be BROADER than the one task yet still "
    "ENCLOSE it:\n"
    "- title names a CAPABILITY (not a single task); steps use placeholders "
    "(<path>, <N>, <name>) for anything that varies; NO hardcoded paths, ids, "
    "numbers, or names from this mission survive.\n"
    "- `When to use` names a FAMILY that contains this task (not just a sibling); "
    "`How to solve` would produce the needed artefact/answer with at most "
    "placeholder substitution; `When NOT to use` does not exclude this task.\n"
    "CORRECTNESS — the playbook's logic must be SOUND:\n"
    "- the steps are technically correct and in a workable order; no step "
    "contradicts another; `When NOT to use` does not contradict `When to use`; "
    "the advice would not mislead an engineer into a wrong or harmful action.\n"
    "REJECT if it only fits THIS task, hardcodes mission specifics, captures a "
    "one-off / environmental blocker rather than a reusable skill, OR contains "
    "incorrect / contradictory / misleading steps."
)


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


def approve_skill(
    *,
    content: str,
    task: str,
    op: str = "create",
    runner: Any,
    model: str = "",
    reasoning_effort: str = "low",
    role_skill_block: str = "",
) -> ApprovalVerdict:
    """Judge a proposed skill playbook's generalizability. Returns
    ``ApprovalVerdict``. Fail-soft and CONSERVATIVE: any error, empty/unparseable
    judge output, or missing runner → ``approved=False`` (a skill is kept out of
    the library unless the gate affirmatively passes it).

    ``role_skill_block`` is OPTIONALLY prepended to the judge prompt — the Manager
    passes its role skill block here so the approval gate shares the same injected
    identity/duties context the Manager's other LLM calls use. It defaults to
    ``""`` so every existing caller (and a Manager with no ``skill_store``) gets a
    byte-for-byte identical prompt to before this parameter existed."""
    if not (content or "").strip():
        return ApprovalVerdict(False, "empty proposal")
    if runner is None:
        return ApprovalVerdict(False, "no manager runner available")

    prompt = (
        f"{role_skill_block}"
        "You are the Manager's skill-library gate — the top-level authority on "
        "what enters the shared skill library. A reviewer PROPOSED a capability "
        f"playbook (op={op}) after working a task. Judge whether it is BOTH "
        "generalizable AND logically correct enough to keep in a library reused "
        "across many future tasks.\n\n"
        f"## Rubric\n{_RUBRIC}\n\n"
        f"## The task the reviewer just worked\n{task.strip()[:2000]}\n\n"
        f"## Proposed playbook\n{content.strip()[:12000]}\n\n"
        "Reply with ONLY a JSON object: "
        '{\"approve\": true|false, \"why\": \"<one short clause>\"}. '
        "Approve only when BOTH generality and correctness are genuinely "
        "satisfied; when in doubt, reject — a wrong skill is worse than no skill."
    )
    try:
        result = runner.run_exec(
            prompt=prompt,
            options=RunnerOptions(
                model=model or None,
                reasoning_effort=reasoning_effort,
                skip_git_repo_check=True,
                full_auto=True,
            ),
            run_label="manager.skill_review",
        )
    except Exception as exc:  # noqa: BLE001 — gate must never break the loop
        log.warning("manager skill gate failed (%s: %s)", type(exc).__name__, exc)
        return ApprovalVerdict(False, f"gate error: {type(exc).__name__}")

    parsed = _extract_json(getattr(result, "last_agent_message", "") or "")
    if parsed is None:
        return ApprovalVerdict(False, "gate returned no JSON verdict")
    approved = bool(parsed.get("approve"))
    why = str(parsed.get("why", "")).strip()[:500]
    return ApprovalVerdict(approved, why or ("approved" if approved else "rejected"))


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


_PLACEMENT_RUBRIC = (
    "A project-distilled skill has proven useful in ONE project. Decide where it "
    "belongs in the shared library:\n"
    "- GLOBAL — a CROSS-DOMAIN capability useful to many kinds of missions "
    "(research, optimization, finance, …); it does not assume any one domain's "
    "tools, data, or vocabulary.\n"
    "- VERTICAL — a capability specific to ONE domain/vertical: its mechanism, "
    "tools, metrics, or vocabulary only make sense within that vertical (e.g. "
    "factor / backtest / alpha discipline → quant). Name that vertical from the "
    "candidate list.\n"
    "- STAY — keep it in the project layer: it is too project-specific to "
    "generalize, OR you cannot confidently place it. Prefer STAY when unsure — "
    "mis-filing a skill is worse than leaving it where it is."
)


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

    One focused LLM judge, used by the Manager's end-of-mission "tidy-up". Unlike
    :func:`approve_skill` (a create/update admission gate), this only ROUTES an
    already-stored project skill. Fail-soft and CONSERVATIVE: any error,
    empty/unparseable output, missing runner, or a vertical not in the candidate
    list → ``stay`` (never mis-file)."""
    if not (content or "").strip():
        return PlacementVerdict("stay", "", "empty content")
    if runner is None:
        return PlacementVerdict("stay", "", "no manager runner available")
    candidates = [v for v in (candidate_verticals or []) if isinstance(v, str) and v]

    prompt = (
        "You are the Manager tidying the skill library after a project finished. "
        "A reviewer distilled the playbook below while working this project. "
        "Decide where it belongs.\n\n"
        f"## Rubric\n{_PLACEMENT_RUBRIC}\n\n"
        f"## Candidate verticals\n{', '.join(candidates) or '(none)'}\n\n"
        f"## The task the skill was distilled on\n{task.strip()[:2000]}\n\n"
        f"## The skill playbook\n{content.strip()[:12000]}\n\n"
        "Reply with ONLY a JSON object: "
        '{"placement": "global"|"vertical"|"stay", '
        '"vertical": "<name or empty>", "why": "<one short clause>"}. '
        'Use "vertical" only with a name from the candidate list; when unsure, '
        'use "stay".'
    )
    try:
        result = runner.run_exec(
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

    parsed = _extract_json(getattr(result, "last_agent_message", "") or "")
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


__all__ = [
    "approve_skill",
    "ApprovalVerdict",
    "classify_skill_placement",
    "PlacementVerdict",
]
