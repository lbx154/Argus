from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from ..memory import JournalEntry
from ._constants import PLANNER_RECENT_FAILURE_STATUS


def _resolve_task_dep_ids(
    deps: list[str],
    key_map: dict[str, str],
) -> tuple[list[str], list[str]]:
    """Map a task's *local* dep keys to real backlog item ids.

    ``deps`` are the local ``key`` references the planner emitted on a task;
    ``key_map`` maps each in-batch local key to the real ``BacklogItem.id``
    chosen for the task that declared it. Returns ``(resolved_ids,
    unresolved_keys)``:

    * a local key present in ``key_map`` becomes its real item id (de-duped,
      order-preserving — a dep can only be satisfied once);
    * a local key NOT in ``key_map`` (typo, or a cross-cycle reference, which
      is unsupported) is dropped and reported in ``unresolved_keys`` so the
      caller can ``log.warning`` it.

    A task with no ``deps`` yields ``([], [])`` — i.e. a flat item, scheduled
    exactly as before the DAG existed.
    """
    resolved: list[str] = []
    unresolved: list[str] = []
    seen: set[str] = set()
    for key in deps:
        item_id = key_map.get(key)
        if item_id is None:
            unresolved.append(key)
            continue
        if item_id in seen:
            continue
        seen.add(item_id)
        resolved.append(item_id)
    return resolved, unresolved


def _operator_only_blocker_paths_for_project(project_root: Path) -> list[Path]:
    """Return existing operator-only external-blocker artifact paths.

    Looks for `diagnosis/operator_only_external_blocker*.json` so both the
    legacy dated lock file and the undated generic filename match. Returned
    newest first by mtime; empty list when none.
    """
    diagnosis = project_root / "diagnosis"
    if not diagnosis.is_dir():
        return []
    candidates: list[Path] = []
    for path in diagnosis.glob("operator_only_external_blocker*.json"):
        if path.is_file():
            candidates.append(path)
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates


def _operator_only_external_blocker_wait_reason_for_project(project_root: Path) -> str:
    """Return a wait reason for an operator-only external blocker artifact."""
    for lock_path in _operator_only_blocker_paths_for_project(project_root):
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return (
                f"operator-only external blocker {lock_path.name} is present "
                "but unreadable (malformed JSON); treating as active blocker "
                "pending operator fix"
            )
        if not isinstance(payload, dict):
            continue
        if payload.get("local_engineer_action_required_before_mount") is not False:
            continue
        required = payload.get("required_external_targets")
        if not isinstance(required, list) or not required:
            continue
        missing = [
            str(item)
            for item in required
            if isinstance(item, str) and not (project_root / item).exists()
        ]
        if not missing:
            continue
        owner = payload.get("next_owner") or "operator/data owner"
        verdict = (
            payload.get("canonical_viability_verdict")
            or "external artifacts missing"
        )
        sample_missing = ", ".join(missing[:4])
        return (
            f"operator-only external benchmark blocker ({lock_path.name}): "
            f"{verdict}; {len(missing)} required external target(s) still "
            f"absent ({sample_missing}); next owner is {owner}"
        )
    return ""


def _normalize_planner_text(text: str) -> str:
    """Normalize planner task text for duplicate detection."""
    normalized = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _sanitize_planner_task_text(text: str) -> str:
    """Remove stale host-specific entry paths from planner-generated missions."""
    value = str(text)
    command_replacements = {
        (
            "PYTHONPATH=/home/argustest/argus-skill "
            "/home/argustest/miniconda3/bin/python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
        (
            "PYTHONPATH=/home/argustest/argus-skill "
            "python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
        (
            "/home/argustest/miniconda3/bin/python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
    }
    for old, new in command_replacements.items():
        value = value.replace(old, new)
    path_replacements = {
        "`/home/argustest/research.md`": "the operator-provided research playbook if present",
        "/home/argustest/research.md": "the operator-provided research playbook if present",
        "`/home/argustest/argus-skill`": "the active Argus source/package",
        "/home/argustest/argus-skill": "the active Argus source/package",
        (
            "`/root/Auto-claude-code-research-in-sleep/skills/"
            "paper-illustration-image2/SKILL.md`"
        ): "`argus_builtin_skills/engineer/paper-illustration-image2.md`",
        (
            "/root/Auto-claude-code-research-in-sleep/skills/"
            "paper-illustration-image2/SKILL.md"
        ): "argus_builtin_skills/engineer/paper-illustration-image2.md",
    }
    for old, new in path_replacements.items():
        value = value.replace(old, new)
    return value


def _planner_task_signature(
    title: str,
    objective: str,
    *,
    acceptance_check: str = "",
    context_refs: list[dict[str, str]] | None = None,
    scope: str = "",
    stage_closing: bool = False,
    require_independent_review: bool = False,
    skip_stage_transition: bool = False,
) -> tuple[str, ...]:
    """Identity for deduping work, including the evidence revision it reads.

    Title/objective-only dedup incorrectly suppresses a legitimate rerun after
    an upstream artifact changes. Stable context refs keep true duplicates
    filtered while a changed content hash creates a new acceptance unit.
    """
    refs = sorted(
        (
            str(ref.get("kind") or "").strip(),
            str(ref.get("ref") or "").strip(),
            str(ref.get("content_hash") or "").strip(),
        )
        for ref in (context_refs or [])
        if isinstance(ref, dict)
    )
    return (
        _normalize_planner_text(title),
        _normalize_planner_text(objective),
        _normalize_planner_text(acceptance_check),
        json.dumps(refs, ensure_ascii=False, separators=(",", ":")),
        str(scope or "").strip().lower().replace("-", "_"),
        "stage_closing" if stage_closing else "not_stage_closing",
        (
            "independent_review_required"
            if require_independent_review
            else "independent_review_optional"
        ),
        "stage_transition_skipped" if skip_stage_transition else "stage_transition_allowed",
    )


def _normalize_blocker_fingerprint(value: object) -> str:
    """Normalize a Planner-authored blocker identity without using task prose."""
    return " ".join(str(value or "").strip().lower().split())[:500]


def _entry_task_signature(entry: JournalEntry) -> tuple[str, str] | None:
    extra = getattr(entry, "extra", {}) or {}
    signature = extra.get("planner_task_signature")
    title = ""
    objective = ""
    if isinstance(signature, dict):
        title = str(signature.get("title", "") or "")
        objective = str(signature.get("objective", "") or "")
    elif isinstance(signature, (list, tuple)) and len(signature) >= 2:
        title = str(signature[0] or "")
        objective = str(signature[1] or "")
    else:
        title = str(extra.get("title") or entry.title or "")
        objective = str(extra.get("objective") or "")
    normalized_title = _normalize_planner_text(title)
    normalized_objective = _normalize_planner_text(objective)
    if not normalized_title and not normalized_objective:
        return None
    return normalized_title, normalized_objective


def _is_recent_no_progress_failure(entry: JournalEntry) -> bool:
    if entry.kind != "mission_failed":
        return False
    extra = getattr(entry, "extra", {}) or {}
    terminal_status = str(
        extra.get("terminal_status")
        or extra.get("status")
        or extra.get("failure_status")
        or ""
    ).strip().casefold()
    return terminal_status == PLANNER_RECENT_FAILURE_STATUS
