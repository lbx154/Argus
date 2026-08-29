"""Host-owned intermediate stage review certificates."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

FILENAME = "stage-certificates.json"


def certificate_path(state_root: Path | str) -> Path:
    return Path(state_root) / FILENAME


def _read(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "stages": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("stages"), dict):
        return {"schema_version": 1, "stages": {}}
    return payload


def _evidence_fingerprint(item: Any) -> str:
    refs = []
    for ref in getattr(item, "context_refs", []) or []:
        if not isinstance(ref, dict):
            continue
        refs.append({
            "kind": str(ref.get("kind") or ""),
            "ref": str(ref.get("ref") or ""),
            "content_hash": str(ref.get("content_hash") or ""),
        })
    payload = {
        "acceptance_check": str(getattr(item, "acceptance_check", "") or ""),
        "context_refs": sorted(refs, key=lambda row: (row["kind"], row["ref"])),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def record_stage_review(
    *,
    state_root: Path | str,
    project_root: Path | str,
    stage: str,
    item: Any,
    manager_action: str,
    manager_reason: str = "",
    manuscript_binding: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Record one independently reviewed stage-closing attempt."""
    normalized_stage = str(stage or "").strip().lower()
    if not normalized_stage:
        raise ValueError("stage review certificate requires a stage")
    checklist_fingerprint = ""
    try:
        from ..skills.stage_machine import completion_contract_fingerprint

        checklist_fingerprint = completion_contract_fingerprint(
            project_root,
            normalized_stage,
            version=1,
        )
    except Exception:  # noqa: BLE001 - evidence receipt remains usable
        pass
    now = time.time()
    record = {
        "stage": normalized_stage,
        "task_id": str(getattr(item, "id", "") or ""),
        "review_status": "done",
        "manager_action": str(manager_action or "hold").strip().lower(),
        "manager_reason": str(manager_reason or "")[:2000],
        "certified": str(manager_action or "").strip().lower()
        in {"advance", "complete"},
        "checklist_fingerprint": checklist_fingerprint,
        "evidence_fingerprint": _evidence_fingerprint(item),
        "recorded_at": now,
        "project_root": str(Path(project_root).resolve()),
    }
    try:
        from .manuscript_snapshot import manuscript_snapshot

        snapshot = (
            dict(manuscript_binding)
            if isinstance(manuscript_binding, dict)
            else manuscript_snapshot(project_root)
        )
        if snapshot["sha256"]:
            record["manuscript_snapshot"] = snapshot
    except Exception:  # noqa: BLE001 - non-paper certificates remain valid
        pass
    path = certificate_path(state_root)
    payload = _read(path)
    stages = dict(payload.get("stages") or {})
    stages[normalized_stage] = record
    payload = {"schema_version": 1, "stages": stages}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return record


def latest_stage_review(state_root: Path | str, stage: str) -> dict[str, Any] | None:
    return all_stage_reviews(state_root).get(str(stage or "").strip().lower())


def all_stage_reviews(state_root: Path | str) -> dict[str, dict[str, Any]]:
    """Return every stage review with current manuscript freshness applied."""
    payload = _read(certificate_path(state_root))
    reviews: dict[str, dict[str, Any]] = {}
    for stage, record in (payload.get("stages") or {}).items():
        if not isinstance(record, dict):
            continue
        result = dict(record)
        snapshot = result.get("manuscript_snapshot")
        project_root = str(result.get("project_root") or "").strip()
        if isinstance(snapshot, dict) and snapshot.get("sha256") and project_root:
            try:
                from .manuscript_snapshot import manuscript_review_status

                freshness = manuscript_review_status(result, project_root)
            except Exception:  # noqa: BLE001 - an unreadable binding never certifies
                freshness = {
                    "status": "unbound",
                    "message": "unbound manuscript review",
                }
            if freshness.get("status") != "current":
                result["certified"] = False
                result["review_status"] = "stale"
                result["freshness_status"] = freshness.get("status")
                result["stale_reason"] = freshness.get("message")
        reviews[str(stage)] = result
    return reviews


__all__ = [
    "FILENAME",
    "all_stage_reviews",
    "certificate_path",
    "latest_stage_review",
    "record_stage_review",
]
