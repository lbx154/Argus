"""Crash-safe durable identity for team formation."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from . import _store, pool, registry, roster, task_board

_RECEIPT_FILE = "dispatch_receipt.json"
_LOCK_FILE = ".formation.lock"
_ADMISSION_LOCK_FILE = ".formation-admission.lock"
_TEAM_TASK_ENV = "ARGUS_SKILL_TEAM_TASK_ID"
_ALLOW_NESTED_ENV = "ARGUS_SKILL_ALLOW_NESTED_TEAM"
# Formation caps bound admitted process/state fan-out, never live task duration.
_MAX_ACTIVE_ENV = "ARGUS_TEAM_MAX_ACTIVE_CAMPAIGNS"
_MAX_TASKS_ENV = "ARGUS_TEAM_MAX_TASKS_PER_FORMATION"
_ACTIVE_TASK_STATES = frozenset({"pending", "claimed", "running"})


def _receipt_path(root: Path) -> Path:
    return Path(root) / _RECEIPT_FILE


def _identity_payload(
    *,
    team_id: str,
    mission: str,
    lead: str,
    cwd: Path | str,
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "team_id": str(team_id).strip(),
        "mission_objective": str(mission).strip(),
        "lead": str(lead).strip(),
        "cwd": str(Path(cwd).expanduser().resolve()),
        "tasks": task_board.canonical_material_specs(tasks),
    }


def _formation_id(identity: dict[str, Any]) -> str:
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_receipt(receipt: Any, expected: dict[str, Any]) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("team dispatch receipt is corrupt")
    identity = {
        key: receipt.get(key)
        for key in (
            "schema_version",
            "team_id",
            "mission_objective",
            "lead",
            "cwd",
            "tasks",
        )
    }
    if (
        identity != expected
        or receipt.get("formation_id") != _formation_id(expected)
    ):
        raise ValueError(
            "team formation identity conflicts with the persisted dispatch receipt"
        )


def _validate_roster(
    existing: dict[str, Any],
    *,
    team_id: str,
    mission: str,
    lead: str,
) -> None:
    if not existing:
        return
    if str(existing.get("team_id") or "") != team_id:
        raise ValueError("persisted roster team identity does not match formation")
    if str(existing.get("mission_objective") or "").strip() != mission:
        raise ValueError("persisted roster mission objective does not match formation")
    if str(existing.get("lead") or "").strip() != lead:
        raise ValueError("persisted roster lead does not match formation")


def _validate_marker(
    project_root: Path,
    *,
    team_id: str,
    root: Path,
    cwd: str,
) -> None:
    marker_path = registry.marker_path(project_root, team_id)
    if not marker_path.exists():
        return
    marker = _store.read_json(marker_path, default=None)
    if not isinstance(marker, dict):
        raise ValueError("persisted team registry marker is corrupt")
    if (
        str(marker.get("team_id") or "") != team_id
        or Path(str(marker.get("team_root") or "")).expanduser().resolve() != root
        or Path(str(marker.get("cwd") or "")).expanduser().resolve()
        != Path(cwd)
    ):
        raise ValueError("persisted team registry marker conflicts with formation")


def _form_team(
    *,
    project_root: Path,
    root: Path,
    team_id: str,
    mission: str,
    lead: str,
    cwd: Path | str,
    tasks: list[dict[str, Any]],
    now: float | None = None,
) -> dict[str, Any]:
    """Complete or recover one exact team formation.

    The receipt is persisted before a new roster, making a crash at any later
    formation step resumable. Legacy receipt-less formations are adopted only
    when their complete durable board already matches the requested identity.
    """
    project_root = Path(project_root).expanduser().resolve()
    root = Path(root).expanduser().resolve()
    team_id = str(team_id).strip()
    mission = str(mission).strip()
    lead = str(lead).strip()
    cwd_text = str(Path(cwd).expanduser().resolve())
    if not team_id or not mission or not lead:
        raise ValueError("team formation requires team id, mission, and lead")
    expected = _identity_payload(
        team_id=team_id,
        mission=mission,
        lead=lead,
        cwd=cwd_text,
        tasks=tasks,
    )
    receipt_path = _receipt_path(root)
    formed_at = time.time() if now is None else float(now)

    with _store.locked(root / _LOCK_FILE):
        receipt_exists = receipt_path.exists()
        receipt = _store.read_json(receipt_path, default=None)
        existing_roster = roster.load(root)
        existing_board = task_board.snapshot(root)
        marker_path = registry.marker_path(project_root, team_id)
        pool_exists = (root / "pool.json").exists()
        persisted_without_receipt = bool(
            existing_roster
            or existing_board
            or pool_exists
        )

        if receipt_exists:
            _validate_receipt(receipt, expected)
        elif persisted_without_receipt:
            _validate_roster(
                existing_roster,
                team_id=team_id,
                mission=mission,
                lead=lead,
            )
            if not existing_board or not task_board.material_specs_match(root, tasks):
                raise ValueError(
                    "cannot recover receipt-less team formation: "
                    "persisted board does not exactly match the requested tasks"
                )

        _validate_roster(
            existing_roster,
            team_id=team_id,
            mission=mission,
            lead=lead,
        )
        resume_partial_board = False
        if existing_board and not task_board.material_specs_match(root, tasks):
            resume_partial_board = bool(
                receipt_exists
                and not pool_exists
                and not marker_path.exists()
                and not existing_roster.get("members")
                and task_board.material_specs_match(
                    root,
                    tasks,
                    allow_subset=True,
                    require_pending=True,
                )
            )
            if not resume_partial_board:
                raise ValueError(
                    "persisted team board does not exactly match the dispatch receipt"
                )
        _validate_marker(
            project_root,
            team_id=team_id,
            root=root,
            cwd=cwd_text,
        )

        if not receipt_exists:
            receipt = {
                **expected,
                "formation_id": _formation_id(expected),
                "recorded_at": formed_at,
            }
            _store.atomic_write_json(receipt_path, receipt)

        if not existing_roster:
            roster.create(
                root,
                team_id=team_id,
                mission=mission,
                lead=lead,
                now=formed_at,
            )
        if not existing_board or resume_partial_board:
            task_board.form(root, tasks)
        if not (root / "pool.json").exists():
            pool.update(root, width=0, state="running")
        if not marker_path.exists():
            registry.write_marker(
                project_root,
                team_id=team_id,
                team_root=root,
                cwd=cwd_text,
                now=formed_at,
            )
        return dict(receipt)


def _env_limit(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _nested_formation_allowed() -> bool:
    return os.environ.get(_ALLOW_NESTED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _active_campaigns(project_root: Path) -> list[str]:
    active: list[str] = []
    for marker in registry.list_markers(project_root):
        team_id = str(marker.get("team_id") or "").strip() or "(unknown)"
        try:
            root = Path(str(marker["team_root"])).expanduser().resolve()
            tasks = task_board.snapshot(root)
        except (KeyError, OSError, TypeError, ValueError):
            active.append(team_id)
            continue
        if any(str(task.get("state") or "") in _ACTIVE_TASK_STATES for task in tasks):
            active.append(team_id)
    return active


def form_team(
    *,
    project_root: Path,
    root: Path,
    team_id: str,
    mission: str,
    lead: str,
    cwd: Path | str,
    tasks: list[dict[str, Any]],
    now: float | None = None,
) -> dict[str, Any]:
    """Admit and durably form one team without unbounded recursive fanout."""
    project_root = Path(project_root).expanduser().resolve()
    root = Path(root).expanduser().resolve()
    nested_task_id = os.environ.get(_TEAM_TASK_ENV, "").strip()
    if nested_task_id and not _nested_formation_allowed():
        raise RuntimeError(
            "nested team formation is disabled inside team task "
            f"{nested_task_id!r}; the parent lead must own further delegation"
        )
    maximum_tasks = _env_limit(_MAX_TASKS_ENV, 256)
    if len(tasks) > maximum_tasks:
        raise RuntimeError(
            f"team formation has {len(tasks)} tasks, above {_MAX_TASKS_ENV}="
            f"{maximum_tasks}"
        )

    admission_lock = registry.marker_dir(project_root) / _ADMISSION_LOCK_FILE
    with _store.locked(admission_lock):
        if not registry.marker_path(project_root, team_id).exists():
            maximum_active = _env_limit(_MAX_ACTIVE_ENV, 8)
            active = _active_campaigns(project_root)
            if len(active) >= maximum_active:
                raise RuntimeError(
                    f"project already has {len(active)} active team campaigns, "
                    f"reaching {_MAX_ACTIVE_ENV}={maximum_active}: "
                    + ", ".join(active[:maximum_active])
                )
        return _form_team(
            project_root=project_root,
            root=root,
            team_id=team_id,
            mission=mission,
            lead=lead,
            cwd=cwd,
            tasks=tasks,
            now=now,
        )


def load_receipt(root: Path) -> dict[str, Any]:
    receipt = _store.read_json(_receipt_path(root), default={})
    return dict(receipt) if isinstance(receipt, dict) else {}


__all__ = ["form_team", "load_receipt"]
