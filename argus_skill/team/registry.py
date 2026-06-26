"""Campaign registry: tiny markers the lead drops so the daemon-resident Curator
can discover active team campaigns.

When the lead forms a team it writes ``<project_root>/.argus/team/<team_id>.json``
``{team_id, team_root, cwd, created_ts}``. ONE Curator per daemon watches this
directory and manages the pool for every active root — which is what makes a
duplicate coordinator (the "2 coordinators on one root" leak) structurally
impossible: discovery is centralised, not per-lead-mission.

``.argus/`` is the existing per-project overlay convention (cf. the harness
overlay), so this sits beside it without inventing a new location.
"""
from __future__ import annotations

import contextlib
import re
from pathlib import Path
from typing import Any

from . import _store

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _safe(team_id: str) -> str:
    return _SAFE_RE.sub("_", str(team_id))


def marker_dir(project_root: Path) -> Path:
    return Path(project_root) / ".argus" / "team"


def marker_path(project_root: Path, team_id: str) -> Path:
    return marker_dir(project_root) / (_safe(team_id) + ".json")


def write_marker(project_root: Path, *, team_id: str, team_root: Path | str,
                 cwd: Path | str, now: float) -> Path:
    """Atomically write a campaign marker; returns its path."""
    path = marker_path(project_root, team_id)
    _store.atomic_write_json(path, {
        "team_id": str(team_id),
        "team_root": str(team_root),
        "cwd": str(cwd),
        "created_ts": float(now),
    })
    return path


def read_marker(project_root: Path, team_id: str) -> dict[str, Any] | None:
    doc = _store.read_json(marker_path(project_root, team_id), default=None)
    return doc if isinstance(doc, dict) else None


def list_markers(project_root: Path) -> list[dict[str, Any]]:
    """Every active campaign marker (corrupt/temp files skipped)."""
    d = marker_dir(project_root)
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.json")):
        if p.name.startswith("."):  # atomic-write temp siblings (.tmp-*.json)
            continue
        doc = _store.read_json(p, default=None)
        if isinstance(doc, dict):
            out.append(doc)
    return out


def remove_marker(project_root: Path, team_id: str) -> None:
    """Delete a campaign marker; idempotent (missing → no-op)."""
    with contextlib.suppress(FileNotFoundError):
        marker_path(project_root, team_id).unlink()
