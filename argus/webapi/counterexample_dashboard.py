"""Bounded project projection for the live counterexample research dashboard."""

from __future__ import annotations

import csv
import json
import os
import re
import time
from itertools import islice
from pathlib import Path
from typing import Any

from .artifacts import safe_artifact_path

_MAX_FILE_BYTES = 4 * 1024 * 1024
_MAX_CANDIDATES = 500
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def _csv_rows(workspace: Path, relative_path: str) -> list[dict[str, str]]:
    entry = safe_artifact_path(workspace, relative_path)
    if entry is None:
        return []
    _, path = entry
    try:
        if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            return []
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return list(islice(csv.DictReader(handle), _MAX_CANDIDATES))
    except (OSError, csv.Error, UnicodeError):
        return []


def _latest_claim_status(workspace: Path) -> dict[str, str]:
    entry = safe_artifact_path(workspace, "research/MATH_STATE.json")
    if entry is None:
        return {}
    _, path = entry
    try:
        if not path.is_file() or path.stat().st_size > _MAX_FILE_BYTES:
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    latest: dict[str, tuple[int, str]] = {}
    claims = payload.get("claims", []) if isinstance(payload, dict) else []
    if not isinstance(claims, list):
        return {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id.startswith("C_"):
            continue
        item_id = claim_id[2:]
        if _SAFE_ID.fullmatch(item_id) is None:
            continue
        try:
            version = int(claim.get("version") or 0)
        except (TypeError, ValueError):
            version = 0
        statement = str(claim.get("natural_statement") or "")
        if version >= latest.get(item_id, (-1, ""))[0]:
            latest[item_id] = (version, statement)
    statuses: dict[str, str] = {}
    for item_id, (_version, statement) in latest.items():
        lowered = statement.lower()
        if "accepted_row_verification" in lowered:
            statuses[item_id] = "source-review"
        elif "further_source_retrieval" in lowered:
            statuses[item_id] = "source-review"
        elif "rejected_near_miss" in lowered:
            statuses[item_id] = "rejected"
        else:
            statuses[item_id] = "scoped"
    return statuses


def _parallel_state(workspace: Path, item_id: str) -> tuple[int, float]:
    root = workspace / "parallel" / item_id
    if root.is_symlink() or not root.is_dir() or not root.resolve().is_relative_to(workspace):
        return 0, 0.0
    count = 0
    scanned = 0
    updated_at = 0.0
    pending = [root]
    try:
        while pending and scanned < 200:
            with os.scandir(pending.pop()) as entries:
                for child in entries:
                    scanned += 1
                    if child.is_dir(follow_symlinks=False):
                        pending.append(Path(child.path))
                    elif child.is_file(follow_symlinks=False):
                        count += 1
                        updated_at = max(updated_at, child.stat().st_mtime)
                    if scanned >= 200:
                        break
    except OSError:
        pass
    return count, updated_at


def _progress(status: str) -> int:
    return {
        "queued": 8,
        "scoped": 22,
        "source-review": 45,
        "constructing": 62,
        "evidence": 78,
        "reviewing": 88,
        "verified": 100,
        "rejected": 100,
    }.get(status, 8)


def build_counterexample_dashboard(workspace: Path | str) -> dict[str, Any]:
    root = Path(workspace).expanduser().resolve()
    candidates = _csv_rows(root, "inputs/priority_pool.csv")
    accepted = {row.get("ID", ""): row for row in _csv_rows(root, "outputs/results.csv")}
    rejected = {row.get("ID", ""): row for row in _csv_rows(root, "outputs/rejected.csv")}
    claim_status = _latest_claim_status(root)
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        item_id = str(candidate.get("ID") or "").strip()
        if _SAFE_ID.fullmatch(item_id) is None:
            continue
        accepted_row = accepted.get(item_id)
        rejected_row = rejected.get(item_id)
        evidence = safe_artifact_path(root, f"evidence/{item_id}/README.md")
        evidence_exists = (
            evidence is not None
            and evidence[1].is_file()
            and not (root / "evidence" / item_id / "README.md").is_symlink()
        )
        parallel_files, parallel_updated_at = _parallel_state(root, item_id)
        status = claim_status.get(item_id, "queued")
        if parallel_files:
            status = "constructing"
        if evidence_exists:
            status = "evidence"
        if rejected_row:
            status = "rejected"
        if accepted_row:
            status = "verified"
        mtimes = [parallel_updated_at]
        for relative_path in (
            f"evidence/{item_id}/README.md",
            "outputs/results.csv" if accepted_row else "",
            "outputs/rejected.csv" if rejected_row else "",
        ):
            entry = safe_artifact_path(root, relative_path)
            if entry is None:
                continue
            _, path = entry
            try:
                if path.is_file():
                    mtimes.append(path.stat().st_mtime)
            except OSError:
                pass
        rows.append(
            {
                "id": item_id,
                "title": str(candidate.get("题目") or "").strip(),
                "description": str(candidate.get("具体描述") or "").strip(),
                "classification": str(candidate.get("分类") or "").strip(),
                "source_grade": str(candidate.get("来源等级") or "").strip(),
                "verification_level": str(candidate.get("验证级别") or "").strip(),
                "status": status,
                "progress": _progress(status),
                "disposition": str((accepted_row or {}).get("disposition") or ""),
                "result_summary": str(
                    (accepted_row or {}).get("counterexample_or_refutation") or ""
                ),
                "rejection_reason": str(
                    (rejected_row or {}).get("rejection_reason") or ""
                ),
                "evidence_path": (
                    f"evidence/{item_id}/README.md" if evidence_exists else ""
                ),
                "parallel_files": parallel_files,
                "updated_at": max(mtimes),
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["status"])
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema_version": 1,
        "generated_at": time.time(),
        "total": len(rows),
        "counts": counts,
        "candidates": rows,
    }


__all__ = ["build_counterexample_dashboard"]
