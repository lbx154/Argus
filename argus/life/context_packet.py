"""Versioned mission metadata and references for fresh agent sessions.

The packet indexes canonical sources without copying their prose:
``mission.json`` owns the task contract, ``CHECKPOINT.md`` owns durable state, and
the Reviewer verdict owns control.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Mapping

CONTEXT_PACKET_VERSION = 3
CHECKPOINT_CONTRACT_VERSION = 2
CHECKPOINT_FILENAME = "CHECKPOINT.md"
HANDOFF_DIRNAME = "handoffs"

log = logging.getLogger(__name__)


def _initialize_checkpoint(path: Path) -> bool:
    """Atomically create the mission's empty optional checkpoint placeholder.

    Empty is deliberate: round one should not pay checkpoint prompt overhead
    until a role actually has continuation state to preserve.  Exclusive create
    avoids overwriting a role-authored checkpoint when mission context is
    refreshed.  Failure is advisory because readers independently tolerate an
    absent or concurrently deleted checkpoint.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | getattr(os, "O_BINARY", 0) | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return True
    except OSError as exc:
        log.warning("could not initialize optional mission checkpoint %s: %s", path, exc)
        return False
    try:
        os.close(descriptor)
    except OSError as exc:
        log.warning("could not close initialized mission checkpoint %s: %s", path, exc)
        return False
    return True


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _file_reference(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": ""}
    return {"path": str(path)}


def _model_visible_context_ref(ref: Mapping[str, Any]) -> dict[str, str]:
    """Drop host-only integrity metadata from agent-readable context packets."""
    hidden = {"content_hash", "hash", "digest"}
    return {
        str(key): str(value)
        for key, value in ref.items()
        if str(key).strip().casefold() not in hidden
    }


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def render_mission_contract(path: Path | str | None) -> str:
    """Render the canonical mission fields once for a fresh role turn."""
    if not path:
        return ""
    payload = _read_json_object(Path(path).expanduser())
    if str(payload.get("kind") or "") != "mission_context":
        return ""
    objective = str(payload.get("objective") or "").strip()
    if not objective:
        return ""
    lines = ["## Mission contract", objective]
    acceptance = str(payload.get("acceptance_check") or "").strip()
    if acceptance:
        lines.extend(("", "Acceptance:", acceptance))
    non_goals = [
        str(item).strip()
        for item in payload.get("non_goals") or []
        if str(item).strip()
    ]
    if non_goals:
        lines.extend(("", "Non-goals:", *(f"- {item}" for item in non_goals)))
    return "\n".join(lines)


def _brief_text(value: Any, *, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit]


def _latest_reviewed_handoff(root: Path) -> dict[str, Any]:
    """Read the newest sealed Reviewer handoff, ignoring Engineer-only seals."""
    candidates: list[tuple[float, str, dict[str, Any]]] = []
    for path in root.glob("round-*.json"):
        payload = _read_json_object(path)
        if str(payload.get("kind") or "") == "round_reviewed_handoff":
            try:
                created = float(payload.get("created_at") or 0)
            except (TypeError, ValueError):
                created = 0
            if not math.isfinite(created) or created <= 0:
                try:
                    created = path.stat().st_mtime
                except OSError:
                    continue
            candidates.append((created, path.name, payload))
    # A resumed mission starts counting rounds at one again. Filename order
    # can therefore select an older review from before the restart.
    return max(candidates, key=lambda entry: entry[:2])[2] if candidates else {}


def _latest_unreviewed_engineer(root: Path) -> tuple[dict[str, Any], Path | None]:
    path = root / "latest.json"
    latest = _read_json_object(path)
    if latest.get("kind") == "handoff_ref":
        reference = latest.get("handoff")
        if not isinstance(reference, Mapping) or not reference.get("path"):
            return {}, None
        path = Path(str(reference["path"])).expanduser()
        try:
            local_reference = path.resolve().parent == root.resolve()
        except (OSError, RuntimeError, ValueError):
            return {}, None
        if not local_reference:
            return {}, None
        latest = _read_json_object(path)
    if latest.get("kind") != "round_engineer_handoff":
        return {}, None
    return latest, path


def render_mission_brief(
    path: Path | str | None,
    *,
    include_engineer_account: bool = True,
    include_previous_review: bool = True,
) -> str:
    """Project canonical mission state into one compact role briefing.

    The projection selects named semantic fields only. It never reads a
    checkpoint or transcript and writes no state of its own. Unreviewed work is
    distinguished from the last reviewed state. A Reviewer that already receives
    the current Engineer response can omit that duplicate account, and one
    that already receives its own previous findings can omit their summary.
    """
    if not path:
        return ""
    mission_path = Path(path).expanduser()
    mission = _read_json_object(mission_path)
    if str(mission.get("kind") or "") != "mission_context":
        return ""

    reviewed = _latest_reviewed_handoff(mission_path.parent)
    pending_engineer, pending_path = _latest_unreviewed_engineer(mission_path.parent)
    if pending_engineer and reviewed:
        try:
            pending_time = float(pending_engineer.get("created_at") or 0)
            reviewed_time = float(reviewed.get("created_at") or 0)
        except (TypeError, ValueError):
            pending_time = reviewed_time = 0
        if 0 < pending_time <= reviewed_time and math.isfinite(reviewed_time):
            # A stale reference must not make already reviewed work look pending.
            pending_engineer, pending_path = {}, None
    review = reviewed.get("review")
    review = review if isinstance(review, Mapping) else {}
    status = _brief_text(review.get("status"), limit=40)

    lines = ["## MissionBrief"]
    workdir = _brief_text(mission.get("execution_workdir"), limit=4000)
    stage = _brief_text(mission.get("stage"), limit=120)
    acceptance = _brief_text(mission.get("acceptance_check"))
    if workdir:
        lines.append(f"- Workdir: `{workdir}`")
    if stage:
        lines.append(f"- Stage: {stage}")
    owns_paths = [
        str(item).strip()
        for item in (mission.get("owns_paths") or [])
        if str(item).strip()
    ]
    if owns_paths:
        lines.append(
            "- Owned paths (authoritative write boundary; Reviewer must not "
            "request edits outside it): " + "; ".join(owns_paths)
        )

    resources: list[str] = []
    context_refs = mission.get("context_refs")
    if isinstance(context_refs, list):
        for ref in context_refs:
            if not isinstance(ref, Mapping):
                continue
            location = _brief_text(ref.get("ref"), limit=400)
            if not location:
                continue
            kind = _brief_text(ref.get("kind"), limit=80)
            why = _brief_text(ref.get("why"), limit=240)
            label = f"{kind}: {location}" if kind else location
            resources.append(f"{label} ({why})" if why else label)
    if resources:
        lines.append("- Tools/resources: " + "; ".join(resources[:6]))
    if acceptance:
        lines.append(f"- Native check: {acceptance}")

    reason = _brief_text(review.get("reason")) if include_previous_review else ""
    if reason:
        result = f"{status}: {reason}" if status else reason
        review_label = "Previous review" if pending_engineer else "Last review"
        lines.append(f"- {review_label}: {result}")
    # Unreviewed work awaits review only where the Engineer's line sent it there.
    holder = str(pending_engineer.get("holder") or "reviewer")
    if pending_path is not None:
        lines.append(
            f"- Latest work awaiting review: `{pending_path}`" if holder == "reviewer"
            else f"- Latest Engineer work (→ {holder}): `{pending_path}`"
        )
    engineer_summary = _brief_text(
        (pending_engineer or reviewed).get("engineer_summary"), limit=1200,
    )
    if engineer_summary and include_engineer_account:
        label = (
            "Reviewed Engineer work" if not pending_engineer
            else "Unreviewed Engineer work" if holder == "reviewer"
            else f"Engineer work (→ {holder})"
        )
        lines.append(f"- {label}: {engineer_summary}")
    next_action = (
        _brief_text(review.get("next_action")) if include_previous_review else ""
    )
    # The same text already appears as the review reason one line above.
    if next_action and next_action != reason:
        label = "Previously requested action" if pending_engineer else "Next action"
        lines.append(f"- {label}: {next_action}")
    if isinstance(review.get("venue_review"), Mapping):
        feedback_path = round_handoff_path(
            mission_path.parent, int(reviewed.get("round") or 1), mission_run(reviewed),
        )
        lines.append(
            f"- Complete final-review feedback: `{feedback_path}`. Read review.next_action "
            "and review.venue_review before revising; the brief above is abbreviated."
        )

    return "\n".join(lines) if len(lines) > 1 else ""


def _attach_mission_metadata(
    mission_path: Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a round handoff with one reference to the mission contract."""
    out = dict(payload)
    out["mission"] = {"path": str(mission_path)}
    return out


def _latest_handoff_reference(
    *,
    mission_path: Path,
    handoff_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "handoff_ref",
        "mission": {"path": str(mission_path)},
        "handoff": _file_reference(handoff_path),
    }


def mission_context_dir(life_dir: Path | str, mission_id: str) -> Path:
    return Path(life_dir).expanduser() / HANDOFF_DIRNAME / str(mission_id)


def mission_run(payload: Mapping[str, Any]) -> int:
    """Which run of the mission a mission context or round handoff belongs to.

    Records written before runs were counted belong to the first run.
    """
    return max(1, int(payload.get("run") or 1))


def round_handoff_path(root: Path, round_index: int, run: int, suffix: str = "") -> Path:
    """Name one round record without colliding with another run's rounds.

    Every run of a mission (resume, requeue for iteration, orphan retry) keeps
    the mission id but counts rounds from one again. The first run keeps the
    original names; later runs carry their run number, so a new run never
    overwrites an earlier run's record.
    """
    stem = f"round-{max(1, int(round_index)):04d}"
    if run > 1:
        stem += f"-run-{run:02d}"
    return root / f"{stem}{suffix}.json"


def create_mission_context(
    *,
    life_dir: Path | str,
    mission_id: str,
    stage: str,
    objective: str,
    scope: str = "",
    acceptance_check: str = "",
    plan_hypothesis: str = "",
    goal_contribution: str = "",
    expected_regressions: str = "",
    decision_rule: str = "",
    execution_workdir: str = "",
    owns_paths: list[str] | None = None,
    non_goals: list[str] | None = None,
    context_refs: list[dict[str, str]] | None = None,
    plan_id: str = "",
    plan_version: int = 0,
    node_key: str = "",
    deps: list[str] | None = None,
    tags: list[str] | None = None,
) -> Path:
    """Create or refresh the immutable mission-level handoff description."""
    root = mission_context_dir(life_dir, mission_id)
    path = root / "mission.json"
    checkpoint_path = root / CHECKPOINT_FILENAME
    _initialize_checkpoint(checkpoint_path)
    existing_created_at = time.time()
    existing: dict[str, Any] = {}
    try:
        existing = _read_json_object(path)
        existing_created_at = float(existing.get("created_at") or existing_created_at)
    except (OSError, ValueError, TypeError):
        pass
    # The supervisor creates the context once at the start of every run of
    # this mission, so each call opens the next run.
    run = mission_run(existing) + 1 if existing else 1
    payload = {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "mission_context",
        "mission_id": str(mission_id),
        "run": run,
        "stage": str(stage or ""),
        "scope": str(scope or ""),
        "objective": str(objective or "").strip(),
        "acceptance_check": str(acceptance_check or "").strip(),
        "plan_hypothesis": str(plan_hypothesis or "").strip(),
        "goal_contribution": str(goal_contribution or "").strip(),
        "expected_regressions": str(expected_regressions or "").strip(),
        "decision_rule": str(decision_rule or "").strip(),
        "execution_workdir": str(execution_workdir or "").strip(),
        "owns_paths": [
            str(item).strip()
            for item in (owns_paths or [])
            if str(item).strip()
        ],
        "non_goals": [str(item).strip() for item in (non_goals or []) if str(item).strip()],
        "context_refs": [
            _model_visible_context_ref(ref)
            for ref in (context_refs or [])
            if isinstance(ref, dict) and str(ref.get("ref") or "").strip()
        ],
        "plan_id": str(plan_id or ""),
        "plan_version": max(0, int(plan_version or 0)),
        "node_key": str(node_key or ""),
        "deps": [str(dep) for dep in (deps or [])],
        "tags": [str(tag) for tag in (tags or [])],
        "checkpoint": {
            **_file_reference(checkpoint_path),
            "contract_version": CHECKPOINT_CONTRACT_VERSION,
        },
        "created_at": existing_created_at,
        "updated_at": time.time(),
    }
    _atomic_write_json(path, payload)
    latest_path = root / "latest.json"
    if not latest_path.exists():
        _atomic_write_json(root / "latest.json", payload)
    else:
        latest = _read_json_object(latest_path)
        if (
            str(latest.get("kind") or "") != "mission_context"
            and latest.get("mission") != {"path": str(path)}
        ):
            # Current references already resolve the refreshed contract. Avoid
            # racing a new round by overwriting its latest pointer needlessly.
            _atomic_write_json(latest_path, _attach_mission_metadata(path, latest))
    return path


def record_engineer_handoff(
    *,
    mission_context_path: Path | str | None,
    round_index: int,
    engineer_summary: str,
    checkpoint_path: Path | None,
    thread_id: str = "",
    holder: str = "reviewer",
) -> Path | None:
    """Seal the Engineer's turn with ``holder``, where its line sent the work."""
    if not mission_context_path:
        return None
    mission_path = Path(mission_context_path)
    root = mission_path.parent
    run = mission_run(_read_json_object(mission_path))
    payload = {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "round_engineer_handoff",
        "mission_context": str(mission_path),
        "mission_id": root.name,
        "run": run,
        "round": max(1, int(round_index)),
        "producer_role": "engineer",
        "engineer_summary": str(engineer_summary or "").strip()[:4000],
        "holder": holder,
        "session_id": str(thread_id or ""),
        "checkpoint": _file_reference(checkpoint_path),
        "created_at": time.time(),
    }
    path = round_handoff_path(root, round_index, run, "-engineer")
    _atomic_write_json(path, payload)
    _atomic_write_json(
        root / "latest.json",
        _latest_handoff_reference(
            mission_path=mission_path,
            handoff_path=path,
        ),
    )
    return path


def record_reviewed_handoff(
    *,
    mission_context_path: Path | str | None,
    round_index: int,
    engineer_summary: str,
    review: Any,
    checkpoint_path: Path | None,
) -> Path | None:
    if not mission_context_path:
        return None
    mission_path = Path(mission_context_path)
    root = mission_path.parent
    # Keep the canonical final-review handoff complete. In particular, a long
    # experimental plan must not lose its controls or final success criterion.
    feedback_limit = None if isinstance(getattr(review, "venue_review", None), dict) else 4000
    review_payload: dict[str, Any] = {
        "status": str(getattr(review, "status", "") or ""),
        "reason": str(getattr(review, "reason", "") or "")[:feedback_limit],
        "next_action": str(getattr(review, "next_action", "") or "")[:feedback_limit],
        "operator_question": str(getattr(review, "operator_question", "") or "")[:1000],
    }
    review_source = str(getattr(review, "review_source", "") or "").strip()
    if review_source:
        review_payload["review_source"] = review_source
    # The Manager reads this packet as evidence: a check this environment
    # cannot run, and a risk the Reviewer accepted, belong in it by name.
    for key in (
        "verification_obstacle", "verification_obstacle_basis", "verification_obstacle_basis_source",
        "residual_risk",
    ):
        value = str(getattr(review, key, "") or "").strip()
        if value:
            review_payload[key] = value[:2000]
    manuscript_binding = getattr(review, "manuscript_snapshot", None)
    if isinstance(manuscript_binding, dict):
        review_payload["manuscript_snapshot"] = dict(manuscript_binding)
    for key in ("venue_review", "venue_review_snapshot"):
        value = getattr(review, key, None)
        if isinstance(value, dict):
            review_payload[key] = dict(value)
    mission = _read_json_object(mission_path)
    if (
        str(mission.get("scope") or "").strip().lower() == "final_submission"
        and isinstance(manuscript_binding, dict)
    ):
        candidate_root_text = str(mission.get("execution_workdir") or "").strip()
        candidate_root = Path(candidate_root_text).expanduser()
        if candidate_root_text and candidate_root.is_dir():
            from .terminal_state import build_project_state_signature

            review_payload["final_submission_signature"] = (
                build_project_state_signature(
                    project_root=candidate_root,
                    state_root=root,
                )
            )
    payload = {
        "schema_version": CONTEXT_PACKET_VERSION,
        "kind": "round_reviewed_handoff",
        "mission_context": str(mission_path),
        "mission_id": root.name,
        "run": mission_run(mission),
        "round": max(1, int(round_index)),
        "producer_role": "reviewer",
        "engineer_summary": str(engineer_summary or "").strip()[:4000],
        "review": review_payload,
        "checkpoint": _file_reference(checkpoint_path),
        "created_at": time.time(),
    }
    path = round_handoff_path(root, round_index, mission_run(mission))
    _atomic_write_json(path, payload)
    _atomic_write_json(
        root / "latest.json",
        _latest_handoff_reference(
            mission_path=mission_path,
            handoff_path=path,
        ),
    )
    return path


__all__ = [
    "CHECKPOINT_CONTRACT_VERSION",
    "CHECKPOINT_FILENAME",
    "CONTEXT_PACKET_VERSION",
    "create_mission_context",
    "mission_context_dir",
    "mission_run",
    "record_engineer_handoff",
    "record_reviewed_handoff",
    "render_mission_brief",
    "render_mission_contract",
    "round_handoff_path",
]
