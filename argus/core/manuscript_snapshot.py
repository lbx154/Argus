"""Identity and freshness facts for manuscript judgments.

A judgment records the manuscript's stat facts (``core.file_facts``) when it
was made; it is current while the manuscript still has exactly those facts.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .file_facts import file_facts

MANUSCRIPT_PATH = Path("paper/main.tex")
MANUSCRIPT_SNAPSHOT_FIELD = "manuscript_snapshot"


def manuscript_facts(project_root: Path | str) -> list[int]:
    """The manuscript's ``file_facts``, or ``[]`` when it is absent."""
    path = Path(project_root) / MANUSCRIPT_PATH
    return file_facts(path) if path.is_file() else []


def manuscript_snapshot(
    project_root: Path | str,
    *,
    recorded_at: str | None = None,
    facts: list[int] | None = None,
) -> dict[str, Any]:
    """Return the stable file shape embedded by review-producing processes."""
    return {
        "path": MANUSCRIPT_PATH.as_posix(),
        "facts": manuscript_facts(project_root) if facts is None else list(facts),
        "recorded_at": recorded_at or datetime.now(UTC).isoformat(),
    }


def bind_manuscript_snapshot(
    payload: dict[str, Any],
    project_root: Path | str,
    *,
    recorded_at: str | None = None,
    facts: list[int] | None = None,
) -> dict[str, Any]:
    payload[MANUSCRIPT_SNAPSHOT_FIELD] = manuscript_snapshot(
        project_root,
        recorded_at=recorded_at,
        facts=facts,
    )
    return payload


def recorded_manuscript_snapshot(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """The binding a judgment recorded, or ``None`` when it recorded no manuscript.

    A binding from before stat facts held a content digest instead: it names a
    version that can no longer be checked, so its ``facts`` are ``None`` and it
    never reads as current.
    """
    raw = payload.get(MANUSCRIPT_SNAPSHOT_FIELD)
    if not isinstance(raw, Mapping) or not raw or raw.get("facts") == []:
        return None
    facts = raw.get("facts")
    return {
        "path": str(raw.get("path") or MANUSCRIPT_PATH.as_posix()),
        "facts": list(facts) if isinstance(facts, list) else None,
        "recorded_at": str(
            raw.get("recorded_at")
            or payload.get("created_at")
            or payload.get("certified_at")
            or payload.get("recorded_at")
            or "unknown time"
        ),
    }


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
    current = manuscript_facts(project_root)
    if recorded is None:
        return {
            "status": "unbound",
            "current_facts": current,
            "message": "unbound (review did not record the manuscript version)",
        }
    if recorded["facts"] != current:
        return {
            "status": "stale",
            "recorded_facts": recorded["facts"],
            "current_facts": current,
            "reviewed_at": recorded["recorded_at"],
            "message": stale_review_message(
                reviewed_at=recorded["recorded_at"],
            ),
        }
    return {
        "status": "current",
        "recorded_facts": recorded["facts"],
        "current_facts": current,
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
    "manuscript_facts",
    "manuscript_review_status",
    "manuscript_review_artifact_statuses",
    "manuscript_snapshot",
    "recorded_manuscript_snapshot",
    "stale_review_message",
]
