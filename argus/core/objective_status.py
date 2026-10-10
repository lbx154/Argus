"""The Reviewer's latest judgment of the operator's whole objective, kept per project.

An approval can say the increment is done while the objective is not
(``ReviewDecision.objective_status`` partial / not_met). Completion reads that
judgment from the review in hand, but a later turn may bring no review at all
(the Manager's terminal reconciliation builds a synthetic ``done``). So the
latest real judgment is kept here, and a partial or not_met one keeps the
project from completing until a real review says ``met``, or the Manager
judges the objective unachievable as stated and finishes the project as
PARTIAL with the gap recorded (``finished_partial``).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

FILENAME = "OBJECTIVE_STATUS.json"
NOT_ESTABLISHED = frozenset({"partial", "not_met"})


def objective_status_path(project_root: Path | str) -> Path:
    return Path(project_root) / ".argus" / FILENAME


def read_objective_status(project_root: Path | str | None) -> dict[str, Any]:
    if project_root is None:
        return {}
    try:
        payload = json.loads(objective_status_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write(project_root: Path | str, payload: dict[str, Any]) -> None:
    path = objective_status_path(project_root)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return
    finally:
        temporary.unlink(missing_ok=True)


def is_real_review(review: Any) -> bool:
    """A verdict the independent Reviewer returned, not a host placeholder."""
    return (
        str(getattr(review, "review_source", "") or "").strip().lower() == "reviewer"
        and not str(getattr(review, "host_placeholder", "") or "").strip()
    )


def record_review_objective(project_root: Path | str | None, review: Any) -> None:
    """Keep a real approving review's objective judgment; ignore anything else."""
    if project_root is None or not is_real_review(review):
        return
    if str(getattr(review, "status", "") or "").strip().lower() != "done":
        return
    status = str(getattr(review, "objective_status", "") or "").strip().lower()
    if status not in NOT_ESTABLISHED | {"met"}:
        return
    _write(project_root, {
        "version": 1,
        "status": status,
        "gap": " ".join(str(getattr(review, "objective_gap", "") or "").split())[:2000]
        if status != "met" else "",
        "residual_risk": str(getattr(review, "residual_risk", "") or "").strip()[:2000],
        "recorded_at": time.time(),
    })


def outstanding_objective_gap(project_root: Path | str | None) -> dict[str, Any]:
    """The recorded partial/not_met judgment still standing, or {}."""
    record = read_objective_status(project_root)
    if str(record.get("status") or "") in NOT_ESTABLISHED and not record.get("finished_partial"):
        return record
    return {}


def mark_finished_partial(project_root: Path | str, *, reason: str) -> dict[str, Any]:
    """Record that the project ends as PARTIAL on the standing objective gap."""
    record = read_objective_status(project_root)
    record.update(
        finished_partial=True,
        finished_reason=" ".join(str(reason or "").split())[:2000],
        finished_at=time.time(),
    )
    record.setdefault("status", "partial")
    _write(project_root, record)
    return record


def finished_partial(project_root: Path | str | None) -> dict[str, Any]:
    """The PARTIAL finish record, or {} when the project did not end partial."""
    record = read_objective_status(project_root)
    return record if record.get("finished_partial") else {}


__all__ = [
    "FILENAME", "NOT_ESTABLISHED", "finished_partial", "is_real_review",
    "mark_finished_partial", "objective_status_path", "outstanding_objective_gap",
    "read_objective_status", "record_review_objective",
]
