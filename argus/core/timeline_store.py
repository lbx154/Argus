"""Immutable proposal forecast revisions, separate from runtime verdict events."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .file_lock import exclusive_file_lock
from .timeline import estimate
from .timeline_models import label


class TimelineVersionConflict(ValueError):
    """Another writer has published a forecast since the caller loaded it."""


def latest(project_root: Path) -> dict | None:
    root = Path(project_root) / ".argus" / "timeline"
    versions = sorted(root.glob("[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    return json.loads(versions[-1].read_text(encoding="utf-8")) if versions else None


def _tasks(payload):
    return {(p["id"], task["id"]): task for p in payload["proposals"] for task in p["tasks"]}


def _finish(report):
    selected = next(p for p in report["proposals"] if p["id"] == report["selected_proposal_id"])
    interval = selected["finish_hours"]
    return interval["expected"] if interval else None


def _variances(report, baseline):
    planned = {(p["id"], row["id"]): row for p in baseline["proposals"] for row in p["schedule"]}
    result = []
    for proposal in report["proposals"]:
        for row in proposal["schedule"]:
            before = planned.get((proposal["id"], row["id"]))
            if before is None or row["finish_hours"] <= before["finish_hours"]:
                continue
            result.append(
                dict(
                    proposal_id=proposal["id"],
                    id=row["id"],
                    baseline_finish_hours=before["finish_hours"],
                    current_finish_hours=row["finish_hours"],
                    delay_hours=row["finish_hours"] - before["finish_hours"],
                    observed=row["status"] in {"completed", "failed"},
                    resource_wait_hours=row["resource_wait_hours"],
                    depends_on=row["depends_on"],
                    reason=row["reason"] or "原因待确认 / Cause not yet diagnosed",
                    reason_status="reported" if row["reason"] else "unconfirmed",
                    evidence=row["evidence"],
                )
            )
    return result


def _revision(payload, report, previous, baseline, reason):
    if payload.get("now_hours", 0) < previous["input"].get("now_hours", 0):
        raise ValueError("now_hours cannot move backwards")
    old_tasks, new_tasks = _tasks(previous["input"]), _tasks(payload)
    for key, old in old_tasks.items():
        new = new_tasks.get(key)
        if old.get("status") in {"completed", "failed", "running"}:
            if new is None or new.get("status", "pending") == "pending":
                raise ValueError(f"executed task {key} cannot be removed or requeued")
            immutable = [
                "actual_start_hours",
                "resources",
                "depends_on",
                "duration_hours",
                "execution_option_id",
            ]
            if old.get("status") in {"completed", "failed"}:
                immutable += ["status", "actual_finish_hours"]
            elif new.get("status") not in {"running", "completed", "failed"}:
                raise ValueError(f"executed task {key} must preserve its running or terminal state")
            if any(old.get(field) != new.get(field) for field in immutable):
                raise ValueError(f"executed task {key} cannot rewrite actual history")
    changes = []
    for key in sorted(old_tasks.keys() | new_tasks.keys()):
        before, after = old_tasks.get(key), new_tasks.get(key)
        if before == after:
            continue
        row = after or {}
        changes.append(
            dict(
                proposal_id=key[0],
                id=key[1],
                change="added" if before is None else "removed" if after is None else "updated",
                reason=row.get("reason") or "原因待确认 / Cause not yet diagnosed",
                reason_status="reported" if row.get("reason") else "unconfirmed",
                evidence=row.get("evidence", []),
                previous_duration_hours=None if before is None else before.get("duration_hours"),
                revised_duration_hours=row.get("duration_hours"),
            )
        )
    finish, old_finish, initial_finish = (
        _finish(report),
        _finish(previous["report"]),
        _finish(baseline["report"]),
    )
    return dict(
        reason=reason,
        previous_version=previous["version"],
        previous_selected_proposal_id=previous["report"]["selected_proposal_id"],
        baseline_selected_proposal_id=baseline["report"]["selected_proposal_id"],
        baseline_finish_hours=initial_finish,
        previous_finish_hours=old_finish,
        previous_delta_hours=None if finish is None or old_finish is None else finish - old_finish,
        baseline_delta_hours=None
        if finish is None or initial_finish is None
        else finish - initial_finish,
        changed_tasks=changes,
        task_variances=_variances(report, baseline["report"]),
        previous_deadline_hours=previous["report"]["deadline_hours"],
        previous_resources=previous["report"]["resources"],
    )


def record(project_root: Path, payload: dict, *, expected_version: int, reason: str) -> dict:
    """CAS against the latest revision; old files remain unchanged on every update."""
    reason = label(reason, "revision reason")
    if (
        isinstance(expected_version, bool)
        or not isinstance(expected_version, int)
        or expected_version < 0
    ):
        raise ValueError("expected_version must be a nonnegative integer")
    # Copy the caller's input so concurrent caller mutation cannot alter the record.
    payload = json.loads(json.dumps(payload, allow_nan=False))
    report = estimate(payload)
    root = Path(project_root) / ".argus" / "timeline"
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lock").open("a+b") as handle, exclusive_file_lock(handle, lock_name="timeline"):
        versions = sorted(root.glob("[0-9][0-9][0-9][0-9][0-9][0-9].json"))
        version = int(versions[-1].stem) if versions else 0
        if version != expected_version:
            raise TimelineVersionConflict(
                f"timeline version conflict: expected {expected_version}, found {version}"
            )
        if version >= 999999:
            raise ValueError("timeline revision limit reached")
        if versions:
            if versions[0].name != "000001.json":
                raise ValueError("original timeline revision is missing; cannot compare baseline")
            previous = json.loads(versions[-1].read_text(encoding="utf-8"))
            baseline = json.loads(versions[0].read_text(encoding="utf-8"))
            report["revision"] = _revision(payload, report, previous, baseline, reason)
        report["version"] = version + 1
        entry = dict(
            version=version + 1,
            created_at=datetime.now(timezone.utc).isoformat(),
            reason=reason,
            input=payload,
            report=report,
        )
        path = root / f"{version + 1:06d}.json"
        # Only publish a complete, fsynced record. A killed writer leaves an ignored
        # temporary file, never a truncated revision or a rewritten original plan.
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(entry, output, ensure_ascii=False, indent=2, allow_nan=False)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return entry
