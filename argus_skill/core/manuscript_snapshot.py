"""Content identity and freshness facts for manuscript judgments."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

MANUSCRIPT_PATH = Path("paper/main.tex")
MANUSCRIPT_SNAPSHOT_FIELD = "manuscript_snapshot"


def manuscript_sha256(project_root: Path | str) -> str:
    """SHA-256 of the exact manuscript bytes, or ``""`` when absent."""
    path = Path(project_root) / MANUSCRIPT_PATH
    if not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manuscript_snapshot(
    project_root: Path | str,
    *,
    recorded_at: str | None = None,
    sha256: str | None = None,
) -> dict[str, str]:
    """Return the stable file shape embedded by review-producing processes."""
    return {
        "path": MANUSCRIPT_PATH.as_posix(),
        "sha256": manuscript_sha256(project_root) if sha256 is None else str(sha256),
        "recorded_at": recorded_at or datetime.now(UTC).isoformat(),
    }


def bind_manuscript_snapshot(
    payload: dict[str, Any],
    project_root: Path | str,
    *,
    recorded_at: str | None = None,
    sha256: str | None = None,
) -> dict[str, Any]:
    payload[MANUSCRIPT_SNAPSHOT_FIELD] = manuscript_snapshot(
        project_root,
        recorded_at=recorded_at,
        sha256=sha256,
    )
    return payload


def recorded_manuscript_snapshot(payload: Mapping[str, Any]) -> dict[str, str] | None:
    raw = payload.get(MANUSCRIPT_SNAPSHOT_FIELD)
    if isinstance(raw, Mapping):
        digest = str(raw.get("sha256") or "").strip().lower()
        if digest:
            return {
                "path": str(raw.get("path") or MANUSCRIPT_PATH.as_posix()),
                "sha256": digest,
                "recorded_at": str(
                    raw.get("recorded_at")
                    or payload.get("created_at")
                    or payload.get("certified_at")
                    or payload.get("recorded_at")
                    or "unknown time"
                ),
            }

    # Read compatibility for reviews written before the top-level binding was
    # introduced. The main source snapshot is already the exact manuscript.
    snapshots = payload.get("source_snapshots")
    if isinstance(snapshots, list):
        for item in snapshots:
            if not isinstance(item, Mapping):
                continue
            if Path(str(item.get("path") or "")).as_posix() != MANUSCRIPT_PATH.as_posix():
                continue
            digest = str(item.get("sha256") or "").strip().lower()
            if digest:
                return {
                    "path": MANUSCRIPT_PATH.as_posix(),
                    "sha256": digest,
                    "recorded_at": str(
                        payload.get("created_at")
                        or payload.get("certified_at")
                        or payload.get("recorded_at")
                        or "unknown time"
                    ),
                }
    return None


def stale_review_message(
    *,
    reviewed_at: str,
) -> str:
    return (
        f"stale: reviewed an earlier manuscript version (at {reviewed_at}); "
        "the manuscript has changed since"
    )


def manuscript_review_status(
    payload: Mapping[str, Any],
    project_root: Path | str,
) -> dict[str, Any]:
    """Mechanically compare one recorded judgment with the current manuscript."""
    recorded = recorded_manuscript_snapshot(payload)
    current = manuscript_sha256(project_root)
    if recorded is None:
        return {
            "status": "unbound",
            "current_sha256": current,
            "message": "unbound (review did not record the manuscript version)",
        }
    if recorded["sha256"] != current:
        return {
            "status": "stale",
            "recorded_sha256": recorded["sha256"],
            "current_sha256": current,
            "reviewed_at": recorded["recorded_at"],
            "message": stale_review_message(
                reviewed_at=recorded["recorded_at"],
            ),
        }
    return {
        "status": "current",
        "recorded_sha256": recorded["sha256"],
        "current_sha256": current,
        "reviewed_at": recorded["recorded_at"],
        "message": "current",
    }


def manuscript_review_artifact_statuses(
    project_root: Path | str,
) -> tuple[dict[str, str], ...]:
    """Return freshness facts for persisted manuscript-bound JSON judgments."""
    root = Path(project_root).resolve()
    facts: list[dict[str, str]] = []
    for directory_name in ("paper", "analysis"):
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or recorded_manuscript_snapshot(payload) is None:
                continue
            status = manuscript_review_status(payload, root)
            facts.append({
                "path": path.relative_to(root).as_posix(),
                "status": str(status["status"]),
                "message": str(status["message"]),
            })
    return tuple(facts)


__all__ = [
    "MANUSCRIPT_PATH",
    "MANUSCRIPT_SNAPSHOT_FIELD",
    "bind_manuscript_snapshot",
    "manuscript_review_status",
    "manuscript_review_artifact_statuses",
    "manuscript_sha256",
    "manuscript_snapshot",
    "recorded_manuscript_snapshot",
    "stale_review_message",
]
