"""Single enqueue-boundary fact check for paper-wide review purchases."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from ...core.manuscript_snapshot import (
    manuscript_review_status,
    recorded_manuscript_snapshot,
)
from .method_freeze import load_method_freeze

_PAPER_REVIEW_MARKERS = (
    "publication scale assessment",
    "publication assessment",
    "paper wide review",
    "final paper review",
    "paper infrastructure review",
    "paper layout review",
    "layout review",
    "academic language review",
    "submission assessment",
    "submission package certification",
    "final submission review",
    "final submission certification",
)


def _normalized_task_text(task: Any) -> str:
    text = " ".join(
        str(getattr(task, field, "") or "")
        for field in ("title", "objective", "acceptance_check")
    ).casefold().replace("_", "-")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def is_paper_wide_review_task(task: Any, *, vertical: str) -> bool:
    if str(vertical or "").strip().casefold() != "research":
        return False
    text = _normalized_task_text(task)
    return any(marker in text for marker in _PAPER_REVIEW_MARKERS)


def _review_artifacts(project_root: Path) -> Iterable[tuple[Path, dict[str, Any]]]:
    for directory_name in ("paper", "analysis"):
        directory = project_root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and recorded_manuscript_snapshot(payload):
                yield path, payload


def _unexpired(payload: dict[str, Any]) -> bool:
    try:
        expires_at = float(payload.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        expires_at = 0.0
    return expires_at <= 0.0 or expires_at > time.time()


def _timestamp(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def completed_review_predates_freeze(
    item: Any,
    *,
    vertical: str,
    project_root: Path | str,
) -> bool:
    """Whether a completed semantic duplicate belongs to pre-freeze exploration."""
    if str(getattr(item, "status", "") or "") != "done":
        return False
    if not is_paper_wide_review_task(item, vertical=vertical):
        return False
    freeze = load_method_freeze(project_root)
    if freeze is None:
        return False
    frozen_at = _timestamp(freeze.get("frozen_at"))
    finished_at = _timestamp(
        getattr(item, "finished_ts", 0.0) or getattr(item, "ts", 0.0)
    )
    return bool(frozen_at and finished_at and finished_at < frozen_at)


def method_freeze_timestamp(project_root: Path | str) -> float:
    freeze = load_method_freeze(project_root)
    return _timestamp((freeze or {}).get("frozen_at"))


def paper_review_purchase_defer_reason(
    task: Any,
    *,
    vertical: str,
    project_root: Path | str,
    existing_items: Iterable[Any],
    semantic_duplicate: Any | None = None,
) -> str:
    """Return why this one model-scale review purchase must be deferred."""
    if not is_paper_wide_review_task(task, vertical=vertical):
        return ""
    if semantic_duplicate is not None and str(
        getattr(semantic_duplicate, "status", "") or ""
    ) not in {"done", "failed", "aborted", "skipped", "superseded"}:
        return (
            "semantically equal paper-wide review is already active "
            f"({getattr(semantic_duplicate, 'id', 'unknown')})"
        )

    root = Path(project_root).resolve()
    freeze = load_method_freeze(root)
    frozen_at = _timestamp((freeze or {}).get("frozen_at"))

    for path, payload in _review_artifacts(root):
        status = manuscript_review_status(payload, root)
        relative = path.relative_to(root).as_posix()
        reviewed_at = _timestamp(status.get("reviewed_at"))
        if freeze is not None:
            if (
                status["status"] == "current"
                and _unexpired(payload)
                and frozen_at
                and reviewed_at >= frozen_at
            ):
                return (
                    "an unexpired post-freeze current-SHA paper review already "
                    f"exists ({relative})"
                )
            continue
        if status["status"] == "current" and _unexpired(payload):
            return (
                f"manuscript is unfrozen and an unexpired current-SHA review "
                f"already exists ({relative})"
            )
        if status["status"] == "stale":
            return (
                f"manuscript is unfrozen; prior review is {status['message']} "
                f"({relative}); staleness is a planning fact, not a review trigger"
            )

    for item in existing_items:
        if str(getattr(item, "status", "") or "") != "done":
            continue
        if is_paper_wide_review_task(item, vertical=vertical):
            if freeze is not None:
                finished_at = _timestamp(
                    getattr(item, "finished_ts", 0.0)
                    or getattr(item, "ts", 0.0)
                )
                if frozen_at and finished_at < frozen_at:
                    continue
                return (
                    "a post-freeze paper-wide review task already completed "
                    f"({getattr(item, 'id', 'unknown')})"
                )
            return (
                "manuscript is unfrozen and a prior paper-wide review task "
                f"already completed ({getattr(item, 'id', 'unknown')})"
            )
    return ""


__all__ = [
    "is_paper_wide_review_task",
    "completed_review_predates_freeze",
    "method_freeze_timestamp",
    "paper_review_purchase_defer_reason",
]
